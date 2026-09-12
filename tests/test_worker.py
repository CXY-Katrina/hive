"""Preflight integration boundaries with independent remote and collected state."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import unittest

from hive.config import Settings
from hive.domain import DomainError, now
from hive.inventory import device_status
from hive.worker import Worker


class PreflightResources:
    def __init__(self, state, events):
        self.state, self.events = state, events
        self.delivered = False
        self.aborts = []

    def devices(self, request_id):
        return [deepcopy(self.state['latest'])]

    def deliver(self, request_id, epoch, *args, **kwargs):
        self.events.append('deliver')
        candidate = dict(self.state['latest'])
        candidate.pop('request_id', None)
        if device_status(candidate) != 'available':
            raise DomainError('Latest device state prevents delivery')
        self.delivered = True
        return True

    def abort_reservation(self, request_id, epoch, reason):
        self.aborts.append((request_id, epoch, reason))


class WorkerPreflightTests(unittest.TestCase):
    def setup_worker(self, during_probe=None, probe_error=None, collect_error=None):
        card = dict(id='device-1', node_id='node-1', slot='0:0', command_id='0', chip_id='0', logical_id='0',
                    boot_id='boot-1', sampled_at=now(), quality='ok', health='OK', process_complete=True,
                    ai_core=0, memory_used=0, memory_total=64 * 1024**3, baseline_bytes=0,
                    maintenance=False, request_id='request-1', epoch=2, processes=[])
        state = {'remote': deepcopy(card), 'latest': deepcopy(card)}
        events = []
        resources = PreflightResources(state, events)

        def collect_node(node_id):
            events.append('collect')
            if collect_error:
                raise collect_error
            state['latest'] = deepcopy(state['remote'])
            state['latest']['sampled_at'] = now()

        def check_selection(devices, **kwargs):
            events.append('probe')
            # Simulate elapsed probe time without sleeping or contacting actual nodes.
            state['latest']['sampled_at'] = now() - timedelta(minutes=2)
            if during_probe:
                during_probe(state['remote'])
            if probe_error:
                raise probe_error
            return True

        services = SimpleNamespace(settings=Settings(), resources=resources,
                    telemetry=SimpleNamespace(collect_node=collect_node),
                    probes=SimpleNamespace(check_selection=check_selection))
        worker = Worker(services)
        self.addCleanup(worker.probe_pool.shutdown, wait=True)
        # Additional executors may be introduced without changing these preflight contracts.
        for name, value in vars(worker).items():
            if name != 'probe_pool' and hasattr(value, 'shutdown'):
                self.addCleanup(value.shutdown, wait=True)
        request = dict(id='request-1', version=2, status='RESERVED', spec={'generation': 'A2'})
        return worker, resources, state, events, request

    def test_external_process_started_during_probe_prevents_delivery(self):
        def start_external(card):
            card.update(ai_core=99, processes=[{'pid': 2345}])
        worker, resources, _, events, request = self.setup_worker(start_external)
        worker.preflight(request)
        self.assertFalse(resources.delivered)
        self.assertEqual(len(resources.aborts), 1)
        self.assertIn('collect', events[events.index('probe') + 1:])

    def test_unknown_sample_after_probe_prevents_delivery(self):
        worker, resources, _, _, request = self.setup_worker(lambda card: card.update(quality='unknown', ai_core=None))
        worker.preflight(request)
        self.assertFalse(resources.delivered)
        self.assertEqual(len(resources.aborts), 1)

    def test_node_reboot_after_probe_invalidates_verified_selection(self):
        worker, resources, _, _, request = self.setup_worker(lambda card: card.update(boot_id='boot-2'))
        worker.preflight(request)
        self.assertFalse(resources.delivered)
        self.assertEqual(len(resources.aborts), 1)

    def test_healthy_probe_recollects_before_delivering(self):
        worker, resources, _, events, request = self.setup_worker()
        worker.preflight(request)
        self.assertTrue(resources.delivered)
        self.assertEqual(resources.aborts, [])
        after_probe = events[events.index('probe') + 1:]
        self.assertIn('collect', after_probe)
        self.assertLess(after_probe.index('collect'), after_probe.index('deliver'))

    def test_failed_interconnect_aborts_without_delivery(self):
        worker, resources, _, events, request = self.setup_worker(probe_error=DomainError('NPU peer unreachable'))
        worker.preflight(request)
        self.assertFalse(resources.delivered)
        self.assertEqual(len(resources.aborts), 1)
        self.assertNotIn('deliver', events)

    def test_collector_failure_aborts_without_probing(self):
        worker, resources, _, events, request = self.setup_worker(collect_error=DomainError('Missing hardware snapshot'))
        worker.preflight(request)
        self.assertFalse(resources.delivered)
        self.assertEqual(len(resources.aborts), 1)
        self.assertNotIn('probe', events)

    def test_missing_ai_core_is_never_available(self):
        card = dict(sampled_at=now(), quality='ok', health='OK', process_complete=True,
                    ai_core=None, memory_used=0, baseline_bytes=0, processes=[])
        self.assertEqual(device_status(card), 'unknown')

    def test_control_poll_rotates_beyond_first_four_requests(self):
        worker,_,_,_,_=self.setup_worker()
        rows=[{'id':str(i),'execution_id':'task-'+str(i)} for i in range(8)]
        submitted=[]
        class ImmediateExecutor:
            def submit(self,fn,*args):
                submitted.append(args[0])
                return SimpleNamespace(done=lambda:True,result=lambda:None)
        worker.control_pool.shutdown()
        worker.control_pool=ImmediateExecutor()
        worker.s.db=SimpleNamespace(one=lambda *a:None,all=lambda sql,*a:list(rows) if 'LEFT JOIN executions' in sql else [])
        worker.s.reporting=SimpleNamespace(maintain=lambda:None)
        worker.s.resources.expire_queued=lambda:None
        worker.tick()
        worker.tick()
        self.assertEqual(submitted,[str(i) for i in range(8)])


if __name__ == '__main__':
    unittest.main()
