"""Benchmark safety tests use a disposable MySQL DB and no real SSH/NPU work."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
from hive.api import create_app
from hive.compute_benchmark import ComputeBenchmark, benchmark_script, parse_fp16
from hive.domain import CommandResult, DomainError, now, encode
from tests import test_integration as integration


def normal_table(logical_id, tflops='123.45'):
    # Huawei Toolbox documented normal-column layout, synthetic values only.
    return ('+--------+---------------+--------------+-------------+----------+\n'
            '| Device | Execute Times | Duration(ms) | TFLOPS@FP16 | Power(W) |\n'
            f'| {logical_id} | 1000000 | 31.7 | {tflops} | 210.5 |\n')


class OutputParsing(unittest.TestCase):
    def test_documented_normal_table_and_plain_whitespace(self):
        self.assertEqual(parse_fp16(normal_table('7'), '7'), 123.45)
        self.assertEqual(parse_fp16(normal_table('7').replace('|', ' '), '7'), 123.45)
        documented = ('Device Execute Times Duration(ms) TFLOPS@FP16 Power(W)\n'
                      '0 192,000,000 1536 262.144 242.899994\n')
        self.assertEqual(parse_fp16(documented, '0'), 262.144)
        with self.assertRaises(DomainError):
            parse_fp16(documented.replace('192,000,000', '192,00,000'), '0')

    def test_wrong_id_missing_column_duplicate_and_nonfinite_fail_closed(self):
        for output in (normal_table('8'), normal_table('7').replace('TFLOPS@FP16', 'TFLOPS@BF16'),
                       normal_table('7') * 2, normal_table('7', 'NaN'), normal_table('7', '-10'),
                       normal_table('7', '0'), 'TFLOPS 752'):
            with self.subTest(output=output), self.assertRaises(DomainError):
                parse_fp16(output, '7')

    def test_script_only_numeric_device_id_and_bounded_execution(self):
        script = benchmark_script('15')
        self.assertIn('-d 15 --et 10 --fmt normal -q', script)
        self.assertIn('timeout --signal=TERM --kill-after=2 60s', script)
        self.assertNotIn('pkill', script)
        for value in ('1;true', '-1', '1 2', '', None, '01'):
            with self.assertRaises(DomainError):
                benchmark_script(value)

    def test_worker_continues_sampling_and_scheduling_during_benchmark(self):
        from hive.config import Settings
        from hive.worker import Worker
        started, release, collected = threading.Event(), threading.Event(), threading.Event()
        def measure(_):
            started.set()
            release.wait(5)
        resources = SimpleNamespace(expire_queued=Mock())
        services = SimpleNamespace(settings=Settings(), resources=resources,
            db=SimpleNamespace(all=lambda *a: [], one=lambda *a: None),
            reporting=SimpleNamespace(maintain=Mock()),
            compute_benchmark=SimpleNamespace(pending=lambda: ['node'], run=measure),
            inventory=SimpleNamespace(list_nodes=lambda: [{'id': 'node'}]),
            telemetry=SimpleNamespace(collect_node=lambda _: collected.set()))
        worker = Worker(services)
        collector = threading.Thread(target=worker.collect_loop)
        try:
            worker.tick()
            self.assertTrue(started.wait(1))
            collector.start()
            self.assertTrue(collected.wait(1))
            worker.tick()
            self.assertEqual(resources.expire_queued.call_count, 2)
            self.assertFalse(worker.benchmark_future.done())
        finally:
            release.set()
            worker.stop.set()
            if collector.ident:
                collector.join(2)
            for value in vars(worker).values():
                if hasattr(value, 'shutdown'):
                    value.shutdown(wait=True)


@unittest.skipUnless(os.getenv('HIVE_TEST_MYSQL_PORT'), 'Requires isolated MySQL test service')
class BenchmarkIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        integration.MySQLIntegration.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        integration.MySQLIntegration.tearDownClass.__func__(cls)

    def setUp(self):
        integration.MySQLIntegration.setUp(self)
        self.collector = SimpleNamespace(collect_node=Mock())
        self.transport = SimpleNamespace(run=Mock(side_effect=self.result))
        self.benchmark = ComputeBenchmark(self.db, self.settings, self.inventory, self.collector, self.transport)

    @staticmethod
    def result(node, script, timeout):
        import re
        logical_id = re.search(r'-d ([0-9]+) --et', script)[1]
        return CommandResult(normal_table(logical_id, str(120 + int(logical_id))), '', 0)

    def node(self, cards=2):
        node = integration.MySQLIntegration.node(self, cards=cards)
        for device in node['devices']:
            self.inventory.confirm_baseline(device['id'], self.admin)
        self.inventory.record_probe(node['id'], metadata={'hardware_profile': {
            'boot_id': node['boot_id'], 'ascend_dmi': {'available': True}}})
        return self.inventory.get(node['id'])

    def state(self, node):
        return self.inventory.get(node['id'])['metadata']['compute_benchmark']

    def due(self, node):
        with self.db.transaction() as c:
            c.execute("UPDATE nodes SET metadata=JSON_SET(metadata,'$.compute_benchmark.recover_after',%s) WHERE id=%s",
                      (str(now() - timedelta(seconds=1)), node['id']))

    def test_success_per_logical_device_preserves_original_maintenance(self):
        node = self.node()
        with self.db.transaction() as c:
            c.execute("UPDATE devices SET command_id='0',chip_id=slot,logical_id=CAST(slot AS UNSIGNED)+6 WHERE node_id=%s", (node['id'],))
        self.inventory.update(node['id'], self.admin, {'maintenance': True})
        queued = self.benchmark.request(node['id'], self.admin)
        self.assertEqual(queued['status'], 'QUEUED')
        self.benchmark.run(node['id'])
        state = self.state(node)
        self.assertEqual(state['status'], 'SUCCEEDED')
        self.assertEqual([d['logical_id'] for d in state['devices']], ['6', '7'])
        self.assertEqual((state['min_tflops'], state['max_tflops']), (126, 127))
        self.assertTrue(self.inventory.get(node['id'])['maintenance'])
        self.assertEqual(self.transport.run.call_count, 2)

    def test_benchmark_excludes_allocation_and_disallows_unlock_or_duplicate(self):
        node = self.node()
        self.benchmark.request(node['id'], self.admin)
        with self.assertRaises(DomainError):
            self.inventory.update(node['id'], self.admin, {'maintenance': False})
        with self.assertRaises(DomainError):
            self.benchmark.request(node['id'], self.admin)
        request = integration.MySQLIntegration.request(self)
        self.assertIsNone(self.resources.reserve(request['id']))
        self.benchmark.run(node['id'])
        self.assertFalse(self.inventory.get(node['id'])['maintenance'])
        self.assertIsNotNone(self.resources.reserve(request['id']))

    def test_concurrent_allocator_or_benchmark_has_one_winner(self):
        node = self.node()
        request = integration.MySQLIntegration.request(self)
        def benchmark():
            try:
                return self.benchmark.request(node['id'], self.admin)
            except DomainError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(benchmark)
            b = pool.submit(self.resources.reserve, request['id'])
            self.assertEqual(sum(value is not None for value in (a.result(), b.result())), 1)

    def test_busy_missing_baseline_stale_and_missing_tool_refused(self):
        node = self.node()
        updates = ["processes='[{\"pid\":42}]'", 'ai_core=1', 'memory_used=104857600',
                   'baseline_confirmed=FALSE', "quality='unknown'", 'process_complete=FALSE',
                   "sampled_at='2001-01-01'", "logical_id='bad'"]
        for expression in updates:
            with self.subTest(expression=expression):
                with self.db.transaction() as c:
                    c.execute('UPDATE devices SET ' + expression + ' WHERE node_id=%s', (node['id'],))
                with self.assertRaises(DomainError):
                    self.benchmark.request(node['id'], self.admin)
                with self.db.transaction() as c:
                    c.execute("UPDATE devices SET processes='[]',ai_core=0,memory_used=0,baseline_confirmed=TRUE,quality='ok',process_complete=TRUE,sampled_at=%s,logical_id=slot WHERE node_id=%s", (now(), node['id']))
        self.inventory.record_probe(node['id'], metadata={'hardware_profile': {'ascend_dmi': {'available': False}}})
        with self.assertRaises(DomainError):
            self.benchmark.request(node['id'], self.admin)
        self.transport.run.assert_not_called()

    def test_busy_during_actual_preflight_never_executes(self):
        node = self.node()
        self.benchmark.request(node['id'], self.admin)
        def busy(_):
            with self.db.transaction() as c:
                c.execute("UPDATE devices SET processes='[{\"pid\":42}]' WHERE node_id=%s", (node['id'],))
        self.collector.collect_node.side_effect = busy
        self.benchmark.run(node['id'])
        self.transport.run.assert_not_called()
        self.assertEqual(self.state(node)['status'], 'RECOVERING')
        self.assertTrue(self.inventory.get(node['id'])['maintenance'])

    def test_failed_command_and_restart_wait_bounded_remote_timeout_no_retry(self):
        node = self.node()
        self.benchmark.request(node['id'], self.admin)
        self.transport.run.side_effect = OSError('Disconnected')
        self.benchmark.run(node['id'])
        state = self.state(node)
        self.assertEqual(state['status'], 'RECOVERING')
        self.assertTrue(self.inventory.get(node['id'])['maintenance'])
        restarted = ComputeBenchmark(self.db, self.settings, self.inventory, self.collector, self.transport)
        restarted.run(node['id'])
        self.assertTrue(self.inventory.get(node['id'])['maintenance'])
        self.due(node)
        self.collector.collect_node.side_effect = OSError('Temporary telemetry failure')
        restarted.run(node['id'])
        self.assertIn('recovery_reason', self.state(node))
        self.collector.collect_node.side_effect = None
        self.due(node)
        restarted.run(node['id'])
        self.assertEqual(self.state(node)['status'], 'FAILED')
        self.assertNotIn('recovery_reason', self.state(node))
        self.assertFalse(self.inventory.get(node['id'])['maintenance'])
        self.transport.run.assert_called_once()

    def test_interrupted_running_not_reexecuted_and_mapping_change_invalidates(self):
        node = self.node()
        state = self.benchmark.request(node['id'], self.admin)
        state.update(status='RUNNING', recover_after=str(now() - timedelta(seconds=1)),
                     all_devices_succeeded=True, devices=[{'tflops': 123}])
        with self.db.transaction() as c:
            c.execute("UPDATE nodes SET metadata=JSON_SET(metadata,'$.compute_benchmark',CAST(%s AS JSON)) WHERE id=%s", (encode(state), node['id']))
            c.execute("UPDATE devices SET command_id='88' WHERE node_id=%s", (node['id'],))
        self.benchmark.run(node['id'])
        self.assertEqual(self.state(node)['status'], 'FAILED')
        self.assertFalse(self.inventory.get(node['id'])['maintenance'])
        self.transport.run.assert_not_called()

    def test_failed_output_kept_bounded_and_password_redacted(self):
        node = self.node()
        self.benchmark.request(node['id'], self.admin)
        self.transport.run.side_effect = None
        self.transport.run.return_value = CommandResult('', 'test-secret library unavailable ' + 'x' * 6000, 127)
        self.benchmark.run(node['id'])
        state = self.state(node)
        self.assertNotIn('test-secret', state['last_output'])
        self.assertIn('library unavailable', state['last_output'])
        self.assertLessEqual(len(state['last_output']), 4096)
        self.due(node)
        self.benchmark.run(node['id'])
        self.assertEqual(self.state(node)['status'], 'FAILED')

    def test_api_admin_permission_and_accepted_response(self):
        node = self.node()
        services = SimpleNamespace(db=self.db, identity=self.identity, inventory=self.inventory,
                                   compute_benchmark=self.benchmark)
        with TestClient(create_app(services, self.settings)) as client:
            endpoint = f"/api/nodes/{node['id']}/compute-benchmark"
            self.assertEqual(client.post(endpoint).status_code, 401)
            client.post('/api/session', json={'username': 'alice'})
            self.assertEqual(client.post(endpoint).status_code, 403)
            client.post('/api/session', json={'username': 'admin', 'password': 'test-admin'})
            response = client.post(endpoint)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()['status'], 'QUEUED')
            self.assertEqual(client.patch(f"/api/nodes/{node['id']}", json={'maintenance': False}).status_code, 409)
        self.transport.run.assert_not_called()
