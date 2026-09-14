"""Artifact failures observed through workflow HTTP and simulated SSH only."""
import os
import base64
import hashlib
import json
import unittest

from hive.domain import CommandResult
from tests import test_workflow_multinode as multinode
from tests.test_workflows import payload
from tests.workflow_remote import Remote


class ArtifactRemote(Remote):
    def __init__(self):
        super().__init__()
        self.file_result = CommandResult('HIVE_FILE_ERROR MISSING\n', '', 0)
        self.file_reads = 0

    def run(self, node, script, timeout=20):
        if 'HIVE_FILE ' in script:
            self.file_reads += 1
            return self.file_result
        return super().run(node, script, timeout)


@unittest.skipUnless(os.getenv('HIVE_TEST_MYSQL_PORT'), 'Needs isolated MySQL')
class WorkflowArtifactFailureHTTP(unittest.TestCase):
    setUpClass = classmethod(multinode.MultiNodeHTTP.setUpClass.__func__)
    tearDownClass = classmethod(multinode.MultiNodeHTTP.tearDownClass.__func__)
    setUp = multinode.MultiNodeHTTP.setUp
    tearDown = multinode.MultiNodeHTTP.tearDown
    start = multinode.MultiNodeHTTP.start
    advance = multinode.MultiNodeHTTP.advance

    def spec(self):
        spec = payload()
        spec['jobs'] = [spec['jobs'][0]]
        spec['jobs'][0].update(npu_count=2, artifacts=[{'path': '/results/missing.json'}])
        return spec

    def reuse(self, space):
        spec = self.spec()
        spec.update(idempotency_key='reuse-after-artifact-failure', space_id=space['id'], environments=[])
        spec.pop('resource')
        spec['jobs'][0]['artifacts'] = []
        response = self.client.post('/api/workflows', json=spec)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_failed_process_with_missing_output_finishes_and_releases_cards_for_next_task(self):
        remote = ArtifactRemote()
        remote.fail_jobs = True
        task, space = self.start(self.spec(), remote)
        failed = self.advance(task, space, 'FAILED')
        self.assertEqual(failed['jobs'][0]['status'], 'FAILED')
        self.assertIn('退出码 7', failed['jobs'][0]['reason'])
        self.assertEqual(failed['jobs'][0]['artifacts'], [])
        self.assertGreater(remote.file_reads, 0)
        remote.fail_jobs = False
        following = self.advance(self.reuse(space), space)
        self.assertEqual(following['jobs'][0]['status'], 'SUCCEEDED')
        self.assertEqual(len(following['jobs'][0]['cards']), 2)

    def test_unconfirmed_artifact_read_keeps_claim_until_file_error_is_confirmed(self):
        remote = ArtifactRemote()
        # Even a missing-file marker cannot override a failed identity post-check.
        remote.file_result = CommandResult('HIVE_FILE_ERROR MISSING\n', 'identity changed', 1)
        task, space = self.start(self.spec(), remote)
        for _ in range(15):
            self.s.workflows.tick_space(space['id'])
        pending = self.client.get('/api/workflows/' + task['id']).json()
        self.assertEqual(pending['status'], 'RUNNING')
        self.assertEqual(pending['jobs'][0]['status'], 'RUNNING')
        following = self.reuse(space)
        for _ in range(4):
            self.s.workflows.tick_space(space['id'])
        self.assertEqual(self.client.get('/api/workflows/' + following['id']).json()['jobs'][0]['status'], 'PENDING')
        remote.file_result = CommandResult('HIVE_FILE_ERROR MISSING\n', '', 0)
        failed = self.advance(task, space, 'FAILED')
        self.assertIn('missing', failed['jobs'][0]['reason'].lower())
        self.assertEqual(self.advance(following, space)['jobs'][0]['status'], 'SUCCEEDED')

    def test_confirmed_archive_conflict_preserves_first_download_and_releases_claim(self):
        class ChangingRemote(ArtifactRemote):
            changed = False

            def run(self, node, script, timeout=20):
                if 'HIVE_FILE ' in script:
                    if '/results/second.txt' in script:
                        self.changed = True
                        return CommandResult('', 'temporary connection failure', 255)
                    raw = b'changed' if self.changed else b'original'
                    return CommandResult('HIVE_FILE '+str(len(raw))+' '+hashlib.sha256(raw).hexdigest()
                                         +'\n'+base64.b64encode(raw).decode(),'',0)
                return super().run(node,script,timeout)

        remote = ChangingRemote()
        spec = self.spec()
        spec['jobs'][0]['artifacts'] = [{'path':'/results/first.txt'},{'path':'/results/second.txt'}]
        task, space = self.start(spec,remote)
        for _ in range(20):
            self.s.workflows.tick_space(space['id'])
            detail = self.client.get('/api/workflows/'+task['id']).json()
            if detail['status']=='FAILED':
                break
        self.assertEqual(detail['status'],'FAILED')
        self.assertIn('原归档',detail['jobs'][0]['reason'])
        saved = detail['jobs'][0]['artifacts']
        self.assertEqual(len(saved),1)
        self.assertEqual(self.client.get(saved[0]['download_url']).content,b'original')
        self.assertEqual(self.advance(self.reuse(space),space)['status'],'SUCCEEDED')

    def test_directory_output_finishes_and_downloads_as_tar_gzip(self):
        from tests.test_directory_artifacts import archive_bytes
        raw = archive_bytes()

        class DirectoryRemote(ArtifactRemote):
            def run(self,node,script,timeout=20):
                if 'HIVE_FILE ' in script:
                    return CommandResult('HIVE_FILE_ERROR NOT_REGULAR','',0)
                if 'python3 - create ' in script:
                    return CommandResult(json.dumps({'token':'a'*32,'size':len(raw),
                        'sha256':hashlib.sha256(raw).hexdigest(),'unpacked_size':13,'entry_count':3}),'',0)
                if 'python3 - read ' in script:
                    return CommandResult(base64.b64encode(raw).decode(),'',0)
                if 'python3 - drop ' in script:
                    return CommandResult('REMOVED','',0)
                return super().run(node,script,timeout)

        spec = self.spec()
        spec['jobs'][0]['artifacts'] = [{'path':'/outputs'}]
        task,space = self.start(spec,DirectoryRemote())
        complete = self.advance(task,space)
        artifact = complete['jobs'][0]['artifacts'][0]
        response = self.client.get(artifact['download_url'])
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.content,raw)
        self.assertEqual(response.headers['content-type'],'application/gzip')
        self.assertIn('outputs.tar.gz',response.headers['content-disposition'])
