"""Real disposable MySQL, real HTTP services, simulated remote SSH only."""
from dataclasses import replace
import os
from pathlib import Path
import shlex
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient

from hive.api import create_app
from hive.cleanup import Cleanup
from hive.domain import CommandResult, DeviceSample, Snapshot, now
from hive.execution import Execution
from tests import test_integration as database_tests


class FakeTransport:
    def __init__(self):
        self.files = {}
        self.attempts = {}
        self.events = []
        self.fail_prepare = set()
        self.unknown_close = set()
        self.launch_timeout = set()
        self.log = b"task output\n"

    def run(self, node, script, timeout=20):
        node_id = node["id"]
        if script.startswith("bash -s -- "):
            _, _, _, directory, action, *_ = shlex.split(script.splitlines()[0])
            key = (node_id, directory)
            self.events.append((action, node_id))
            if action == "launch":
                if key not in self.attempts:
                    self.attempts[key] = "RUNNING"
                    self.files[node_id, directory + "/output.log"] = self.log
                result = "STARTING" if self.attempts[key] != "CLOSED" else "CLOSED"
                if node_id in self.launch_timeout:
                    raise TimeoutError("response lost after remote launch")
            elif action == "status":
                result = self.attempts.get(key, "PREPARED")
            elif action == "close":
                if node_id in self.unknown_close:
                    result = "UNKNOWN"
                else:
                    self.attempts[key] = "CLOSED"
                    result = "CLOSED"
            else:
                raise AssertionError(action)
            return CommandResult(result + "\n", "", 0)
        if script.startswith("mv -- "):
            _, _, source, destination = shlex.split(script)
            self.files[node_id, destination] = self.files.pop((node_id, source))
            self.events.append(("prepared-file", node_id))
        return CommandResult("", "", 0)

    def put(self, node, path, content):
        if node["id"] in self.fail_prepare:
            raise OSError("simulated SFTP failure")
        self.files[node["id"], path] = content

    def read(self, node, path, offset=0, limit=65536):
        key = (node["id"], path)
        if key not in self.files:
            raise FileNotFoundError(path)
        return self.files[key][offset:offset + limit]

    def finish(self, code=0):
        for key, status in list(self.attempts.items()):
            if status != "CLOSED":
                self.attempts[key] = f"EXITED {code}"


class FakeAdapter:
    def collect(self, node):
        return Snapshot([DeviceSample(d["slot"], d["command_id"], d["chip_id"], d["logical_id"],
                                      d["memory_total"], 0, 0, "OK", process_complete=True)
                         for d in node["devices"]], "test-boot", now())

    def environment(self, devices):
        return {"ASCEND_RT_VISIBLE_DEVICES": ",".join(d["logical_id"] for d in devices)}


