import unittest
from datetime import timedelta
from hive.metrics import MetricCatalog, Metric
from hive.domain import DomainError, now


class MetricTests(unittest.TestCase):
    def test_extension_contract_and_null(self):
        c=MetricCatalog()
        c.register(Metric("vendor.extra","扩展","W",minimum=0))
        c.validate("vendor.extra",None)
        c.validate("vendor.extra",12)
        with self.assertRaises(DomainError):
            c.validate("vendor.extra",float("nan"))
        with self.assertRaises(DomainError):
            c.validate("ai_core",101)

    def test_weighted_aggregation_does_not_fill_gap(self):
        c=MetricCatalog()
        stamp=now()
        points=[{"sampled_at":stamp+timedelta(seconds=t),"value":v,"quality":q}
                for t,v,q in [(0,10,"ok"),(15,0,"unknown"),(600,100,"ok"),(615,100,"ok")]]
        result=c.aggregate("ai_core",points)
        self.assertEqual(result["valid_seconds"],30)
        self.assertEqual(result["value"],55)
