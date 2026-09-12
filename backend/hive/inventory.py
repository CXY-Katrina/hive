import base64
import ipaddress
import re
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .domain import DomainError, encode, decode, now, uid


ASCEND_IDLE_MEMORY_TOLERANCE_BYTES = 2 * 1024**2


def model_label(node):
    """Display the server model without a known board product suffix."""
    profile = (decode(node.get('metadata'), {}) or {}).get('hardware_profile') or {}
    model = (node.get('model') or profile.get('system_product') or '').strip()
    server, separator, board = model.rpartition('/')
    if separator and (board.strip() == profile.get('board_product')
                      or re.fullmatch(r'IT22HMDA_[A-Za-z0-9_]+', board.strip())):
        return server.strip() or model
    return model


def idle_memory_limit(device):
    """Only an explicitly confirmed Ascend baseline permits driver jitter."""
    if not device.get("baseline_confirmed"):
        return 0
    baseline = max(0, device.get("baseline_bytes", 0))
    tolerance = ASCEND_IDLE_MEMORY_TOLERANCE_BYTES if device.get("adapter") == "ascend" else 0
    return baseline + tolerance


def device_status(device, stamp=None, stale_seconds=45):
    stamp = stamp or now()
    if device.get("maintenance"):
        return "maintenance"
    if not device.get("sampled_at") or (stamp - device["sampled_at"]).total_seconds() > stale_seconds:
        return "unknown"
    if device.get("quality") != "ok" or device.get("health") != "OK" or not device.get("process_complete"):
        return "unknown" if device.get("health") != "FAULT" else "fault"
    if device.get("ai_core") is None or device.get("memory_used") is None:
        return "unknown"
    if device.get("request_id"):
        return "allocated"
    busy = bool(decode(device.get("processes"), [])) or (device.get("ai_core") or 0) > 0
    used = device.get("memory_used")
    if used is None:
        return "unknown"
    if used > idle_memory_limit(device):
        busy = True
    return "external" if busy else "available"


