"""Artifact failures observed through workflow HTTP and simulated SSH only."""
import os
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
