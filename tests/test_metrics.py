import unittest
from datetime import timedelta
from hive.metrics import MetricCatalog, Metric
from hive.domain import DomainError, now
from hive.telemetry import Telemetry
from hive.config import Settings
from unittest.mock import Mock


class MetricTests(unittest.TestCase):
    def test_history_exposes_extension_values_and_configured_gap_period(self):
        db = Mock()
        db.all.return_value = [{"sampled_at": now(), "extensions": '{"temperature":42}'}]
        telemetry = Telemetry(db, None, {}, None, Settings(sample_seconds=7))
        row = telemetry.history('device')[0]
        self.assertEqual(row['extensions'], {'temperature': 42})
        self.assertEqual(row['sample_interval_seconds'], 7)

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