class Inventory:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        self.supported_adapters = {"ascend"}

    def _cipher(self):
        try:
            key = base64.urlsafe_b64decode(self.settings.secret_key)
            if len(key) != 32:
                raise ValueError()
            return AESGCM(key)
        except Exception as exc:
            raise DomainError("请配置有效 HIVE_SECRET_KEY（hive keygen）", 503) from exc

    def encrypt(self, node_id, password):
        nonce = os.urandom(12)
        value = nonce + self._cipher().encrypt(nonce, password.encode(), node_id.encode())
        return base64.urlsafe_b64encode(value).decode()

    def decrypt(self, node_id, ciphertext):
        raw = base64.urlsafe_b64decode(ciphertext)
        return self._cipher().decrypt(raw[:12], raw[12:], node_id.encode()).decode()

    @staticmethod
    def require_admin(actor):
        if not actor.admin:
            raise DomainError("此操作需要管理员登记身份", 403)

    def create(self, actor, payload):
        self.require_admin(actor)
        if payload.get("adapter", "ascend") not in self.supported_adapters:
            raise DomainError("硬件适配器尚未注册",422)
        node_id = uid()
        host = payload["host"].strip()
        # Host is passed as data to Paramiko, but reject shell/control ambiguity at admission.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?", host):
                raise DomainError("请输入有效 IP 或主机名", 422)
        with self.db.transaction() as c:
            c.execute("""INSERT INTO nodes
                (id,name,cluster_name,host,port,ssh_user,password_cipher,generation,model,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (node_id, payload["name"], payload.get("cluster_name", "default"), host,
                 payload.get("port", 22), payload.get("ssh_user", "root"),
                 self.encrypt(node_id, payload["password"]), payload["generation"], payload["model"], now()))
            self.db.audit(c, actor, "node.admit", node_id, {"host": host})
            c.execute("UPDATE nodes SET adapter=%s,vendor=%s,device_kind=%s WHERE id=%s",
                      (payload.get("adapter","ascend"),payload.get("vendor","ascend"),payload.get("device_kind","npu"),node_id))
        return self.get(node_id)

    def list_nodes(self):
        nodes = self.db.all("SELECT id,name,cluster_name,host,port,ssh_user,generation,model,adapter,vendor,device_kind,maintenance,status,reason,boot_id,sampled_at,metadata,mounts,probe_requested,created_at FROM nodes ORDER BY host,id")
        devices = self.db.all("""SELECT d.*,o.request_id,r.owner_name,r.protected_until,r.status AS allocation_status,
                              n.maintenance,n.adapter FROM devices d JOIN nodes n ON n.id=d.node_id
                              LEFT JOIN device_ownership o ON o.device_id=d.id
                              LEFT JOIN resource_requests r ON r.id=o.request_id ORDER BY d.node_id,d.slot""")
        by_node = {}
        for d in devices:
            d["processes"] = decode(d["processes"], [])
            d["extensions"] = decode(d["extensions"], {})
            d["status"] = device_status(d, stale_seconds=self.settings.stale_seconds)
            by_node.setdefault(d["node_id"], []).append(d)
        for node in nodes:
            node["metadata"] = decode(node["metadata"], {})
            node["model_label"] = model_label(node)
            profile = node['metadata'].get('hardware_profile')
            if profile and profile.get('boot_id') != node.get('boot_id'):
                profile['quality'] = 'unknown'
                profile['reason'] = '节点已重启，等待重新核验 SoC'
                if profile.get('host_system'):
                    profile['host_system'].update(quality='unknown', reason='节点已重启，等待重新采集 uname')
                if profile.get('ascend_dmi'):
                    profile['ascend_dmi'].update(available=False, reason='节点已重启，等待重新检查工具')
            node["mounts"] = decode(node["mounts"], [])
            node["devices"] = by_node.get(node["id"], [])
            if not node["sampled_at"] or (now() - node["sampled_at"]).total_seconds() > self.settings.stale_seconds:
                node["status"] = "unknown"
        return nodes

    def get(self, node_id):
        for node in self.list_nodes():
            if node["id"] == node_id:
                return node
        raise DomainError("节点不存在", 404)

    def connection(self, node_id):
        node = self.db.one("SELECT * FROM nodes WHERE id=%s", (node_id,))
        if not node:
            raise DomainError("节点不存在", 404)
        node["password"] = self.decrypt(node_id, node.pop("password_cipher"))
        node["metadata"] = decode(node["metadata"], {})
        node["mounts"] = decode(node["mounts"], [])
        node["devices"] = self.db.all("SELECT * FROM devices WHERE node_id=%s ORDER BY slot", (node_id,))
        for device in node["devices"]:
            device["processes"] = decode(device["processes"], [])
        return node

    def update(self, node_id, actor, payload):
        self.require_admin(actor)
        with self.db.transaction() as c:
            c.execute("SELECT id,metadata,generation,boot_id FROM nodes WHERE id=%s FOR UPDATE", (node_id,))
            current_node = c.fetchone()
            if not current_node:
                raise DomainError("节点不存在", 404)
            from .compute_benchmark import ACTIVE, ComputeBenchmark
            if ComputeBenchmark.state(current_node).get('status') in ACTIVE:
                raise DomainError('算力测试期间禁止修改节点配置或解除维护，请等待测试完成和空闲复核')
            if payload.get('compute_spec'):
                from .schemas import ComputeSpec
                spec = ComputeSpec.model_validate(payload['compute_spec']).model_dump()
                profile = (decode(current_node['metadata'], {}) or {}).get('hardware_profile', {})
                if (current_node['generation'] != 'A3' or profile.get('quality') != 'ok'
                    or not profile.get('soc_versions') or profile.get('boot_id') != current_node['boot_id']):
                    raise DomainError('请先完成 A3 节点的 SoC 检测，再登记双芯模块算力', 422)
                spec.update(basis='FP16 dense, dual-die module', source=spec['source'].strip(),
                            confirmed_soc_versions=profile['soc_versions'], updated_at=str(now()))
                c.execute("UPDATE nodes SET metadata=JSON_SET(COALESCE(metadata,JSON_OBJECT()),'$.compute_spec',CAST(%s AS JSON)) WHERE id=%s", (encode(spec), node_id))
            elif payload.get('clear_compute_spec'):
                c.execute("UPDATE nodes SET metadata=JSON_REMOVE(COALESCE(metadata,JSON_OBJECT()),'$.compute_spec') WHERE id=%s", (node_id,))
            if "maintenance" in payload:
                c.execute("UPDATE nodes SET maintenance=%s WHERE id=%s", (payload["maintenance"], node_id))
            if payload.get("password"):
                c.execute("UPDATE nodes SET password_cipher=%s WHERE id=%s",
                          (self.encrypt(node_id, payload["password"]), node_id))
            if payload.get("ssh_user"):
                c.execute("UPDATE nodes SET ssh_user=%s,probe_requested=TRUE WHERE id=%s",
                          (payload["ssh_user"], node_id))
            if payload.get("hccn_ids") is not None:
                c.execute("UPDATE nodes SET metadata=JSON_SET(COALESCE(metadata,JSON_OBJECT()),'$.hccn_ids',CAST(%s AS JSON)),probe_requested=TRUE WHERE id=%s",
                          (encode(payload["hccn_ids"]),node_id))
            c.execute("UPDATE nodes SET config_version=config_version+1 WHERE id=%s",(node_id,))
            self.db.audit(c, actor, "node.update", node_id,
                          {k: ("changed" if k == "password" else v) for k, v in payload.items()})
        return self.get(node_id)

    def request_probe(self, node_id, actor):
        self.require_admin(actor)
        with self.db.transaction() as c:
            c.execute("UPDATE nodes SET probe_requested=TRUE WHERE id=%s", (node_id,))
            if not c.rowcount:
                if not self.db.one("SELECT id FROM nodes WHERE id=%s", (node_id,)):
                    raise DomainError("节点不存在", 404)
            self.db.audit(c, actor, "node.probe.request", node_id)

    def record_probe(self, node_id, mounts=None, metadata=None,clear_requested=False):
        with self.db.transaction() as c:
            if mounts is not None:
                c.execute("UPDATE nodes SET mounts=%s WHERE id=%s", (encode(mounts), node_id))
            if metadata is not None:
                c.execute("UPDATE nodes SET metadata=JSON_MERGE_PATCH(COALESCE(metadata,JSON_OBJECT()),CAST(%s AS JSON)) WHERE id=%s",
                          (encode(metadata), node_id))
            if clear_requested:
                c.execute("UPDATE nodes SET probe_requested=FALSE WHERE id=%s", (node_id,))

    def credentials(self, node_id, actor):
        allowed = actor.admin or self.db.one("""SELECT r.id FROM resource_requests r
                   JOIN device_ownership o ON o.request_id=r.id JOIN devices d ON d.id=o.device_id
                   WHERE d.node_id=%s AND r.owner_user_id=%s AND r.status='ACTIVE' LIMIT 1""", (node_id, actor.id))
        if not allowed:
            raise DomainError("仅管理员或有效申请人可查看凭据", 403)
        node = self.connection(node_id)
        with self.db.transaction() as c:
            self.db.audit(c, actor, "credentials.view", node_id)
        return {k: node[k] for k in ("host", "port", "ssh_user", "password")}

    def confirm_baseline(self, device_id, actor):
        self.require_admin(actor)
        with self.db.transaction() as c:
            c.execute("SELECT node_id FROM devices WHERE id=%s", (device_id,))
            found = c.fetchone()
            if not found:
                raise DomainError("设备不存在", 404)
            c.execute("SELECT id FROM nodes WHERE id=%s FOR UPDATE", (found["node_id"],))
            c.execute("SELECT d.*,o.request_id FROM devices d LEFT JOIN device_ownership o ON o.device_id=d.id WHERE d.id=%s FOR UPDATE", (device_id,))
            d = c.fetchone()
            if (d["request_id"] or d["quality"] != "ok" or not d["process_complete"]
                or decode(d["processes"], []) or d["ai_core"] != 0 or d["memory_used"] is None
                or not d["sampled_at"] or (now() - d["sampled_at"]).total_seconds() > self.settings.stale_seconds):
                raise DomainError("只有新鲜、完整、无进程且无申请的空闲卡可确认驱动显存基线")
            c.execute("UPDATE devices SET baseline_bytes=memory_used,baseline_confirmed=TRUE WHERE id=%s", (device_id,))
            self.db.audit(c, actor, "device.baseline.confirm", device_id, {"bytes": d["memory_used"]})
