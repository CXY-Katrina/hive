"""Synthetic edge cases and sanitized table captures from physical Ascend nodes."""
import base64
import json
from pathlib import Path
import re
import unittest

from hive.domain import CommandResult
from hive.hardware import AscendAdapter, parse_info, parse_mapping, sections


BOOT = '11111111-2222-3333-4444-555555555555'
MAPPING = '''NPU ID                         Chip ID                        Chip Logic ID                  Chip Name
0                              0                              0                              Ascend 910B
0                              1                              -                              Mcu
1                              0                              1                              Ascend 910B
'''
METRICS = '''+---------------------------+---------------+----------------------------------------------------+
| npu-smi 24.1.0 Version: 24.1.0 |
| NPU Name | Health | Power(W) Temp(C) Hugepages-Usage(page) |
| Chip | Bus-Id | AICore(%) Memory-Usage(MB) HBM-Usage(MB) |
+===========================+===============+====================================================+
| 0 910B | OK | 70 40 0 / 0 |
| 0 | 0000:C1:00.0 | 0 0 / 0 0 / 65536 |
+===========================+===============+====================================================+
| 1 910B | OK | 70 40 0 / 0 |
| 0 | 0000:C2:00.0 | 25 0 / 0 200 / 65536 |
+===========================+===============+====================================================+
| NPU Chip | Process id | Process name | Process memory(MB) |
+===========================+===============+====================================================+
'''
BOTTOM = '+===========================+===============+====================================================+\n'
EMPTY = '| No running processes found in NPU 0 |\n| No running processes found in NPU 1 |\n'


def envelope(values):
    output = []
    for key, value in values.items():
        code, text = value if isinstance(value, tuple) else (0, value)
        output.append(f'HIVE\t{key}\t{code}\t{base64.b64encode(text.encode()).decode()}\n')
    return ''.join(output) + 'HIVE_END\n'


def stat(pid, start='1234'):
    return str(pid) + ' (python (worker)) S ' + ' '.join(['0'] * 18 + [start] + ['0'] * 5)


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def run(self, node, script, timeout=20):
        self.calls.append((node, script, timeout))
        return self.responses.pop(0)


