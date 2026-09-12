import unittest
from unittest.mock import Mock, MagicMock
from hive.cleanup import Cleanup, cleanup_targets, kill_script
from hive.domain import DomainError, now
from hive.inventory import device_status, idle_memory_limit


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

    def idle_card(self, **overrides):
        return dict(dict(id='a',node_id='node',adapter='ascend',quality='ok',health='OK',
                    process_complete=True,processes=[],sampled_at=now(),ai_core=0,memory_used=3*1024**3,
                    baseline_bytes=3*1024**3,baseline_confirmed=True),**overrides)

    def test_confirmed_ascend_memory_jitter_has_bounded_tolerance(self):
        baseline=3*1024**3
        for delta,expected in ((0,'available'),(1024**2,'available'),(2*1024**2,'available'),(2*1024**2+1,'external')):
            with self.subTest(delta=delta):
                self.assertEqual(device_status(self.idle_card(memory_used=baseline+delta)),expected)

    def test_unconfirmed_or_other_adapter_does_not_gain_tolerance(self):
        self.assertEqual(idle_memory_limit(self.idle_card(baseline_confirmed=False)),0)
        self.assertEqual(device_status(self.idle_card(baseline_confirmed=False,memory_used=1)),'external')
        self.assertEqual(device_status(self.idle_card(baseline_confirmed=False,memory_used=0)),'available')
        for adapter in ('other',None):
            with self.subTest(adapter=adapter):
                card=self.idle_card(adapter=adapter,memory_used=3*1024**3+1)
                self.assertEqual(idle_memory_limit(card),3*1024**3)
                self.assertEqual(device_status(card),'external')

    def test_memory_tolerance_never_overrides_pid_or_compute_activity(self):
        for changes in ({'processes':[{'pid':42}]},{'ai_core':1}):
            with self.subTest(changes=changes):
                self.assertEqual(device_status(self.idle_card(**changes)),'external')

    def test_cleanup_uses_same_memory_limit_and_retains_other_guards(self):
        baseline=3*1024**3
        cases=[({'memory_used':baseline+2*1024**2},True),
               ({'memory_used':baseline+2*1024**2+1},False),
               ({'baseline_confirmed':False},False),
               ({'adapter':'other','memory_used':baseline+1},False),
               ({'ai_core':1},False),({'processes':device('a')['processes']},False),
               ({'quality':'unknown'},False),({'process_complete':False},False)]
        for changes,expected in cases:
            with self.subTest(changes=changes):
                card=self.idle_card(**changes)
                resources=Mock()
                resources.get.return_value={'id':'request','status':'RELEASING','version':1,'purpose':'debug','release_cause':'manual'}
                resources.devices.return_value=[card]
                resources.finish_release.return_value=True
                inventory=Mock()
                inventory.get.return_value={'devices':[card]}
                transport=Mock()
                transport.run.return_value=Mock(code=0)
                cleanup=Cleanup(MagicMock(),transport,inventory,Mock(),resources)
                self.assertEqual(cleanup.run('request'),expected)
                self.assertEqual(resources.finish_release.called,expected)


if __name__ == "__main__":
    unittest.main()
