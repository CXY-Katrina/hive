import unittest
from unittest.mock import Mock
from pydantic import ValidationError
from hive.domain import CommandResult
from hive.hardware_profile import AscendHardwareProfile, soc_version
from hive.schemas import NodeUpdate
from tests.test_hardware import envelope, BOOT


class HardwareProfileTests(unittest.TestCase):
    def test_soc_uses_npu_suffix_not_chip_revision_or_board_name(self):
        for suffix in ('9362', '9382'):
            self.assertEqual(soc_version(f'Chip Name : Ascend910\nNPU Name : {suffix}\nChip Version : V1'), 'Ascend910_' + suffix)
        self.assertEqual(soc_version('Chip Name : 910B1\nChip Type : Ascend'), 'Ascend910B1')
        self.assertEqual(soc_version('Chip Name : Ascend950\nNPU Name : 9599'), 'Ascend950_9599')

    def test_missing_or_conflicting_chip_identity_is_not_guessed(self):
        for raw in ('Chip Name : Ascend910', 'Product Name : IT22HMDA_4_S',
                    'Chip Name : Ascend910\nNPU Name : 9362\nNPU Name : 9382'):
            with self.assertRaises(ValueError): soc_version(raw)

    def profile(self, chip, boot=BOOT):
        transport = Mock()
        transport.run.side_effect = [
            CommandResult(envelope({'system': 'Atlas 800I A3', 'board': 'Product Name : IT22HMDA_4_S'}), '', 0),
            CommandResult(envelope({'boot': boot, 'boot_after': boot, 'chip_0': chip}), '', 0)]
        node = {'boot_id': BOOT, 'devices': [{'slot': '0:0', 'command_id': '0', 'chip_id': '0'}]}
        return AscendHardwareProfile(transport).collect(node)

    def test_profile_persists_exact_sources_without_guessing_compute(self):
        profile = self.profile('NPU ID : 0\nChip ID : 0\nChip Name : Ascend910\nNPU Name : 9362\nChip Version : V1')
        self.assertEqual(profile['quality'], 'ok')
        self.assertEqual(profile['soc_versions'], ['Ascend910_9362'])
        self.assertEqual(profile['system_product'], 'Atlas 800I A3')
        self.assertEqual(profile['board_product'], 'IT22HMDA_4_S')
        self.assertNotIn('fp16_tflops_per_module', profile)

    def test_failed_chip_query_is_partial_and_does_not_invent_soc(self):
        profile = self.profile((1, 'not supported'))
        self.assertEqual(profile['quality'], 'partial')
        self.assertEqual(profile['soc_versions'], [])

    def test_wrong_device_identity_or_reboot_is_not_accepted(self):
        raw = 'NPU ID : 1\nChip ID : 0\nChip Name : Ascend910\nNPU Name : 9362'
        self.assertEqual(self.profile(raw)['quality'], 'partial')
        self.assertEqual(self.profile(raw, boot='changed')['quality'], 'unknown')

    def test_compute_registration_requires_source_valid_number_and_one_action(self):
        for value in ({'fp16_tflops_per_module': 752, 'source': ' '},
                      {'fp16_tflops_per_module': float('nan'), 'source': 'spec'},
                      {'fp16_tflops_per_module': 0, 'source': 'spec'}):
            with self.assertRaises(ValidationError): NodeUpdate(compute_spec=value)
        with self.assertRaises(ValidationError):
            NodeUpdate(compute_spec={'fp16_tflops_per_module': 752, 'source': 'spec'}, clear_compute_spec=True)
