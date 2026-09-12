"""Agentless, persistent SSH task attempts; remote ambiguity never means retry."""
from collections import defaultdict
from pathlib import Path
import hashlib
import re
import shlex

from .domain import DomainError, SYSTEM, decode, encode, now, uid


TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
SCRIPT_DIR = Path(__file__).parent / "scripts"


def validate_spec(payload):
    name = str(payload.get("name", "")).strip()
    script = payload.get("script", "")
    if not name or len(name) > 128 or not isinstance(script, str) or not script.strip() or len(script.encode()) > 1_048_576:
        raise DomainError("任务名称和脚本必填，脚本最多 1 MiB", 422)
    workdir = payload.get("workdir") or ""
    if not isinstance(workdir, str) or (workdir and (not workdir.startswith("/") or "\x00" in workdir)):
        raise DomainError("工作目录必须是绝对路径", 422)
    environment = payload.get("environment", {})
    if not isinstance(environment, dict) or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) or not isinstance(v, str) or "\x00" in v for k, v in environment.items()):
        raise DomainError("环境变量格式无效", 422)
    timeout = payload.get("timeout_seconds", 3600)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 604800:
        raise DomainError("任务时限需为 1–604800 秒", 422)
    key = payload.get("idempotency_key")
    if not isinstance(key, str) or not key or len(key) > 128:
        raise DomainError("缺少有效的幂等键", 422)
    return dict(name=name, script=script, workdir=workdir, environment=environment,
                timeout_seconds=timeout, resource=payload.get("resource", {}))


def render_job(spec, environment, directory):
    # Hardware assignment and rank values override user-provided variables.
    variables = {**spec["environment"], **environment}
    lines = ["#!/usr/bin/env bash", "set -euo pipefail", "cd -- " + shlex.quote(spec["workdir"] or directory)]
    lines += ["export " + k + "=" + shlex.quote(str(v)) for k, v in sorted(variables.items())]
    lines.append(spec["script"])
    return "\n".join(lines) + "\n"