@unittest.skipUnless(os.getenv("HIVE_TEST_MYSQL_PORT"), "Set HIVE_TEST_MYSQL_PORT for isolated MySQL tests")
class TaskIntegration(unittest.TestCase):
    # Reuse setup routines without inheriting the original test case (which
    # would accidentally run all its unrelated test methods a second time).
    node = database_tests.MySQLIntegration.node

    @classmethod
    def setUpClass(cls):
        database_tests.MySQLIntegration.setUpClass.__func__(cls)
        cls.directory = TemporaryDirectory(prefix="hive_task_test_")
        cls.settings = replace(cls.settings, data_dir=Path(cls.directory.name))

    @classmethod
    def tearDownClass(cls):
        try:
            database_tests.MySQLIntegration.tearDownClass.__func__(cls)
        finally:
            cls.directory.cleanup()

    def setUp(self):
        with self.db.transaction() as cursor:
            for table in ("process_events", "device_rollups", "worker_state"):
                cursor.execute("DELETE FROM " + table)
        database_tests.MySQLIntegration.setUp(self)
        self.transport = FakeTransport()
        self.adapters = {"ascend": FakeAdapter()}
        self.telemetry.adapters = self.adapters
        self.execution = Execution(self.db, self.transport, self.inventory, self.resources, self.adapters, self.settings)
        self.cleanup = Cleanup(self.db, self.transport, self.inventory, self.telemetry, self.resources)
        services = SimpleNamespace(db=self.db, settings=self.settings, identity=self.identity,
                                   inventory=self.inventory, catalog=self.catalog, resources=self.resources,
                                   telemetry=self.telemetry, execution=self.execution)
        self.client = TestClient(create_app(services, self.settings))
        self.addCleanup(self.client.close)
        self.client.post("/api/session", json={"username": "alice"})

    def submit(self, key="one", machine_count=1, **overrides):
        body = {"idempotency_key": key, "name": "integration task", "script": "echo test",
                "environment": {"EXAMPLE_SECRET": "private"}, "timeout_seconds": 60,
                "resource": {"generation": "A2", "cards_per_node": 1, "machine_count": machine_count}}
        body.update(overrides)
        response = self.client.post("/api/tasks", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json(), body

    def deliver(self, task):
        reserved = self.resources.reserve(task["request_id"])
        self.assertIsNotNone(reserved)
        self.assertTrue(self.resources.deliver(task["request_id"], reserved["version"]))

    def finish_cleanup(self, task):
        self.assertTrue(self.cleanup.run(task["request_id"]))
        self.execution.tick()
        self.assertEqual(self.resources.get(task["request_id"])["status"], "RELEASED")
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM device_ownership")["n"], 0)

    def test_api_submit_idempotency_owner_and_log_permissions(self):
        self.node()
        task, body = self.submit()
        same = self.client.post("/api/tasks", json=body)
        self.assertEqual(same.status_code, 201, same.text)
        self.assertEqual(same.json()["id"], task["id"])
        self.assertEqual(task["owner_user_id"], self.alice.id)
        changed = self.client.post("/api/tasks", json={**body, "script": "echo changed"})
        self.assertEqual(changed.status_code, 409, changed.text)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM executions")["n"], 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM resource_requests")["n"], 1)
        forged = self.client.post("/api/tasks", json={**body, "owner_user_id": self.bob.id})
        self.assertEqual(forged.status_code, 422)
        self.deliver(task)
        self.execution.tick()
        logs = self.client.get(f"/api/tasks/{task['id']}/logs")
        self.assertEqual(logs.status_code, 200, logs.text)
        self.assertEqual(logs.json()["nodes"][0]["text"], "task output\n")
        self.client.post("/api/session", json={"username": "bob"})
        self.assertEqual(self.client.post(f"/api/tasks/{task['id']}/cancel").status_code, 403)
        self.assertEqual(self.client.get(f"/api/tasks/{task['id']}/logs").status_code, 403)
        other_view = self.client.get("/api/tasks").json()[0]
        self.assertNotIn("script", other_view["spec"])
        self.assertNotIn("environment", other_view["spec"])

    def test_full_multinode_lifecycle_and_stable_rank(self):
        self.node("10.0.0.1")
        self.node("10.0.0.2")
        task, _ = self.submit(machine_count=2)
        self.deliver(task)
        self.execution.tick()
        running = self.execution.get(task["id"])
        self.assertEqual(running["status"], "RUNNING", running)
        first_launch = next(i for i, e in enumerate(self.transport.events) if e[0] == "launch")
        prepared = {node_id for action, node_id in self.transport.events[:first_launch] if action == "prepared-file"}
        self.assertEqual(len(prepared), 2)
        for rank, item in enumerate(running["nodes"]):
            job = self.transport.files[item["node_id"], item["remote_path"] + "/job.sh"].decode()
            self.assertIn(f"export HIVE_RANK={rank}", job)
            self.assertIn("export HIVE_WORLD_SIZE=2", job)
        # Repeated reconciliation must not launch either immutable attempt again.
        self.execution.tick()
        self.assertEqual(sum(event[0] == "launch" for event in self.transport.events), 2)
        self.transport.finish()
        self.execution.tick()
        collecting = self.execution.get(task["id"])
        self.assertEqual(collecting["status"], "CLEANING", collecting)
        self.assertTrue(all(n["status"] == "CLOSED" for n in collecting["nodes"]))
        self.assertEqual(self.resources.get(task["request_id"])["status"], "RELEASING")
        self.finish_cleanup(task)
        finished = self.execution.get(task["id"])
        self.assertEqual(finished["status"], "SUCCEEDED")
        self.assertTrue(all(n["slots"] for n in finished["nodes"]))

    def test_multinode_prepare_failure_closes_without_any_launch(self):
        first, second = self.node("10.0.0.1"), self.node("10.0.0.2")
        task, _ = self.submit(machine_count=2)
        self.deliver(task)
        self.transport.fail_prepare.add(max(first["id"], second["id"]))
        self.execution.tick()
        self.assertFalse(any(event[0] == "launch" for event in self.transport.events))
        self.assertEqual(self.execution.get(task["id"])["status"], "CLEANING")
        self.finish_cleanup(task)
        self.assertEqual(self.execution.get(task["id"])["status"], "FAILED")

    def test_running_cancel_holds_locks_until_remote_close_confirmed(self):
        node = self.node()
        task, _ = self.submit()
        self.deliver(task)
        self.execution.tick()
        self.transport.unknown_close.add(node["id"])
        response = self.client.post(f"/api/tasks/{task['id']}/cancel")
        self.assertEqual(response.status_code, 200, response.text)
        self.execution.tick()
        self.assertEqual(self.execution.get(task["id"])["status"], "UNKNOWN")
        self.assertEqual(self.resources.get(task["request_id"])["status"], "ACTIVE")
        self.assertFalse(self.cleanup.run(task["request_id"]))
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM device_ownership")["n"], 1)
        self.transport.unknown_close.clear()
        self.execution.tick()
        self.finish_cleanup(task)
        self.assertEqual(self.execution.get(task["id"])["status"], "CANCELLED")

    def test_queued_cancel_never_uses_remote(self):
        task, _ = self.submit()
        self.client.post(f"/api/tasks/{task['id']}/cancel")
        self.execution.tick()
        self.assertEqual(self.execution.get(task["id"])["status"], "CANCELLED")
        self.assertEqual(self.resources.get(task["request_id"])["status"], "CANCELLED")
        self.assertFalse(self.transport.events)

    def test_lost_launch_response_never_launches_second_attempt(self):
        node = self.node()
        task, _ = self.submit()
        self.deliver(task)
        self.transport.launch_timeout.add(node["id"])
        self.transport.unknown_close.add(node["id"])
        self.execution.tick()
        self.execution.tick()
        self.assertEqual(self.execution.get(task["id"])["status"], "UNKNOWN")
        self.assertEqual(sum(e[0] == "launch" for e in self.transport.events), 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM device_ownership")["n"], 1)
        self.transport.unknown_close.clear()
        self.execution.tick()
        self.finish_cleanup(task)
        self.assertEqual(self.execution.get(task["id"])["status"], "FAILED")

    def test_logs_are_drained_before_releasing(self):
        self.node()
        self.transport.log = b"x" * 200000
        task, _ = self.submit()
        self.deliver(task)
        self.execution.tick()
        self.transport.finish()
        self.execution.tick()
        self.assertEqual(self.execution.get(task["id"])["status"], "COLLECTING")
        self.execution.tick()
        self.assertEqual(self.execution.get(task["id"])["status"], "CLEANING")
        item = self.execution.get(task["id"])["nodes"][0]
        self.assertEqual(self.execution._log_path(task, item).stat().st_size, 200000)
        self.finish_cleanup(task)


if __name__ == "__main__":
    unittest.main()
