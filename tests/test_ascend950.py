from pathlib import Path
import re
import unittest
from hive.hardware import parse_mapping, parse_info

ROOT = Path(__file__).parent / 'fixtures'

class Ascend950Tests(unittest.TestCase):
    def setUp(self):
        self.mapping_text = (ROOT / 'ascend950-mapping.txt').read_text()
        self.info = (ROOT / 'ascend950-info.txt').read_text()
        self.mapping = parse_mapping(self.mapping_text)

    def test_real_capture(self):
        samples, processes = parse_info(self.info, self.mapping)
        self.assertEqual(len(samples), 8)
        self.assertEqual(self.mapping[('7', '0')], '7')
        self.assertFalse(processes)
        self.assertTrue(all(s.memory_total == 98304 * 1024**2 for s in samples.values()))
        self.assertTrue(all(s.ai_core is None for s in samples.values()))
        self.assertTrue(all('npu_utilization' in s.extensions for s in samples.values()))

    def test_host_pid_column(self):
        text = re.sub(r'\|\s*No running processes found in NPU\s+0\s*\|',
                      '| 0 | 12345 | VLLMWorker_TP | 302 | 9 |', self.info)
        _, processes = parse_info(text, self.mapping)
        self.assertEqual(processes[12345]['keys'], [('0', '0')])
        self.assertNotIn(9, processes)

    def test_duplicate_and_remapped_identity_rejected(self):
        row = self.mapping_text.splitlines()[1]
        with self.assertRaises(ValueError):
            parse_mapping(self.mapping_text + '\n' + row)
        with self.assertRaises(ValueError):
            parse_mapping('NPU ID Slot ID Chip ID Chip Phy-ID Chip Name\n0 0 0 7 Ascend950DT')

    def test_truncated_process_table_rejected(self):
        with self.assertRaises(ValueError):
            parse_info(self.info.rstrip().rsplit('\n', 1)[0], self.mapping)
