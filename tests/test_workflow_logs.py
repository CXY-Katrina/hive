"""Persistent log archive behavior across retries and bounded reads."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from hive.domain import DomainError
from hive.workflow_logs import LogArchive


KEY = 'a' * 64


class LogArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.archive = LogArchive(self.root)

    def test_binary_append_read_and_restart(self):
        data = '中文'.encode() + b'\x00\xff'
        written = self.archive.append(KEY, 0, data)
        self.assertEqual(written, {'next_offset': len(data), 'size': len(data), 'truncated': False})
        result = LogArchive(self.root).read(KEY, offset=2, limit=4)
        self.assertEqual(result['data'], data[2:6])
        self.assertEqual(result['next_offset'], 6)
        self.assertFalse(result['eof'])
        self.assertFalse(result['missing'])

    def test_replayed_and_overlapping_chunks_are_verified_and_not_duplicated(self):
        self.archive.append(KEY, 0, b'abcdef')
        self.assertEqual(self.archive.append(KEY, 0, b'abc')['next_offset'], 6)
        self.assertEqual(self.archive.append(KEY, 3, b'defghi')['next_offset'], 9)
        self.assertEqual(self.archive.read(KEY)['data'], b'abcdefghi')

    def test_conflicting_retry_or_gap_does_not_change_existing_bytes(self):
        self.archive.append(KEY, 0, b'abc')
        for offset, data in ((1, b'XX'), (4, b'd')):
            with self.assertRaises(DomainError):
                self.archive.append(KEY, offset, data)
        self.assertEqual(self.archive.read(KEY)['data'], b'abc')

    def test_capacity_is_enforced_and_reported_without_deleting_original_bytes(self):
        archive = LogArchive(self.root, max_bytes=10)
        archive.append(KEY, 0, b'abcdef')
        result = archive.append(KEY, 6, b'ghijklmnop')
        self.assertEqual(result, {'next_offset': 10, 'size': 10, 'truncated': True})
        self.assertEqual(archive.read(KEY)['data'], b'abcdefghij')
        self.assertTrue(archive.append(KEY, 10, b'ignored')['truncated'])

    def test_missing_is_distinct_from_archived_empty_log(self):
        self.assertTrue(self.archive.read(KEY)['missing'])
        self.archive.append(KEY, 0, b'')
        result = self.archive.read(KEY)
        self.assertFalse(result['missing'])
        self.assertEqual(result['data'], b'')
        self.assertTrue(result['eof'])

    def test_keys_and_ranges_cannot_select_arbitrary_files(self):
        for key in ('../outside', '/tmp/file', 'abc', 'A' * 64, '..' + 'a' * 62):
            with self.assertRaises(DomainError):
                self.archive.append(key, 0, b'x')
        for offset, limit in ((-1, 1), (True, 1), (0, 0), (0, 65537)):
            with self.assertRaises(DomainError):
                self.archive.read(KEY, offset=offset, limit=limit)

    def test_concurrent_duplicate_writers_remain_idempotent(self):
        def write(_):
            return LogArchive(self.root).append(KEY, 0, b'one')
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(write, range(16)))
        self.assertTrue(all(row['size'] == 3 for row in results))
        self.assertEqual(self.archive.read(KEY)['data'], b'one')

    def test_file_symlink_is_rejected(self):
        target = self.root / 'outside'
        target.write_bytes(b'untouched')
        link = self.root / 'workflow-logs' / (KEY + '.log')
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest('Symlinks are unavailable')
        for operation in (lambda: self.archive.append(KEY, 0, b'x'), lambda: self.archive.read(KEY)):
            with self.assertRaises(DomainError):
                operation()
        self.assertEqual(target.read_bytes(), b'untouched')

    def test_directory_symlink_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        linked_data = self.root / 'linked'
        try:
            linked_data.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Symlinks are unavailable')
        with self.assertRaises(DomainError):
            LogArchive(linked_data)

    def test_hardlinked_log_is_rejected(self):
        import os
        outside = self.root / 'outside'
        outside.write_bytes(b'original')
        path = self.root / 'workflow-logs' / (KEY + '.log')
        try:
            os.link(outside, path)
        except OSError:
            self.skipTest('Hardlinks are unavailable')
        with self.assertRaises(DomainError):
            self.archive.append(KEY, 0, b'x')
        self.assertEqual(outside.read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