class Execution:
    def __init__(self, db, transport, inventory, resources, adapters, settings):
        self.db, self.transport, self.inventory = db, transport, inventory
        self.resources, self.adapters, self.settings = resources, adapters, settings

    def submit(self, actor, payload):
        spec = validate_spec(payload)
        body_hash = hashlib.sha256(encode(spec).encode()).hexdigest()
        with self.db.transaction() as c:
            request = self.resources.create(actor, spec["resource"], payload["idempotency_key"], purpose="task", cursor=c)
            c.execute("SELECT * FROM executions WHERE request_id=%s FOR UPDATE", (request["id"],))
            existing = c.fetchone()
            if existing:
                if decode(existing["spec"]).get("body_hash") != body_hash:
                    raise DomainError("同一幂等键对应不同任务")
                return self._decode(existing)
            execution_id = uid()
            spec["body_hash"] = body_hash
            c.execute("INSERT INTO executions (id,request_id,owner_user_id,owner_name,name,spec,status,created_at) VALUES (%s,%s,%s,%s,%s,%s,'QUEUED',%s)",
                      (execution_id, request["id"], actor.id, actor.username, spec["name"], encode(spec), now()))
            self.db.audit(c, actor, "task.submit", execution_id, {"request_id": request["id"]})
        return self.get(execution_id)

    def _decode(self, row):
        if row:
            row = dict(row)
            row["spec"] = decode(row.get("spec"), {})
        return row

    def get(self, execution_id):
        row = self._decode(self.db.one("SELECT * FROM executions WHERE id=%s", (execution_id,)))
        if not row:
            raise DomainError("任务不存在", 404)
        row["nodes"] = self.db.all("SELECT e.*,n.host FROM execution_nodes e JOIN nodes n ON n.id=e.node_id WHERE execution_id=%s ORDER BY e.node_id", (execution_id,))
        assigned = self.resources.devices(row["request_id"], include_released=True)
        for node in row["nodes"]:
            node["result"] = decode(node.get("result"), {})
            node["slots"] = sorted({str(d["slot"]) for d in assigned if d["node_id"] == node["node_id"]})
        return row

    def list(self, actor=None):
        rows = [self.get(r["id"]) for r in self.db.all("SELECT id FROM executions ORDER BY created_at DESC LIMIT 500")]
        for row in rows:
            if actor is None or (not actor.admin and actor.id != row["owner_user_id"]):
                row["spec"] = {k: v for k, v in row["spec"].items() if k in {"name", "resource", "timeout_seconds"}}
        return rows

    def cancel(self, execution_id, actor):
        task = self.get(execution_id)
        self._authorize(task, actor)
        if task["status"] in TERMINAL:
            return task
        with self.db.transaction() as c:
            c.execute("UPDATE executions SET cancel_requested=TRUE WHERE id=%s", (execution_id,))
            self.db.audit(c, actor, "task.cancel", execution_id)
        return self.get(execution_id)

    @staticmethod
    def _authorize(task, actor):
        if not actor.admin and task["owner_user_id"] != actor.id:
            raise DomainError("只能操作自己的任务", 403)

    def _status(self, task, status, reason=None):
        with self.db.transaction() as c:
            c.execute("UPDATE executions SET status=%s,reason=%s WHERE id=%s", (status, reason, task["id"]))

    def _node_status(self, task, node_id, status, result=None):
        with self.db.transaction() as c:
            if result is None:
                c.execute("UPDATE execution_nodes SET status=%s WHERE execution_id=%s AND node_id=%s", (status, task["id"], node_id))
            else:
                c.execute("UPDATE execution_nodes SET status=%s,result=%s WHERE execution_id=%s AND node_id=%s", (status, encode(result), task["id"], node_id))

    def _control(self, node, directory, action):
        script = (SCRIPT_DIR / "task_control.sh").read_text(encoding="utf-8")
        command = "bash -s -- " + shlex.quote(directory) + " " + shlex.quote(action) + " <<'HIVE_CONTROL_EOF'\n" + script + "\nHIVE_CONTROL_EOF"
        result = self.transport.run(node, command, timeout=20)
        if result.code:
            raise RuntimeError("远端任务控制失败: " + result.stderr[-1000:])
        return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "UNKNOWN"

    def tick(self):
        for row in self.db.all("SELECT id FROM executions WHERE status NOT IN ('SUCCEEDED','FAILED','CANCELLED') ORDER BY created_at"):
            self.tick_one(row["id"])

    def tick_one(self, execution_id):
        task = self.get(execution_id)
        if task["status"] in TERMINAL:
            return
        try:
            self._advance(task)
        except Exception as exc:
            self._status(task, "UNKNOWN", str(exc)[:2000])

    def _advance(self, task):
        request = self.resources.get(task["request_id"])
        # An external return must also close any possible delayed start.
        if task["cancel_requested"] or request["status"] in {"RELEASING", "RELEASED", "CANCELLED"} or task["spec"].get("outcome"):
            return self._finish(task, "CANCELLED")
        if task["status"] == "CLEANING":
            if request["status"] == "RELEASED":
                self._finalize(task)
            return
        if request["status"] in {"FAILED", "CONFLICT"}:
            return self._finish(task, "FAILED")
        if request["status"] != "ACTIVE":
            return
        if not task["nodes"]:
            devices = self.resources.devices(request["id"])
            groups = defaultdict(list)
            for device in devices:
                groups[device["node_id"]].append(device)
            if not groups:
                raise RuntimeError("活动申请缺少设备")
            with self.db.transaction() as c:
                for node_id in sorted(groups):
                    path = f"/var/tmp/hive/tasks/{task['id']}/1"
                    c.execute("INSERT INTO execution_nodes (execution_id,node_id,status,remote_path) VALUES (%s,%s,'PENDING',%s)", (task["id"], node_id, path))
                c.execute("UPDATE executions SET status='PREPARING' WHERE id=%s", (task["id"],))
            task = self.get(task["id"])
        if any(n["status"] == "PENDING" for n in task["nodes"]):
            try:
                self._prepare(task)
            except Exception:
                self._finish(self.get(task["id"]), "FAILED")
                return
            task = self.get(task["id"])
        # Persist STARTING before sending the first launch operation. A restart
        # only polls STARTING nodes; it never blindly repeats the launch.
        for item in task["nodes"]:
            if item["status"] == "PREPARED":
                self._node_status(task, item["node_id"], "STARTING")
                self._status(task, "STARTING")
                with self.db.transaction() as c:
                    c.execute("UPDATE executions SET started_at=COALESCE(started_at,%s) WHERE id=%s", (now(), task["id"]))
                node = self.inventory.connection(item["node_id"])
                try:
                    self._control(node, item["remote_path"], "launch")
                except Exception:
                    self._finish(self.get(task["id"]), "FAILED")
                    return
        task = self.get(task["id"])
        statuses = []
        for item in task["nodes"]:
            node = self.inventory.connection(item["node_id"])
            status = self._control(node, item["remote_path"], "status")
            self._pull_log(task, item, node)
            if status.startswith("EXITED "):
                code = int(status.split()[1])
                self._node_status(task, item["node_id"], "EXITED", {"exit_code": code})
                statuses.append(("EXITED", code))
            else:
                status = status if status in {"RUNNING", "CLOSED"} else "UNKNOWN"
                self._node_status(task, item["node_id"], status)
                statuses.append((status, None))
        if any(code not in {None, 0} for _, code in statuses):
            return self._finish(self.get(task["id"]), "FAILED")
        if all(status == "EXITED" for status, _ in statuses):
            return self._finish(self.get(task["id"]), "SUCCEEDED")
        if any(status in {"UNKNOWN", "CLOSED"} for status, _ in statuses):
            self._status(task, "UNKNOWN", "启动或进程结果未知；保留资源且不重复执行")
        else:
            self._status(task, "RUNNING")
            with self.db.transaction() as c:
                c.execute("UPDATE executions SET started_at=COALESCE(started_at,%s) WHERE id=%s", (now(), task["id"]))
        if task.get("started_at") and (now() - task["started_at"]).total_seconds() > task["spec"]["timeout_seconds"] + 30:
            self._finish(self.get(task["id"]), "FAILED")

    def _prepare(self, task):
        devices = self.resources.devices(task["request_id"])
        manifest = [{"node_id": n["node_id"], "host": n["host"], "rank": rank,
                     "devices": [d for d in devices if d["node_id"] == n["node_id"]]}
                    for rank, n in enumerate(task["nodes"])]
        for rank, item in enumerate(task["nodes"]):
            if item["status"] != "PENDING":
                continue
            node = self.inventory.connection(item["node_id"])
            path = item["remote_path"]
            result = self.transport.run(node, "umask 077; mkdir -p -- " + shlex.quote(path), timeout=20)
            if result.code:
                raise RuntimeError("任务目录准备失败")
            assigned = [d for d in devices if d["node_id"] == item["node_id"]]
            env = self.adapters[node.get("adapter", "ascend")].environment(assigned)
            env.update(HIVE_RANK=str(rank), HIVE_WORLD_SIZE=str(len(task["nodes"])), HIVE_MANIFEST=path + "/manifest.json")
            files = {"runner.sh": (SCRIPT_DIR / "task_runner.sh").read_text(encoding="utf-8"),
                     "job.sh": render_job(task["spec"], env, path), "manifest.json": encode(manifest),
                     "timeout": str(task["spec"]["timeout_seconds"])}
            for name, content in files.items():
                self.transport.put(node, path + "/" + name + ".upload", content.encode())
                move = "mv -- " + shlex.quote(path + "/" + name + ".upload") + " " + shlex.quote(path + "/" + name)
                if self.transport.run(node, move, timeout=20).code:
                    raise RuntimeError("任务文件提交失败")
            self._node_status(task, item["node_id"], "PREPARED")

    def _finish(self, task, outcome):
        # Preserve intended outcome across worker restart and cleanup retries.
        spec = task["spec"]
        spec["outcome"] = "CANCELLED" if task["cancel_requested"] else spec.get("outcome", outcome)
        with self.db.transaction() as c:
            c.execute("UPDATE executions SET spec=%s,status='COLLECTING' WHERE id=%s", (encode(spec), task["id"]))
        all_closed = True
        all_logs = True
        for item in task["nodes"]:
            try:
                node = self.inventory.connection(item["node_id"])
                if item["status"] not in {"CLOSED", "PENDING", "PREPARED"}:
                    self._check_close_scope(task, node)
                closed = item["status"] == "CLOSED" or self._control(node, item["remote_path"], "close") == "CLOSED"
                all_logs = self._pull_log(task, item, node) and all_logs
                self._node_status(task, item["node_id"], "CLOSED" if closed else "UNKNOWN")
                all_closed = all_closed and closed
            except Exception:
                all_closed = False
                self._node_status(task, item["node_id"], "UNKNOWN")
        if not all_closed:
            self._status(task, "UNKNOWN", "任务关闭未确认，保留全部资源")
            return
        if not all_logs:
            self._status(task, "COLLECTING", "正在分批拉取剩余日志")
            return
        self.resources.release(task["request_id"], SYSTEM)
        self._status(task, "CLEANING")
        request = self.resources.get(task["request_id"])
        if request["status"] in {"RELEASED", "CANCELLED"} or (request["status"] == "FAILED" and not self.resources.devices(task["request_id"])):
            self._finalize(self.get(task["id"]))

    def _check_close_scope(self, task, node):
        assigned = {str(d["slot"]) for d in self.resources.devices(task["request_id"]) if d["node_id"] == node["id"]}
        snapshot = self.adapters[node.get("adapter", "ascend")].collect(node)
        if snapshot.quality != "ok" or any(d.quality != "ok" or not d.process_complete for d in snapshot.devices):
            raise DomainError("任务关闭前进程清单不完整，保留资源")
        if not assigned.issubset({d.slot for d in snapshot.devices}):
            raise DomainError("任务关闭前分配设备缺失")
        membership = {}
        for device in snapshot.devices:
            for process in device.processes:
                membership.setdefault(process.pid, set()).add(device.slot)
                membership[process.pid].update(process.device_ids)
        if any(used & assigned and used - assigned for used in membership.values()):
            raise DomainError("任务进程跨越其他分配卡，暂缓关闭")

    def _finalize(self, task):
        with self.db.transaction() as c:
            c.execute("UPDATE executions SET status=%s,ended_at=%s WHERE id=%s", (task["spec"].get("outcome", "CANCELLED"), now(), task["id"]))

    def _log_path(self, task, item):
        path = Path(self.settings.data_dir) / "logs" / task["id"] / (item["node_id"] + ".log")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _pull_log(self, task, item, node):
        path = self._log_path(task, item)
        offset = int(item.get("log_offset", 0))
        # The central file size is authoritative after a crash between write/DB.
        offset = path.stat().st_size if path.exists() else 0
        remaining = 20 * 1024 * 1024 - offset
        if remaining <= 0:
            return True
        try:
            chunk = self.transport.read(node, item["remote_path"] + "/output.log", offset=offset, limit=min(65536, remaining))
        except FileNotFoundError:
            return True
        if chunk:
            with path.open("ab") as file:
                file.write(chunk)
            with self.db.transaction() as c:
                c.execute("UPDATE execution_nodes SET log_offset=%s WHERE execution_id=%s AND node_id=%s", (offset + len(chunk), task["id"], item["node_id"]))
        return len(chunk) < min(65536, remaining) or offset + len(chunk) >= 20 * 1024 * 1024

    def logs(self, execution_id, actor):
        task = self.get(execution_id)
        self._authorize(task, actor)
        nodes = []
        for item in task["nodes"]:
            path = self._log_path(task, item)
            text = ""
            if path.exists():
                with path.open("rb") as file:
                    file.seek(max(0, path.stat().st_size - 65536))
                    text = file.read(65536).decode("utf-8", "replace")
            nodes.append({"node_id": item["node_id"], "host": item["host"], "text": text, "status": item["status"]})
        return {"nodes": nodes}