class HardwareTests(unittest.TestCase):
    def network_nodes(self, generation='A3', count=3, cards=3):
        return [{'id': f'n{node}', 'generation': generation,
                 'devices': [{'id': f'd{node}-{card}', 'slot': f'{card}:0', 'command_id': str(card),
                              'extensions': {'hccn_id': str(card)}} for card in range(cards)]}
                for node in range(count)]

    def batch_transport(self, failure=None):
        class BatchTransport:
            def __init__(self):
                self.calls = []

            def run(self, node, script, timeout=20):
                self.calls.append((node, script, timeout))
                values = {}
                for key, ident, option in re.findall(r'^emit (\w+) timeout --signal=TERM --kill-after=1 5 hccn_tool -i (\d+) (.+)$', script, re.M):
                    if key.startswith('address_'):
                        value = f'ipaddr:10.0.{node["id"][1:]}.{int(ident) + 1}\nnetmask:255.255.255.0'
                    elif key.startswith('tls_'):
                        value = 'tls switch: 0'
                    else:
                        value = 'This pkt ping success'
                    values[key] = value
                if failure:
                    failure(node, values)
                return CommandResult(envelope(values), '', 0)
        return BatchTransport()

    def test_batch_network_caches_endpoints_and_bounds_scripts(self):
        transport = self.batch_transport()
        rows = AscendAdapter(transport).connectivity_many(self.network_nodes())
        self.assertEqual(len(rows), 54)
        self.assertEqual(len({(row['source_device'], row['target_device']) for row in rows}), 54)
        self.assertTrue(all(row['status'] == 'passed' and row['source_node'] != row['target_node'] for row in rows))
        scripts = '\n'.join(script for _, script, _ in transport.calls)
        self.assertEqual(len(re.findall(r'^emit address_', scripts, re.M)), 9)
        self.assertEqual(len(re.findall(r'^emit tls_', scripts, re.M)), 9)
        self.assertEqual(len(re.findall(r'^emit ping_', scripts, re.M)), 54)
        self.assertEqual(len(transport.calls), 18)
        for _, script, timeout in transport.calls:
            self.assertLessEqual(len(re.findall(r'^emit ping_', script, re.M)), 4)
            self.assertLessEqual(timeout, 53)
            self.assertIn('--kill-after=1 5 hccn_tool', script)
        self.assertIn('-vnic -g', scripts)
        self.assertIn('-hccs_ping -g address', scripts)
        self.assertNotIn('address 255.255.255.0', scripts)

    def test_batch_endpoint_timeout_blocks_only_affected_pairs(self):
        def fail(node, values):
            if node['id'] == 'n0' and 'address_0' in values:
                values['address_0'] = (124, '')
        rows = AscendAdapter(self.batch_transport(fail)).connectivity_many(self.network_nodes())
        affected = [row for row in rows if 'd0-0' in (row['source_device'], row['target_device'])]
        self.assertEqual(len(affected), 12)
        self.assertTrue(all(row['status'] == 'unknown' for row in affected))
        self.assertEqual(sum(row['status'] == 'passed' for row in rows), 42)

    def test_batch_missing_ping_and_tls_mismatch_do_not_pass(self):
        def missing(node, values):
            values.pop('ping_0', None)
        rows = AscendAdapter(self.batch_transport(missing)).connectivity_many(self.network_nodes(count=2, cards=1))
        self.assertEqual([row['status'] for row in rows], ['unknown', 'unknown'])

        def mismatch(node, values):
            if node['id'] == 'n0' and 'tls_0' in values:
                values['tls_0'] = 'tls switch: 1'
        transport = self.batch_transport(mismatch)
        rows = AscendAdapter(transport).connectivity_many(self.network_nodes(count=2, cards=1))
        self.assertEqual([row['status'] for row in rows], ['failed', 'failed'])
        self.assertTrue(all('emit ping_' not in script for _, script, _ in transport.calls))

    def test_batch_unsupported_generation_and_aliased_mapping_do_not_probe(self):
        transport = self.batch_transport()
        rows = AscendAdapter(transport).connectivity_many(self.network_nodes('A5', count=2, cards=1))
        self.assertTrue(all(row['status'] == 'unknown' for row in rows))
        self.assertEqual(transport.calls, [])
        nodes = self.network_nodes(count=2, cards=2)
        nodes[0]['devices'][1]['extensions']['hccn_id'] = '0'
        rows = AscendAdapter(transport).connectivity_many(nodes)
        self.assertTrue(all(row['status'] == 'unknown' for row in rows))
        self.assertTrue(all('emit ping_' not in script for _, script, _ in transport.calls))

    def test_batch_a2_uses_ip_and_ping_commands(self):
        transport = self.batch_transport()
        rows = AscendAdapter(transport).connectivity_many(self.network_nodes('A2', count=2, cards=1))
        self.assertTrue(all(row['status'] == 'passed' for row in rows))
        scripts = '\n'.join(script for _, script, _ in transport.calls)
        self.assertIn('-ip -g', scripts)
        self.assertIn('-ping -g address', scripts)
        self.assertNotIn('-vnic', scripts)
        self.assertNotIn('-hccs_ping', scripts)

    def test_batch_truncated_envelope_never_uses_partial_success(self):
        base = self.batch_transport()

        class TruncatedTransport:
            def run(self, node, script, timeout=20):
                result = base.run(node, script, timeout)
                if 'emit ping_' in script:
                    return CommandResult(result.stdout.removesuffix('HIVE_END\n'), '', 0)
                return result

        rows = AscendAdapter(TruncatedTransport()).connectivity_many(self.network_nodes(count=2, cards=1))
        self.assertEqual([row['status'] for row in rows], ['unknown', 'unknown'])

    def test_batch_input_limits_and_duplicate_identity_fail_before_ssh(self):
        transport = self.batch_transport()
        adapter = AscendAdapter(transport)
        for nodes in (self.network_nodes(count=65, cards=0), self.network_nodes(count=2, cards=129),
                      self.network_nodes(count=1) * 2):
            with self.assertRaises(ValueError):
                adapter.connectivity_many(nodes)
        nodes = self.network_nodes(count=2, cards=1)
        nodes[1]['devices'][0]['id'] = nodes[0]['devices'][0]['id']
        with self.assertRaises(ValueError):
            adapter.connectivity_many(nodes)
        self.assertEqual(transport.calls, [])

    def observed(self):
        return json.loads((Path(__file__).parent / 'fixtures/ascend/observed-npu-smi.json').read_text(encoding='utf-8'))['captures']

    def test_observed_dual_chip_tables_preserve_all_devices_and_host_pids(self):
        for capture in self.observed():
            with self.subTest(capture=capture['capture']):
                mapping = parse_mapping(capture['mapping'])
                self.assertEqual(mapping, {(str(npu), str(chip)): str(npu * 2 + chip)
                                           for npu in range(8) for chip in range(2)})
                samples, processes = parse_info(capture['info'], mapping)
                self.assertEqual(len(samples), 16)
                self.assertEqual(len(processes), capture['expected_process_count'])
                self.assertTrue(all(sample.health == 'OK' and sample.memory_total == 65536 * 1024**2
                                    for sample in samples.values()))
                self.assertTrue(all(pid >= 20001 for pid in processes))

    def test_observed_container_pid_does_not_replace_host_pid(self):
        capture = self.observed()[0]
        info = re.sub(r'\|\s*NA\s*\|', '| 42 |', capture['info'])
        _, processes = parse_info(info, parse_mapping(capture['mapping']))
        self.assertNotIn(42, processes)
        self.assertEqual(processes[20001]['keys'], [('0', '0')])

    def test_observed_missing_metrics_or_process_column_stays_unknown(self):
        capture = self.observed()[0]
        broken_tables = [
            re.sub(r'(0000:[A-Fa-f0-9:.]+\s*\|)\s*\d+', r'\1 N/A', capture['info'], count=1),
            re.sub(r'\|\s*NA\s*\|', '|', capture['info'], count=1),
            re.sub(r'^\|\s*1\s+1\s*\|.*\n', '', capture['info'], count=1, flags=re.M),
            re.sub(r'^\| No running processes found in NPU 7[^\n]*\n', '', capture['info'], flags=re.M),
        ]
        for info in broken_tables:
            with self.subTest(info=info[-120:]):
                result = CommandResult(envelope({'boot': BOOT, 'boot_after': BOOT,
                                                'mapping': capture['mapping'], 'info': info}), '', 0)
                snapshot = AscendAdapter(FakeTransport(result)).collect({})
                self.assertEqual(snapshot.quality, 'unknown')
                self.assertTrue(all(not sample.process_complete for sample in snapshot.devices))

    def test_observed_unavailable_or_duplicate_physical_id_is_rejected(self):
        capture = self.observed()[0]
        for physical in ('-', '0'):
            mapping = re.sub(r'(?m)^(\s*0\s+1\s+1\s+)1(\s+Ascend910)',
                             lambda match: match[1] + physical + match[2], capture['mapping'], count=1)
            with self.subTest(physical=physical), self.assertRaises(ValueError):
                parse_mapping(mapping)

    def response(self, rows=EMPTY, mapping=MAPPING):
        return CommandResult(envelope({'boot': BOOT, 'boot_after': BOOT, 'mapping': mapping,
                                       'info': METRICS + rows + BOTTOM,
                                       'driver': 'Version=24.1.0', 'firmware': (1, '')}), '', 0)

    def test_real_mapping_layout_skips_mcu_and_preserves_identity(self):
        self.assertEqual(parse_mapping(MAPPING), {('0', '0'): '0', ('1', '0'): '1'})

    def test_unavailable_mapping_is_not_empty(self):
        with self.assertRaises(ValueError):
            parse_mapping(MAPPING.replace('0                              0', '-1                             0', 1))

    def test_duplicate_logical_id_rejected(self):
        with self.assertRaises(ValueError):
            parse_mapping(MAPPING.replace('1                              Ascend', '0                              Ascend'))

    def test_complete_idle_table_and_metrics(self):
        snapshot = AscendAdapter(FakeTransport(self.response())).collect({})
        self.assertEqual(snapshot.quality, 'ok')
        self.assertEqual(snapshot.devices[1].ai_core, 25)
        self.assertEqual(snapshot.devices[1].memory_used, 200 * 1024**2)
        self.assertEqual(snapshot.devices[0].health, 'OK')
        self.assertTrue(all(device.process_complete for device in snapshot.devices))

    def test_missing_device_process_row_is_unknown(self):
        snapshot = AscendAdapter(FakeTransport(self.response('| No running processes found in NPU 0 |\n'))).collect({})
        self.assertEqual(snapshot.quality, 'unknown')

    def test_truncated_table_and_envelope_rejected(self):
        with self.assertRaises(ValueError):
            parse_info(METRICS + EMPTY, parse_mapping(MAPPING))
        with self.assertRaises(ValueError):
            sections(envelope({'boot': BOOT})[:-5])

    def test_unknown_core_is_not_zero(self):
        result = self.response()
        decoded = sections(result.stdout)
        decoded['info'] = (0, decoded['info'][1].replace('25 0 / 0', 'N/A 0 / 0'))
        snapshot = AscendAdapter(FakeTransport(CommandResult(envelope(decoded), '', 0))).collect({})
        self.assertEqual(snapshot.quality, 'unknown')

    def test_command_failure_does_not_replay(self):
        transport = FakeTransport(CommandResult('', 'timeout', 124))
        self.assertEqual(AscendAdapter(transport).collect({}).quality, 'unknown')
        self.assertEqual(len(transport.calls), 1)

    def identity(self, pid, cid, after='1234', rows=None):
        return {'boot': BOOT, 'boot_after': BOOT, f'stat_{pid}': stat(pid),
                f'stat_after_{pid}': stat(pid, after), f'cgroup_{pid}': '0::/system.slice/docker-' + cid + '.scope',
                'docker_state': 'ok', 'docker': cid + '\tactual-container-name', 'host_pidns': 'pid:[1]',
                f'pidns_{pid}': 'pid:[2]', 'info_after': METRICS + (rows or f'| 0 0 | {pid} | python | 50 |\n| 1 0 | {pid} | python | 100 |\n') + BOTTOM}

    def test_external_pid_maps_to_all_cards_and_actual_container(self):
        cid = 'a' * 64
        rows = '| 0 0 | 2345 | python | 50 |\n| 1 0 | 2345 | python | 100 |\n'
        transport = FakeTransport(self.response(rows), CommandResult(envelope(self.identity(2345, cid)), '', 0))
        snapshot = AscendAdapter(transport).collect({})
        self.assertEqual(snapshot.quality, 'ok')
        for device in snapshot.devices:
            process = device.processes[0]
            self.assertEqual(process.device_ids, ['0:0', '1:0'])
            self.assertEqual(process.container_id, cid)
            self.assertEqual(process.container_name, 'actual-container-name')
            self.assertEqual(process.start_time, '1234')

    def test_reused_pid_blocks_sample(self):
        rows = '| 0 0 | 2345 | python | 50 |\n| No running processes found in NPU 1 |\n'
        transport = FakeTransport(self.response(rows), CommandResult(envelope(self.identity(2345, 'a' * 64, '9999', rows)), '', 0))
        snapshot = AscendAdapter(transport).collect({})
        self.assertEqual(snapshot.quality, 'unknown')
        self.assertTrue(all(not device.process_complete for device in snapshot.devices))

    def test_pid_disappearing_from_npu_after_identity_is_unknown(self):
        rows = '| 0 0 | 2345 | python | 50 |\n| No running processes found in NPU 1 |\n'
        transport = FakeTransport(self.response(rows), CommandResult(envelope(self.identity(2345, 'a' * 64, rows=EMPTY)), '', 0))
        self.assertEqual(AscendAdapter(transport).collect({}).quality, 'unknown')

    def test_unknown_docker_is_not_host(self):
        data = {'cgroup_2345': (0, '0::/system.slice/service.scope'), 'docker_state': (0, 'unknown')}
        self.assertEqual(AscendAdapter._container(2345, data), (None, None, 'unknown'))

    def test_top_membership_fallback_uses_host_pid(self):
        cid = 'b' * 64
        data = {'cgroup_2345': (0, '0::/unrecognized'), 'docker_state': (0, 'ok'),
                'docker': (0, cid + '\tactual'), 'top_' + cid: (0, 'PID\n2345\n')}
        self.assertEqual(AscendAdapter._container(2345, data), (cid, 'actual', 'docker'))

    def test_a5_probe_stays_unsupported(self):
        transport = FakeTransport()
        result = AscendAdapter(transport).connectivity({'generation': 'A5'}, {'generation': 'A5'}, {}, {})
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(transport.calls, [])

    def test_a3_requires_real_hccn_mapping(self):
        result = AscendAdapter(FakeTransport()).connectivity({'generation': 'A3'}, {'generation': 'A3'},
                                                          {'slot': '0:0', 'command_id': '0'}, {})
        self.assertEqual(result['status'], 'unknown')

    def test_tls_mismatch_fails_even_when_ping_passes(self):
        transport = FakeTransport(CommandResult(envelope({'address': 'ipaddr:10.0.0.2', 'tls': 'tls switch: 1'}), '', 0),
                                  CommandResult(envelope({'tls': 'tls switch: 0', 'ping': 'This pkt ping success'}), '', 0))
        result = AscendAdapter(transport).connectivity({'generation': 'A2'}, {'generation': 'A2'},
                                                      {'slot': '0:0', 'command_id': '0'}, {'slot': '1:0', 'command_id': '1'})
        self.assertEqual(result['status'], 'failed')

    def test_runtime_uses_logical_ids(self):
        self.assertEqual(AscendAdapter(None).environment([{'logical_id': '4'}, {'logical_id': '7'}]),
                         {'ASCEND_RT_VISIBLE_DEVICES': '4,7'})

    def test_official_hccn_ip_netmask_and_bracket_tls_format(self):
        tls = 'dev_id:0, tls switch[0](0:disable, 1:enable), tls alarm time threshold[60]days'
        transport = FakeTransport(CommandResult(envelope({'address': 'ipaddr:10.0.0.2\nnetmask:255.255.255.0', 'tls': tls}), '', 0),
                                  CommandResult(envelope({'tls': tls, 'ping': 'This pkt ping success'}), '', 0))
        result = AscendAdapter(transport).connectivity({'generation': 'A2'}, {'generation': 'A2'},
                                                      {'slot': '0:0', 'command_id': '0'}, {'slot': '1:0', 'command_id': '1'})
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['peer_address'], '10.0.0.2')
        self.assertNotIn('255.255.255.0', transport.calls[1][1])


if __name__ == '__main__':
    unittest.main()
