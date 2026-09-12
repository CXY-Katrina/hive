from dataclasses import asdict
from datetime import timedelta
import threading
from .domain import DomainError, encode, decode, now, uid


class Telemetry:
    def __init__(self, db, inventory, adapters, catalog, settings):
        self.db, self.inventory, self.adapters = db, inventory, adapters
        self.catalog, self.settings = catalog, settings
        self._locks = {}
        self._guard = threading.Lock()
        self.started_at = now()

    def collect_node(self, node_id):
        with self._guard:
            lock = self._locks.setdefault(node_id, threading.Lock())
        with lock:
            node = self.inventory.connection(node_id)
            self.inventory.record_probe(node_id, metadata={'collection': {'status': 'running', 'started_at': str(now())}})
            try:
                adapter = self.adapters[node["adapter"]]
                snapshot = adapter.collect(node)
                self.ingest(node_id, snapshot)
                return snapshot
            except Exception as exc:
                # DomainError contains curated application messages; raw library errors may contain secrets.
                reason = str(exc) if isinstance(exc, DomainError) else '采集失败（' + type(exc).__name__ + '），请查看服务日志'
                with self.db.transaction() as c:
                    c.execute("UPDATE nodes SET status='unknown',reason=%s WHERE id=%s AND deleted_at IS NULL", (reason, node_id))
                    c.execute("UPDATE devices SET quality='unknown',reason=%s WHERE node_id=%s", (reason, node_id))
                self.inventory.record_probe(node_id, metadata={'collection': {'status': 'failed', 'finished_at': str(now())}})
                raise

    def ingest(self, node_id, snapshot):
        if not snapshot.boot_id or not snapshot.devices:
            raise DomainError(snapshot.reason or "未取得完整设备清单/boot_id，请检查 npu-smi 和 Ascend 驱动")
        stamp = snapshot.sampled_at
        slots = [d.slot for d in snapshot.devices]
        if len(set(slots)) != len(slots):
            raise DomainError("设备 ID 重复")
        for d in snapshot.devices:
            if d.ai_core is None or d.memory_used is None or not d.process_complete:
                d.quality,d.reason="unknown",d.reason or "缺少必要的指标或完整进程清单"
            for k in ("ai_core", "memory_used", "memory_total"):
                self.catalog.validate(k, getattr(d, k))
            if d.memory_used is not None and d.memory_used > d.memory_total:
                d.quality, d.reason = "unknown", "显存读数超过总容量"
            for key, value in d.extensions.items():
                self.catalog.validate(key, value)
        with self.db.transaction() as c:
            c.execute("SELECT id,boot_id,sampled_at,deleted_at FROM nodes WHERE id=%s FOR UPDATE", (node_id,))
            node = c.fetchone()
            if not node:
                raise DomainError("节点不存在", 404)
            if node['deleted_at'] is not None:
                return  # A sample already in flight cannot revive a removed node.
            if node["sampled_at"] and stamp <= node["sampled_at"]:
                return  # Delayed samples must not replace fresh state.
            reboot = node["boot_id"] != snapshot.boot_id
            c.execute("SELECT * FROM devices WHERE node_id=%s", (node_id,))
            existing = {r["slot"]: r for r in c.fetchall()}
            seen = set()
            for d in snapshot.devices:
                old = existing.get(d.slot)
                device_id = old["id"] if old else uid()
                seen.add(device_id)
                processes = [asdict(p) if not isinstance(p, dict) else p for p in d.processes]
                if (d.process_complete and d.quality == "ok" and snapshot.quality == "ok"
                    and (not old or decode(old.get("processes"),[]) != processes or reboot)):
                    c.execute("INSERT INTO process_events (device_id,sampled_at,boot_id,processes) VALUES (%s,%s,%s,%s)",
                              (device_id,stamp,snapshot.boot_id,encode(processes)))
                # Hardware processes may carry slots. Normalize to stable inventory IDs after full enumeration below.
                if old and (old["command_id"], old["chip_id"], old["logical_id"]) != (d.command_id, d.chip_id, d.logical_id):
                    c.execute("SELECT device_id FROM device_ownership WHERE device_id=%s", (device_id,))
                    if c.fetchone():
                        d.quality, d.reason = "unknown", "活动申请期间设备映射改变"
                        # Keep the allocated mapping until explicit reconciliation; otherwise
                        # the next poll could silently accept the changed IDs as healthy.
                        d.command_id,d.chip_id,d.logical_id=old["command_id"],old["chip_id"],old["logical_id"]
                c.execute("""INSERT INTO devices
                     (id,node_id,slot,command_id,chip_id,logical_id,memory_total,memory_used,ai_core,health,quality,reason,
                      process_complete,processes,extensions,sampled_at,boot_id)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                     ON DUPLICATE KEY UPDATE command_id=VALUES(command_id),chip_id=VALUES(chip_id),logical_id=VALUES(logical_id),
                     memory_total=VALUES(memory_total),memory_used=VALUES(memory_used),ai_core=VALUES(ai_core),health=VALUES(health),
                     quality=VALUES(quality),reason=VALUES(reason),process_complete=VALUES(process_complete),processes=VALUES(processes),
                     extensions=VALUES(extensions),sampled_at=VALUES(sampled_at),boot_id=VALUES(boot_id)""",
                     (device_id,node_id,d.slot,d.command_id,d.chip_id,d.logical_id,d.memory_total,d.memory_used,d.ai_core,
                      d.health,d.quality if snapshot.quality == "ok" else snapshot.quality,d.reason,
                      d.process_complete,encode(processes),encode(d.extensions),stamp,snapshot.boot_id))
                if reboot:
                    c.execute("UPDATE devices SET baseline_bytes=0,baseline_confirmed=FALSE WHERE id=%s", (device_id,))
                c.execute("""INSERT INTO device_samples
                    (device_id,sampled_at,boot_id,ai_core,memory_used,memory_total,quality,extensions)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (device_id,stamp,snapshot.boot_id,d.ai_core,d.memory_used,d.memory_total,
                     d.quality if snapshot.quality == "ok" else snapshot.quality,encode(d.extensions)))
            for missing in existing.values():
                if missing["id"] not in seen:
                    c.execute("UPDATE devices SET quality='unknown',health='unknown',reason='设备未出现在枚举结果' WHERE id=%s", (missing["id"],))
            snapshot.metadata['collection'] = {'status': 'ready' if snapshot.quality == 'ok' else 'failed', 'finished_at': str(now())}
            c.execute("UPDATE nodes SET status=%s,reason=%s,boot_id=%s,sampled_at=%s,metadata=JSON_MERGE_PATCH(COALESCE(metadata,JSON_OBJECT()),CAST(%s AS JSON)) WHERE id=%s",
                      (snapshot.quality,snapshot.reason,snapshot.boot_id,stamp,encode(snapshot.metadata),node_id))
            if reboot or set(existing) != set(slots):
                c.execute("UPDATE nodes SET probe_requested=TRUE WHERE id=%s",(node_id,))

    def history(self, device_id, minutes=10):
        rows = self.db.all("""SELECT sampled_at,boot_id,ai_core,memory_used,memory_total,quality,extensions
                            FROM device_samples WHERE device_id=%s AND sampled_at>=%s ORDER BY sampled_at""",
                           (device_id, now()-timedelta(minutes=minutes, seconds=self.settings.sample_seconds * 2)))
        for row in rows:
            row['extensions'] = decode(row['extensions'], {})
            row['sample_interval_seconds'] = self.settings.sample_seconds
        return rows

    def window(self, devices):
        return {d["id"]: [r for r in self.history(d["id"]) if r["sampled_at"] >= self.started_at] for d in devices}
