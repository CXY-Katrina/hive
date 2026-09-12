import unittest

from hive.hardware_versions import hdk_info


class HardwareVersionTests(unittest.TestCase):
    def test_package_release_has_priority_over_component_version(self):
        info = hdk_info((0, 'Version=7.35.23\npackage_version=26.0.rc1\nascendhal_version=7.35.23'), 'stamp')
        self.assertEqual(info['version'], '26.0.rc1')
        self.assertEqual(info['field'], 'package_version')
        self.assertEqual(info['quality'], 'ok')

    def test_legacy_version_fields(self):
        for key in ('Version', 'DriverVersion', 'version'):
            info = hdk_info((0, f'{key}=25.5.0\n'), 'stamp')
            self.assertEqual(info['version'], '25.5.0')
            self.assertEqual(info['field'], key)

    def test_unreadable_missing_invalid_or_conflicting_is_unknown(self):
        for result in ((1, 'Version=26.1.1'), (0, ''), (0, 'ascendhal_version=7.35.23'),
                       (0, 'Version=unknown'), (0, 'package_version=26.1.1\npackage_version=25.5.0'),
                       (0, 'package_version=\nVersion=25.5.0')):
            info = hdk_info(result, 'stamp')
            self.assertIsNone(info['version'])
            self.assertEqual(info['quality'], 'unknown')
            self.assertTrue(info['reason'])
