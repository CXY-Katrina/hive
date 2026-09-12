"""Artifacts through SSH and local archive boundaries, without real workloads."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hive.container_runtime import ContainerRuntime
from hive.domain import DomainError
from hive.domain import CommandResult
from hive.workflow_artifacts import WorkflowArtifacts
from tests.test_container_runtime import SSH, IDENTITY, NODE
from tests.test_container_files import response


class WorkflowArtifactTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix='hive_artifacts_test_')
        self.addCleanup(self.directory.cleanup)

    def archive(self, *data):
        self.ssh = SSH(*(response(raw) for raw in data))
        return WorkflowArtifacts(ContainerRuntime(self.ssh),Path(self.directory.name))

    def test_binary_artifact_is_archived_idempotently_and_can_be_downloaded(self):
        raw = b'\x00binary\xff\n'
        archive = self.archive(raw,raw)
        items = [{'path':'/outputs/result.bin','label':'Result','kind':'file'}]
        first = archive.collect(NODE,IDENTITY,'task-1','job-1',items)
        second = archive.collect(NODE,IDENTITY,'task-1','job-1',items)
        self.assertEqual(first,second)
        self.assertEqual(first['metrics'],[])
        meta = first['artifacts'][0]
        result = archive.read('task-1','job-1',meta['id'])
        self.assertEqual(result['path'].read_bytes(),raw)
        self.assertEqual(result['metadata'],meta)
        self.assertEqual(meta['container_id'],IDENTITY['container_id'])

    def test_metrics_without_declared_verdict_remain_unknown_and_raw_json_is_preserved(self):
        raw = b'{"metrics":[{"name":"throughput","value":123,"unit":"tokens/s","tags":{"phase":"decode"}}]}\n'
        archive = self.archive(raw)
        result = archive.collect(NODE,IDENTITY,'task-1','job-1',[{'path':'/outputs/metrics.json','label':'Metrics','kind':'metrics'}])
        self.assertEqual(result['metrics'][0]['verdict'],'unknown')
        self.assertEqual(result['metrics'][0]['metrics'][0]['verdict'],'unknown')
        self.assertEqual(result['metrics'][0]['metrics'][0]['value'],123)
        self.assertEqual(archive.read('task-1','job-1',result['artifacts'][0]['id'])['path'].read_bytes(),raw)

    def test_changed_remote_artifact_does_not_overwrite_original(self):
        archive = self.archive(b'original',b'changed')
        items = [{'path':'/out.bin','label':'Result','kind':'file'}]
        initial = archive.collect(NODE,IDENTITY,'task-1','job-1',items)
        with self.assertRaises(DomainError):
            archive.collect(NODE,IDENTITY,'task-1','job-1',items)
        self.assertEqual(archive.read('task-1','job-1',initial['artifacts'][0]['id'])['path'].read_bytes(),b'original')

    def test_invalid_metrics_are_not_marked_passed_and_raw_file_remains_downloadable(self):
        import hashlib
        raw = b'{"metrics":[{"name":"score","value":NaN,"unit":"","tags":{}}]}'
        archive = self.archive(raw)
        with self.assertRaises(DomainError):
            archive.collect(NODE,IDENTITY,'task-1','job-1',[{'path':'/metrics.json','label':'Metrics','kind':'metrics'}])
        ident = hashlib.sha256(b'/metrics.json').hexdigest()
        self.assertEqual(archive.read('task-1','job-1',ident)['path'].read_bytes(),raw)

    def test_traversal_excess_count_and_network_failure_do_not_fabricate_artifacts(self):
        archive = self.archive()
        for task,job,items in (('../task','job',[]),('task','../job',[]),
                               ('task','job',[{'path':'/out','label':'x','kind':'file'}]*17)):
            with self.assertRaises(DomainError):
                archive.collect(NODE,IDENTITY,task,job,items)
        self.assertEqual(self.ssh.calls,[])
        broken = WorkflowArtifacts(ContainerRuntime(SSH(TimeoutError('private-error'))),Path(self.directory.name))
        with self.assertRaises(DomainError) as error:
            broken.collect(NODE,IDENTITY,'task','job',[{'path':'/out','label':'x','kind':'file'}])
        self.assertNotIn('private-error',str(error.exception))

    def test_declared_business_verdict_is_preserved_without_recomputation(self):
        raw = b'{"verdict":"failed","metrics":[{"name":"score","value":999,"unit":"score","tags":{},"verdict":"passed"}]}'
        archive = self.archive(raw)
        result = archive.collect(NODE,IDENTITY,'task','job',[{'path':'/metric','label':'x','kind':'metrics'}])
        self.assertEqual(result['metrics'][0]['verdict'],'failed')
        self.assertEqual(result['metrics'][0]['metrics'][0]['verdict'],'passed')

    def test_windows_device_names_and_ambiguous_trailing_dots_are_rejected_before_ssh(self):
        archive = self.archive()
        for name in ('CON','nul.txt','COM1','LPT9.log','job.'):
            with self.subTest(name=name), self.assertRaises(DomainError):
                archive.collect(NODE,IDENTITY,'task',name,[])
        self.assertEqual(self.ssh.calls,[])

    def test_total_size_limit_is_enforced_across_files(self):
        raw = b'x'*(4*1024*1024)
        outputs = [response(raw,raw[i*1024*1024:(i+1)*1024*1024]) for _ in range(5) for i in range(4)]
        archive = WorkflowArtifacts(ContainerRuntime(SSH(*outputs)),Path(self.directory.name))
        with self.assertRaises(DomainError):
            archive.collect(NODE,IDENTITY,'task','job',[{'path':f'/out{i}','label':str(i),'kind':'file'} for i in range(5)])

    def test_local_archive_symlink_escape_is_rejected(self):
        outside = Path(self.directory.name)/'outside'
        outside.mkdir()
        root = Path(self.directory.name)/'workflow-artifacts'
        root.mkdir()
        try:
            (root/'task').symlink_to(outside,target_is_directory=True)
        except OSError:
            self.skipTest('Local filesystem does not allow creating test symlinks')
        archive = self.archive()
        with self.assertRaises(DomainError):
            archive.collect(NODE,IDENTITY,'task','job',[])
        self.assertEqual(self.ssh.calls,[])

    def test_same_path_on_distinct_environments_is_archived_without_collision(self):
        other = {**IDENTITY, 'container_id': 'c' * 64}
        archive = self.archive(b'server-output', b'client-output')
        result = archive.collect(NODE, IDENTITY, 'task', 'job', [
            {'environment': 'server', 'path': '/out.txt', 'label': 'Server', 'kind': 'file'},
            {'environment': 'client', 'path': '/out.txt', 'label': 'Client', 'kind': 'file'}],
            targets={'server': (NODE, IDENTITY), 'client': (NODE, other)})
        left, right = result['artifacts']
        self.assertNotEqual(left['id'], right['id'])
        self.assertEqual(right['container_id'], other['container_id'])
        self.assertEqual(archive.read('task', 'job', left['id'])['path'].read_bytes(), b'server-output')
        self.assertEqual(archive.read('task', 'job', right['id'])['path'].read_bytes(), b'client-output')

    def test_output_without_type_uses_generic_metric_document_or_preserves_plain_file(self):
        archive = self.archive(b'{"metrics":[],"verdict":"failed"}', b'plain text output')
        metric = archive.collect(NODE, IDENTITY, 'task', 'job', [{'path': '/result.json', 'label': 'Result'}])
        self.assertEqual(metric['metrics'][0]['verdict'], 'failed')
        plain = archive.collect(NODE, IDENTITY, 'task', 'job', [{'path': '/result.txt', 'label': 'Log'}])
        self.assertEqual(plain['metrics'], [])
        self.assertEqual(archive.read('task', 'job', plain['artifacts'][0]['id'])['path'].read_bytes(), b'plain text output')
