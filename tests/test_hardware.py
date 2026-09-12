"""Synthetic vendor-format fixtures, NOT acceptance results from physical Ascend nodes."""
import base64
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
