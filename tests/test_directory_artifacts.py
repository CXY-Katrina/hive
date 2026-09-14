"""Directory artifacts at the external SSH boundary."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest

from hive.container_runtime import ContainerRuntime
from hive.domain import DomainError
from hive.workflow_artifacts import WorkflowArtifacts
from tests.test_container_runtime import SSH, IDENTITY, NODE


def archive_bytes():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        content = b'actual output\n'
        info = tarfile.TarInfo('outputs/results/gsm8k.csv')
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


class DirectoryArtifactTests(unittest.TestCase):
    def test_directory_is_automatically_archived_and_downloadable(self):
        raw = archive_bytes()
        snapshot = {'token': 'a' * 32, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                    'unpacked_size': 13, 'entry_count': 3}
        ssh = SSH('HIVE_FILE_ERROR NOT_REGULAR', json.dumps(snapshot),
                  base64.b64encode(raw).decode(), 'REMOVED')
        with TemporaryDirectory() as folder:
            archive = WorkflowArtifacts(ContainerRuntime(ssh), Path(folder))
            result = archive.collect(NODE, IDENTITY, 'task', 'job', [{'path': '/work/outputs', 'label': 'Outputs'}])
            meta = result['artifacts'][0]
            self.assertEqual(meta['format'], 'tar.gz')
            self.assertEqual(meta['download_name'], 'outputs.tar.gz')
            saved = archive.read('task', 'job', meta['id'])
            with tarfile.open(saved['path']) as bundle:
                self.assertEqual(bundle.extractfile('outputs/results/gsm8k.csv').read(), b'actual output\n')
            self.assertEqual(result['metrics'], [])

    def test_directory_policy_failure_remains_terminal(self):
        for reason in ('UNSAFE_PATH', 'SPECIAL_FILE', 'OVERSIZED', 'TOO_MANY_ENTRIES', 'CHANGED'):
            ssh = SSH('HIVE_FILE_ERROR NOT_REGULAR', json.dumps({'error': reason}))
            with TemporaryDirectory() as folder, self.assertRaises(DomainError) as raised:
                WorkflowArtifacts(ContainerRuntime(ssh), Path(folder)).collect(
                    NODE, IDENTITY, 'task', 'job', [{'path': '/outputs', 'label': 'Outputs'}])
            self.assertEqual(raised.exception.code, 422)

    def test_corrupt_directory_transfer_never_becomes_downloadable(self):
        raw = archive_bytes()
        ssh = SSH('HIVE_FILE_ERROR NOT_REGULAR', json.dumps({'token': 'a' * 32,
            'size': len(raw), 'sha256': '0' * 64, 'unpacked_size': 13, 'entry_count': 3}),
            base64.b64encode(raw).decode(), 'REMOVED')
        with TemporaryDirectory() as folder, self.assertRaises(DomainError):
            WorkflowArtifacts(ContainerRuntime(ssh), Path(folder)).collect(
                NODE, IDENTITY, 'task', 'job', [{'path': '/outputs', 'label': 'Outputs'}])

    def test_large_snapshot_is_streamed_in_bounded_chunks_with_identity_guards(self):
        raw = os.urandom(1024 * 1024 + 97)
        ssh = SSH('HIVE_FILE_ERROR NOT_REGULAR', json.dumps({'token': 'b' * 32,
            'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'unpacked_size': len(raw), 'entry_count': 1}),
            base64.b64encode(raw[:1024*1024]).decode(),
            base64.b64encode(raw[1024*1024:]).decode(), 'REMOVED')
        with TemporaryDirectory() as folder:
            destination = Path(folder)/'snapshot'
            result = ContainerRuntime(ssh).export_artifact(NODE, IDENTITY, '/outputs', destination)
            self.assertEqual(destination.read_bytes(), raw)
            self.assertEqual(result['size'], len(raw))
        self.assertEqual(len(ssh.calls), 5)
        for _, script, *_ in ssh.calls:
            self.assertIn(IDENTITY['container_id'], script)
            self.assertIn(IDENTITY['started_at'], script)
            self.assertIn(IDENTITY['host_boot_id'], script)

    def test_archive_size_and_entry_limits_reject_before_download(self):
        for change in ({'size': 256*1024*1024+1}, {'unpacked_size': 256*1024*1024+1}, {'entry_count':10001}):
            data = {'token':'b'*32,'size':10,'sha256':'a'*64,'unpacked_size':10,'entry_count':1,**change}
            ssh = SSH('HIVE_FILE_ERROR NOT_REGULAR', json.dumps(data), 'REMOVED')
            with TemporaryDirectory() as folder, self.assertRaises(DomainError):
                ContainerRuntime(ssh).export_artifact(NODE, IDENTITY, '/outputs', Path(folder)/'snapshot')
            self.assertEqual(len(ssh.calls), 3)

    def test_changed_directory_preserves_original_archive_and_leaves_no_partial_download(self):
        first, second = archive_bytes(), archive_bytes()+b'changed'
        replies = []
        for raw in (first,first,second):
            replies += ['HIVE_FILE_ERROR NOT_REGULAR', json.dumps({'token':'c'*32,'size':len(raw),
                'sha256':hashlib.sha256(raw).hexdigest(),'unpacked_size':13,'entry_count':3}),
                base64.b64encode(raw).decode(),'REMOVED']
        with TemporaryDirectory() as folder:
            archive = WorkflowArtifacts(ContainerRuntime(SSH(*replies)),Path(folder))
            items = [{'path':'/outputs','label':'Outputs'}]
            original = archive.collect(NODE,IDENTITY,'task','job',items)
            self.assertEqual(archive.collect(NODE,IDENTITY,'task','job',items),original)
            with self.assertRaises(DomainError) as raised:
                archive.collect(NODE,IDENTITY,'task','job',items)
            self.assertEqual(raised.exception.code,409)
            saved = archive.read('task','job',original['artifacts'][0]['id'])
            self.assertEqual(saved['path'].read_bytes(),first)
            self.assertEqual(list(Path(folder).rglob('.collect-*')),[])


@unittest.skipUnless(os.name == 'posix' and os.open in os.supports_dir_fd,
                     'Requires local Linux descriptor-relative filesystem operations')
class LinuxDirectorySnapshotTests(unittest.TestCase):
    def setUp(self):
        import importlib.util
        script = Path(__file__).resolve().parents[1]/'backend/hive/scripts/artifact_snapshot.py'
        spec = importlib.util.spec_from_file_location('artifact_snapshot_test',script)
        self.snapshot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.snapshot)
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.snapshot.PREFIX = str(self.root/'archive-')

    def test_nested_and_empty_directories_preserve_paths_and_bytes(self):
        output = self.root/'outputs'
        (output/'empty').mkdir(parents=True)
        (output/'results').mkdir()
        (output/'results'/'metrics.csv').write_bytes(b'actual output\n')
        first = self.snapshot.create(str(output),1024*1024)
        second = self.snapshot.create(str(output),1024*1024)
        self.assertEqual(first['sha256'],second['sha256'])
        with tarfile.open(self.snapshot.PREFIX+first['token']+'.tar.gz') as archive:
            self.assertIn('outputs/empty', archive.getnames())
            self.assertEqual(archive.extractfile('outputs/results/metrics.csv').read(),b'actual output\n')

    def test_symlinks_hardlinks_fifos_and_raw_size_limit_are_rejected(self):
        outside = self.root/'secret'
        outside.write_bytes(b'private')
        for kind in ('symlink','hardlink','fifo','large'):
            with self.subTest(kind=kind):
                output = self.root/kind
                output.mkdir()
                child = output/'item'
                if kind=='symlink':
                    child.symlink_to(outside)
                elif kind=='hardlink':
                    os.link(outside,child)
                elif kind=='fifo':
                    os.mkfifo(child)
                else:
                    child.write_bytes(b'x'*2048)
                with self.assertRaises(self.snapshot.PolicyError):
                    self.snapshot.create(str(output),1024)
        self.assertEqual(list(self.root.glob('archive-*')),[])
