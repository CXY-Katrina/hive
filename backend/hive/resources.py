from datetime import timedelta
from itertools import combinations, product, islice
import hashlib
from .domain import DomainError, SYSTEM, uid, now, encode, decode
from .inventory import device_status, model_label
from .schemas import ResourceSpec


TERMINAL = {"RELEASED", "CANCELLED", "FAILED"}


class ResourceService:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    def create(self, actor, spec, idempotency_key, purpose="debug", cursor=None):
        spec = ResourceSpec.model_validate(spec).model_dump()
        spec["purpose"] = purpose
        body_hash = hashlib.sha256(encode(spec).encode()).hexdigest()
        if cursor is None:
            with self.db.transaction() as c:
                return self.create(actor, spec, idempotency_key, purpose, c)
        c = cursor
        if not actor.admin:
            c.execute('SELECT can_request FROM users WHERE id=%s AND deleted_at IS NULL FOR UPDATE', (actor.id,))
            member = c.fetchone()
            if not member or not member['can_request']:
                raise DomainError('尚未获得服务器申请权限，请联系管理员开启', 403)
        stamp, request_id = now(), uid()
        wait = spec.get("wait_minutes")
        deadline = stamp + timedelta(minutes=wait) if wait else None
        # Unique(user,key) makes concurrent retries return one immutable request.
        c.execute("""INSERT INTO resource_requests
                  (id,owner_user_id,owner_name,idempotency_key,body_hash,spec,purpose,status,created_at,deadline)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,'QUEUED',%s,%s)
                  ON DUPLICATE KEY UPDATE id=id""",
                  (request_id,actor.id,actor.username,idempotency_key,body_hash,encode(spec),purpose,stamp,deadline))
        c.execute("SELECT * FROM resource_requests WHERE owner_user_id=%s AND idempotency_key=%s",
                  (actor.id,idempotency_key))
        row = c.fetchone()
        if row["body_hash"] != body_hash:
            raise DomainError("幂等键已用于不同的申请内容")
        if row["id"] == request_id:
            self.db.audit(c, actor, "request.create", request_id, {"spec": spec})
        row["spec"] = decode(row["spec"])
        return row

    def list(self):
        terminal = tuple(sorted(TERMINAL))
        rows = self.db.all("""SELECT * FROM resource_requests WHERE status NOT IN (%s,%s,%s)
                          UNION ALL
                          (SELECT * FROM resource_requests WHERE status IN (%s,%s,%s)
                           ORDER BY created_at DESC,id DESC LIMIT 500)
                          ORDER BY created_at DESC,id DESC""", terminal + terminal)
        by_id = {r["id"]: r for r in rows}
        for r in rows:
            r["spec"] = decode(r["spec"])
            r["devices"] = []
        # History can include hundreds of requests; avoid one MySQL connection
        # per row while keeping every active allocation visible.
        ids = list(by_id)
        for offset in range(0, len(ids), 500):
            batch = ids[offset:offset + 500]
            placeholders = ",".join(["%s"] * len(batch))
            devices = self.db.all("""SELECT d.*,n.host,n.port,n.generation,n.adapter,n.maintenance,n.config_version,
                              a.epoch,a.released_at,a.request_id AS allocation_request_id
                              FROM allocation_devices a JOIN devices d ON d.id=a.device_id JOIN nodes n ON n.id=d.node_id
                              WHERE a.request_id IN (""" + placeholders + ") ORDER BY n.id,d.slot", tuple(batch))
            for device in devices:
                by_id[device.pop("allocation_request_id")]["devices"].append(device)
        return rows

    def get(self, request_id):
        row = self.db.one("SELECT * FROM resource_requests WHERE id=%s", (request_id,))
        if not row:
            raise DomainError("申请不存在", 404)
        row["spec"] = decode(row["spec"])
        return row

    def devices(self, request_id, include_released=False):
        if include_released:
            return self.db.all("""SELECT d.*,n.host,n.port,n.generation,n.adapter,n.maintenance,n.config_version,a.epoch,a.released_at
                            FROM allocation_devices a JOIN devices d ON d.id=a.device_id JOIN nodes n ON n.id=d.node_id
                            WHERE a.request_id=%s ORDER BY n.id,d.slot""", (request_id,))
        return self.db.all("""SELECT d.*,n.host,n.port,n.generation,n.adapter,n.maintenance,n.config_version,o.epoch,o.request_id
                            FROM device_ownership o JOIN devices d ON d.id=o.device_id JOIN nodes n ON n.id=d.node_id
                            WHERE o.request_id=%s ORDER BY n.id,d.slot""", (request_id,))

    def release(self, request_id, actor):
        with self.db.transaction() as c:
            c.execute("SELECT * FROM resource_requests WHERE id=%s FOR UPDATE", (request_id,))
            row = c.fetchone()
            if not row:
                raise DomainError("申请不存在", 404)
            if not actor.admin and row["owner_user_id"] != actor.id:
                raise DomainError("只能归还自己的申请", 403)
            if row["status"] in TERMINAL or row["status"] == "RELEASING":
                return row
            target = "CANCELLED" if row["status"] == "QUEUED" else "RELEASING"
            cause = "idle" if actor.id == "system" and row["purpose"] == "debug" else "task" if row["purpose"] == "task" else "manual"
            c.execute("UPDATE resource_requests SET status=%s,release_started_at=%s,reason=%s,release_cause=%s WHERE id=%s",
                      (target,now(),"用户归还" if actor.id != "system" else "自动清理",cause,request_id))
            self.db.audit(c,actor,"request.release",request_id,{"from":row["status"],"to":target})
        return self.get(request_id)

    def finish_release(self, request_id, epoch):
        # Lock ordering agrees with reservation: nodes -> request -> ownership.
        with self.db.transaction() as c:
            c.execute("SELECT id FROM nodes ORDER BY id FOR UPDATE")
            c.fetchall()
            c.execute("SELECT * FROM resource_requests WHERE id=%s FOR UPDATE", (request_id,))
            row = c.fetchone()
            if not row or row["version"] != epoch or row["status"] != "RELEASING":
                return False
            stamp = now()
            c.execute("DELETE FROM device_ownership WHERE request_id=%s AND epoch=%s", (request_id,epoch))
            c.execute("UPDATE allocation_devices SET released_at=%s WHERE request_id=%s AND epoch=%s AND released_at IS NULL", (stamp,request_id,epoch))
            c.execute("UPDATE resource_requests SET status='RELEASED',released_at=%s,reason='清理及空闲复核通过' WHERE id=%s", (stamp,request_id))
            self.db.audit(c,SYSTEM,"request.released",request_id,{"epoch":epoch})
        return True

    def cancel_idle_release(self, request_id, epoch):
        with self.db.transaction() as c:
            c.execute("""UPDATE resource_requests SET status='ACTIVE',release_started_at=NULL,release_cause=NULL,
                      reason='清理前复验不再满足空闲条件，继续保留'
                      WHERE id=%s AND version=%s AND status='RELEASING' AND release_cause='idle'
                      AND cleanup_started_at IS NULL""",(request_id,epoch))
            changed=bool(c.rowcount)
            if changed:
                self.db.audit(c,SYSTEM,"request.idle_release_cancelled",request_id,{"epoch":epoch})
            return changed

    def begin_cleanup(self, request_id, epoch):
        with self.db.transaction() as c:
            c.execute("SELECT status,version,cleanup_started_at FROM resource_requests WHERE id=%s FOR UPDATE",(request_id,))
            row=c.fetchone()
            if not row or row["status"]!="RELEASING" or row["version"]!=epoch:
                return False
            if row["cleanup_started_at"] is None:
                c.execute("UPDATE resource_requests SET cleanup_started_at=%s WHERE id=%s",(now(),request_id))
                self.db.audit(c,SYSTEM,"request.cleanup_started",request_id,{"epoch":epoch})
            return True

    def reserve(self, request_id):
        with self.db.transaction() as c:
            c.execute("SELECT * FROM nodes ORDER BY id FOR UPDATE")
            nodes = c.fetchall()
            c.execute("SELECT * FROM resource_requests WHERE id=%s FOR UPDATE", (request_id,))
            req = c.fetchone()
            if not req or req["status"] != "QUEUED":
                return None
            stamp, spec = now(), decode(req["spec"])
            if req["deadline"] and stamp >= req["deadline"]:
                c.execute("UPDATE resource_requests SET status='FAILED',reason='排队期限已到' WHERE id=%s", (request_id,))
                self.db.audit(c,SYSTEM,"request.expired",request_id)
                return None
            c.execute("""SELECT d.*,o.request_id,n.maintenance,n.adapter FROM devices d JOIN nodes n ON n.id=d.node_id
                         LEFT JOIN device_ownership o ON o.device_id=d.id ORDER BY d.node_id,d.slot""")
            devices = c.fetchall()
            candidates = []
            for n in nodes:
                if spec.get("target_node_ids") and n["id"] not in spec["target_node_ids"]:
                    continue
                if (n["maintenance"] or n["generation"] != spec["generation"] or n["vendor"] != spec["vendor"]
                    or n["device_kind"] != spec["device_kind"]
                    or spec.get("model") and spec["model"] not in (n["model"], model_label(n))):
                    continue
                if spec.get("shared_storage_id") and not any(
                    (m.get("shared_storage_id") or m.get("candidate_id")) == spec["shared_storage_id"]
                    and m.get("readable") and m.get("writable") for m in decode(n.get("mounts"),[])):
                    continue
                all_cards = [d for d in devices if d["node_id"] == n["id"]]
                free = [d for d in all_cards if device_status(d,stamp,self.settings.stale_seconds) == "available"
                        and d["memory_total"] >= spec.get("min_memory_gib",0) * 1024**3]
                count = len(all_cards) if spec["mode"] == "whole" else spec["cards_per_node"]
                if count and len(free) >= count and (spec["mode"] != "whole" or len(free) == len(all_cards)):
                    if spec.get("require_interconnect"):
                        candidates.append(list(islice(combinations(free,count),64)))
                    else:
                        candidates.append([free[:count]])
            count = spec["machine_count"]
            selected = []
            if len(candidates) >= count:
                bad_pairs=set()
                if spec.get("require_interconnect"):
                    c.execute("SELECT source_device,target_device FROM connectivity_checks WHERE status NOT IN ('ok','passed') AND checked_at>%s",(stamp-timedelta(minutes=5),))
                    bad_pairs={(r["source_device"],r["target_device"]) for r in c.fetchall()}
                # Bounded search also tries alternative cards, not always the first failed pair.
                inspected=0
                for node_group in islice(combinations(candidates,count),256):
                    for choice in product(*node_group):
                        proposed=[d for group in choice for d in group]
                        inspected+=1
                        if not any((a["id"],b["id"]) in bad_pairs for a in proposed for b in proposed if a["node_id"]!=b["node_id"]):
                            selected=proposed
                            break
                        if inspected>=256:
                            break
                    if selected or inspected>=256:
                        break
            # Probe selection occurs outside transactions; allocator never assumes connectivity transitively.
            if not selected:
                status = "QUEUED" if spec.get("queue", True) else "FAILED"
                c.execute("UPDATE resource_requests SET status=%s,reason='暂无满足规格的空闲设备' WHERE id=%s",(status,request_id))
                return None
            epoch = req["version"] + 1
            for d in selected:
                c.execute("INSERT INTO device_ownership VALUES (%s,%s,%s)", (d["id"],request_id,epoch))
                c.execute("INSERT INTO allocation_devices (request_id,device_id,node_id,epoch,locked_at) VALUES (%s,%s,%s,%s,%s)",
                          (request_id,d["id"],d["node_id"],epoch,stamp))
            c.execute("UPDATE resource_requests SET status='RESERVED',version=%s,reserved_at=%s,reason=NULL WHERE id=%s",
                      (epoch,stamp,request_id))
            self.db.audit(c,SYSTEM,"request.reserved",request_id,{"epoch":epoch,"devices":[d["id"] for d in selected]})
        return self.get(request_id)

    def expire_queued(self):
        with self.db.transaction() as c:
            c.execute("SELECT id FROM resource_requests WHERE status='QUEUED' AND deadline<=%s FOR UPDATE",(now(),))
            ids=[r["id"] for r in c.fetchall()]
            for ident in ids:
                c.execute("UPDATE resource_requests SET status='FAILED',reason='排队期限已到' WHERE id=%s",(ident,))
                self.db.audit(c,SYSTEM,"request.expired",ident)

    def deliver(self, request_id, epoch,expected_devices=None):
        with self.db.transaction() as c:
            c.execute("SELECT * FROM nodes ORDER BY id FOR UPDATE")
            nodes={n["id"]:n for n in c.fetchall()}
            c.execute("SELECT * FROM resource_requests WHERE id=%s FOR UPDATE",(request_id,))
            req=c.fetchone()
            if not req or req["status"]!="RESERVED" or req["version"]!=epoch:
                return False
            c.execute("""SELECT d.*,o.epoch,n.maintenance,n.config_version,n.adapter FROM device_ownership o JOIN devices d ON d.id=o.device_id
                       JOIN nodes n ON n.id=d.node_id WHERE o.request_id=%s""",(request_id,))
            devices=c.fetchall()
            if expected_devices is not None:
                fields=("boot_id","command_id","chip_id","logical_id","config_version")
                current={d["id"]:tuple(d.get(k) for k in fields) for d in devices}
                if current!=expected_devices:
                    raise DomainError("探测后节点重启、卡映射或配置改变，须重新复验")
            c.execute("SELECT COUNT(*) n FROM allocation_devices WHERE request_id=%s AND epoch=%s AND released_at IS NULL",(request_id,epoch))
            expected=c.fetchone()["n"]
            if not expected or len(devices)!=expected or any(d["epoch"]!=epoch or device_status(d,stale_seconds=self.settings.stale_seconds)!="available" for d in devices):
                raise DomainError("交付事务复核发现设备已占用、缺测或归属改变")
            spec=decode(req["spec"])
            selected_nodes={d["node_id"] for d in devices}
            if len(selected_nodes)!=spec["machine_count"]:
                raise DomainError("交付节点数量与申请不一致")
            for ident in selected_nodes:
                assigned=sum(d["node_id"]==ident for d in devices)
                if spec["mode"]=="whole":
                    c.execute("SELECT COUNT(*) n FROM devices WHERE node_id=%s",(ident,))
                    if assigned!=c.fetchone()["n"]:
                        raise DomainError("整机设备清单在交付前改变")
                elif assigned!=spec["cards_per_node"]:
                    raise DomainError("交付卡数与申请不一致")
            stamp = now()
            c.execute("""UPDATE resource_requests SET status='ACTIVE',delivered_at=%s,
                         protected_until=CASE WHEN purpose='debug' THEN %s ELSE NULL END,reason=NULL
                         WHERE id=%s AND version=%s AND status='RESERVED'""",
                      (stamp,stamp+timedelta(minutes=30),request_id,epoch))
            if c.rowcount:
                self.db.audit(c,SYSTEM,"request.delivered",request_id,{"epoch":epoch})
                return True
        return False

    def abort_reservation(self, request_id, epoch, reason):
        with self.db.transaction() as c:
            c.execute("SELECT id FROM nodes ORDER BY id FOR UPDATE")
            c.fetchall()
            c.execute("SELECT * FROM resource_requests WHERE id=%s FOR UPDATE",(request_id,))
            req=c.fetchone()
            if not req or req["status"] != "RESERVED" or req["version"] != epoch:
                return
            # Only read-only admission probes run before ACTIVE, so no task can be launched in this state.
            c.execute("DELETE FROM device_ownership WHERE request_id=%s AND epoch=%s",(request_id,epoch))
            c.execute("UPDATE allocation_devices SET released_at=%s WHERE request_id=%s AND epoch=%s",(now(),request_id,epoch))
            status="QUEUED" if decode(req["spec"]).get("queue",True) else "FAILED"
            c.execute("UPDATE resource_requests SET status=%s,reason=%s WHERE id=%s",(status,reason,request_id))
            self.db.audit(c,SYSTEM,"request.preflight_failed",request_id,{"reason":reason,"epoch":epoch})

    def note(self, request_id, reason):
        with self.db.transaction() as c:
            c.execute("UPDATE resource_requests SET reason=%s WHERE id=%s",(reason,request_id))
