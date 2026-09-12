import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace

from hive.domain import Actor, DomainError
from hive.execution import Execution, render_job, validate_spec


class ExecutionTests(unittest.TestCase):
    def payload(self, **kw):
        return dict(name="demo", script="echo hello", idempotency_key="once", **kw)

    def test_input_validation(self):
        self.assertEqual(validate_spec(self.payload())["timeout_seconds"], 3600)
        for kw in ({"environment": {"BAD;echo": "value"}}, {"timeout_seconds": 0},
                   {"timeout_seconds": True}, {"workdir": "relative"}):
            with self.assertRaises(DomainError):
                validate_spec(self.payload(**kw))

    def test_assignment_overrides_user_devices_and_values_are_quoted(self):
        spec = validate_spec(self.payload(environment={"ASCEND_RT_VISIBLE_DEVICES": "99", "TEXT": "$(touch /tmp/bad)'"}))
        job = render_job(spec, {"ASCEND_RT_VISIBLE_DEVICES": "0,1"}, "/tmp/task")
        self.assertIn("export ASCEND_RT_VISIBLE_DEVICES=0,1", job)
        self.assertNotIn("DEVICES=99", job)
        self.assertIn("'$(touch /tmp/bad)'", job)

    def test_ownership_is_checked(self):
        with self.assertRaises(DomainError):
            Execution._authorize({"owner_user_id": "a"}, Actor("b", "other"))

    def test_unconfirmed_close_never_releases(self):
        db, resources = Mock(), Mock()
        db.transaction.return_value.__enter__ = Mock(return_value=Mock())
        db.transaction.return_value.__exit__ = Mock(return_value=False)
        execution = Execution(db, Mock(), Mock(), resources, {}, Mock())
        execution._control = Mock(return_value="UNKNOWN")
        execution._pull_log = Mock(return_value=True)
        execution._node_status = Mock()
        execution._status = Mock()
        task = {"id": "id", "request_id": "req", "spec": {}, "cancel_requested": False,
                "nodes": [{"node_id": "node", "remote_path": "/tmp/task", "status": "STARTING"}]}
        execution._finish(task, "FAILED")
        resources.release.assert_not_called()
        execution._status.assert_called_with(task, "UNKNOWN", "任务关闭未确认，保留全部资源")

    def test_cancel_rejects_process_using_another_card(self):
        resources, adapter = Mock(), Mock()
        resources.devices.return_value = [{"node_id": "node", "slot": "0:0"}]
        process = SimpleNamespace(pid=42, device_ids=["0:0", "1:0"])
        adapter.collect.return_value = SimpleNamespace(quality="ok", devices=[
            SimpleNamespace(slot=slot, quality="ok", process_complete=True, processes=[process])
            for slot in ["0:0", "1:0"]])
        execution = Execution(Mock(), Mock(), Mock(), resources, {"ascend": adapter}, Mock())
        with self.assertRaises(DomainError):
            execution._check_close_scope({"request_id": "request"}, {"id": "node", "adapter": "ascend"})


if __name__ == "__main__":
    unittest.main()
