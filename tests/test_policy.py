from datetime import timedelta
import unittest
from hive.domain import now
from hive.policy import evaluate_lease


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.stamp=now()
        self.request={"purpose":"debug","status":"ACTIVE","protected_until":self.stamp}
        self.devices=[{"id":"a","quality":"ok","health":"OK","boot_id":"boot"}]
        self.samples={"a":[{"sampled_at":self.stamp-timedelta(seconds=600-i*15),
                           "quality":"ok","ai_core":0,"boot_id":"boot"} for i in range(41)]}

    def decide(self):
        return evaluate_lease(self.request,self.devices,self.samples,self.stamp)[0]

    def test_complete_idle_with_pid_is_reclaimable(self):
        self.devices[0]["processes"]=[{"pid":123}]
        self.assertEqual(self.decide(),"cleanup")

    def test_protection_is_not_based_on_create_time(self):
        self.request["protected_until"]=self.stamp+timedelta(seconds=1)
        self.assertEqual(self.decide(),"keep")

    def test_one_active_card_keeps_whole_request(self):
        self.devices.append({"id":"b","quality":"ok","health":"OK","boot_id":"boot"})
        self.samples["b"]=[dict(r) for r in self.samples["a"]]
        self.samples["b"][4]["ai_core"]=1
        self.assertEqual(self.decide(),"keep")

    def test_hole_or_unknown_never_counts_as_zero(self):
        del self.samples["a"][12]
        self.assertEqual(self.decide(),"unknown")
        self.setUp()
        self.samples["a"][12]["ai_core"]=None
        self.assertEqual(self.decide(),"unknown")

    def test_reboot_invalidates_evidence(self):
        self.samples["a"][0]["boot_id"]="old"
        self.assertEqual(self.decide(),"unknown")

    def test_task_build_not_reclaimed(self):
        self.request["purpose"]="task"
        self.assertEqual(self.decide(),"keep")
