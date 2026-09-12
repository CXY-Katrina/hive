import unittest
from hive.cleanup import cleanup_targets, kill_script
from hive.domain import DomainError


def device(key, pid=42):
    return {"id": key, "quality": "ok", "process_complete": True,
            "processes": [{"pid": pid, "start_time": "1234", "boot_id": "boot"}]}


class CleanupTests(unittest.TestCase):
    def test_only_allocated_process_selected(self):
        targets = cleanup_targets([device("a"), device("b", 90)], {"a"})
        self.assertEqual([p["pid"] for p in targets], [42])

    def test_shared_pid_blocks_cleanup(self):
        with self.assertRaises(DomainError):
            cleanup_targets([device("a"), device("b")], {"a"})

    def test_incomplete_other_card_blocks_inference(self):
        other = device("b", 90)
        other["process_complete"] = False
        with self.assertRaises(DomainError):
            cleanup_targets([device("a"), other], {"a"})

    def test_pid_reuse_conflict_blocks_cleanup(self):
        other = device("b")
        other["processes"][0]["start_time"] = "5678"
        with self.assertRaises(DomainError):
            cleanup_targets([device("a"), other], {"a", "b"})

    def test_script_rechecks_identity_before_each_signal(self):
        script = kill_script(cleanup_targets([device("a")], {"a"}))
        self.assertIn("kill -TERM 42", script)
        self.assertIn("kill -KILL 42", script)
        self.assertEqual(script.count("identity 42"), 3)
        self.assertNotIn("docker", script)


if __name__ == "__main__":
    unittest.main()
