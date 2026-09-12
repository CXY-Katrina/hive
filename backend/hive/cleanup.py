"""Conservative per-allocation PID cleanup, with no container-wide operations."""
import re
import shlex

from .domain import DomainError, SYSTEM, decode, encode, now
from .policy import evaluate_lease
from .inventory import idle_memory_limit


def cleanup_targets(devices, allocated_ids):
    """Full-node process membership is required to protect other allocations."""
    memberships, identities = {}, {}
    for device in devices:
        if not device.get("process_complete") or device.get("quality") != "ok":
            raise DomainError("进程列表不完整，暂缓清理")
        for process in decode(device.get("processes"), []):
            pid = process.get("pid")
            identity = (process.get("start_time"), process.get("boot_id"))
            if not isinstance(pid, int) or pid < 2 or not all(identity) or not str(identity[0]).isdigit():
                raise DomainError("缺少可核验的进程身份，暂缓清理")
            if pid in identities and identities[pid] != identity:
                raise DomainError("进程身份不一致，暂缓清理")
            identities[pid] = identity
            memberships.setdefault(pid, set()).add(device["id"])
    targets = []
    for pid, used in memberships.items():
        if used & allocated_ids:
            if used - allocated_ids:
                raise DomainError("进程跨越其他申请或未申请卡，暂缓清理")
            start, boot = identities[pid]
            targets.append(dict(pid=pid, start_time=str(start), boot_id=boot))
    return targets


def kill_script(targets):
    """Use exact boot/PID/start ticks before both TERM and KILL (PID reuse)."""
    lines = ["set -eu", "boot=$(cat /proc/sys/kernel/random/boot_id)",
             "identity() { [ -r /proc/$1/stat ] || return 1; raw=$(cat /proc/$1/stat) || return 1; rest=${raw##*) }; set -- $rest; printf '%s' \"${20}\"; }"]
    for target in targets:
        if not isinstance(target["pid"], int) or target["pid"] < 2 or not re.fullmatch(r"\d+", target["start_time"]):
            raise DomainError("无效进程身份")
        boot = shlex.quote(target["boot_id"])
        lines.append(f"[ \"$boot\" = {boot} ] || exit 73")
        lines.append(f"actual=$(identity {target['pid']} || true); [ -z \"$actual\" ] || [ \"$actual\" = {target['start_time']} ] || exit 74")
    for signal in ["TERM", "KILL"]:
        for target in targets:
            lines.append(f"if [ \"$(identity {target['pid']} || true)\" = {target['start_time']} ]; then kill -{signal} {target['pid']} 2>/dev/null || true; fi")
        if targets and signal == "TERM":
            lines.append("sleep 2")
    return "\n".join(lines)


class Cleanup:
    def __init__(self, db, transport, inventory, telemetry, resources):
        self.db, self.transport, self.inventory = db, transport, inventory
        self.telemetry, self.resources = telemetry, resources

    def run(self, request_id):
        request = self.resources.get(request_id)
        if request["status"] != "RELEASING":
            return False
        epoch = request["version"]
        try:
            if request["purpose"] == "task":
                execution = self.db.one("SELECT id FROM executions WHERE request_id=%s", (request_id,))
                if execution:
                    nodes = self.db.all("SELECT status FROM execution_nodes WHERE execution_id=%s", (execution["id"],))
                    if any(n["status"] != "CLOSED" for n in nodes):
                        raise DomainError("任务 attempt 尚未关闭，保留卡锁")
            allocations = self.resources.devices(request_id)
            groups = {}
            for device in allocations:
                groups.setdefault(device["node_id"], set()).add(device["id"])
            plans = []
            # Validate every node before the first signal, avoiding partial
            # cleanup when a cross-allocation conflict is already observable.
            for node_id, allocated_ids in groups.items():
                self.telemetry.collect_node(node_id)
                node = self.inventory.get(node_id)
                full = node["devices"]
                selected = [d for d in full if d["id"] in allocated_ids]
                if len(selected) != len(allocated_ids):
                    raise DomainError("申请设备缺失，保留卡锁")
                if any(d.get("quality") != "ok" or d.get("health") != "OK" or not d.get("process_complete") for d in selected):
                    raise DomainError("设备采集无效，保留卡锁")
                # Automatic decisions must still be idle at signal time. User
                # return/task cancellation may terminate active computation.
                plans.append((node_id, allocated_ids, cleanup_targets(full, allocated_ids)))
            if request.get("release_cause") == "idle":
                self._check_idle(request, epoch)
            for node_id, allocated_ids, targets in plans:
                # Re-observe membership immediately before signalling; reject
                # newly moved PIDs instead of applying an earlier stale plan.
                self.telemetry.collect_node(node_id)
                fresh = self.inventory.get(node_id)["devices"]
                targets = cleanup_targets(fresh, allocated_ids)
                if request.get("release_cause") == "idle":
                    self._check_idle(self.resources.get(request_id), epoch)
                if targets:
                    if not self.resources.begin_cleanup(request_id, epoch):
                        raise DomainError("申请状态已改变，取消本次清理")
                    result = self.transport.run(self.inventory.connection(node_id), kill_script(targets), timeout=20)
                    if result.code:
                        raise DomainError("远端进程身份变化或终止结果未知")
            for node_id, allocated_ids, _ in plans:
                self.telemetry.collect_node(node_id)
                selected = [d for d in self.inventory.get(node_id)["devices"] if d["id"] in allocated_ids]
                if len(selected) != len(allocated_ids):
                    raise DomainError("清理复核设备缺失")
                for device in selected:
                    if (device.get("quality") != "ok" or device.get("health") != "OK" or
                            not device.get("process_complete") or decode(device.get("processes"), []) or
                            device.get("ai_core") != 0 or
                            (device.get("baseline_bytes", 0) > 0 and not device.get("baseline_confirmed")) or
                            device.get("memory_used") is None or device["memory_used"] > idle_memory_limit(device)):
                        raise DomainError("进程/计算/显存未恢复为空闲，保留卡锁")
            return bool(self.resources.finish_release(request_id, epoch))
        except Exception as exc:
            self.resources.note(request_id, str(exc)[:2000])
            with self.db.transaction() as c:
                self.db.audit(c, SYSTEM, "resource.cleanup_pending", request_id, {"reason": str(exc)[:2000]})
            return False

    def _check_idle(self, request, epoch):
        devices = self.resources.devices(request["id"])
        decision, reason, _ = evaluate_lease({**request, "status": "ACTIVE"}, devices,
                                            self.telemetry.window(devices), now(),
                                            self.telemetry.settings.sample_seconds)
        if decision != "cleanup":
            if decision == "keep" and not request.get("cleanup_started_at"):
                self.resources.cancel_idle_release(request["id"], epoch)
            raise DomainError("自动回收复核未通过：" + reason)
