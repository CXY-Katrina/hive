"""Reused-space retention observed through HTTP, isolated MySQL and SSH."""
import os
import re
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from tests import test_workflow_multinode as multinode
from tests.test_workflows import payload
from tests.workflow_remote import Remote


@unittest.skipUnless(os.getenv('HIVE_TEST_MYSQL_PORT'), 'Needs isolated MySQL')
class WorkflowRetentionHTTP(unittest.TestCase):
    setUpClass = classmethod(multinode.MultiNodeHTTP.setUpClass.__func__)
    tearDownClass = classmethod(multinode.MultiNodeHTTP.tearDownClass.__func__)
    setUp = multinode.MultiNodeHTTP.setUp
    tearDown = multinode.MultiNodeHTTP.tearDown
    start = multinode.MultiNodeHTTP.start
    advance = multinode.MultiNodeHTTP.advance

    def space(self, space_id):
        return next(space for space in self.client.get('/api/spaces').json()
                    if space['id'] == space_id)

    def reuse(self, space_id, retain_minutes):
        spec = payload()
        spec.update(idempotency_key='new-retention', space_id=space_id,
                    retain_minutes=retain_minutes, environments=[])
        spec.pop('resource')
        response = self.client.post('/api/workflows', json=spec)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_reuse_changes_three_days_to_one_day_and_expires_after_completion(self):
        spec = payload()
        spec['retain_minutes'] = 4320
        remote = Remote()
        first, space = self.start(spec, remote)
        self.advance(first, space)
        original = self.space(space['id'])
        self.assertIsNotNone(original['retain_until'])

        remote.hold_jobs = True
        second = self.reuse(space['id'], 1440)
        running = self.advance(second, space, 'RUNNING')
        self.assertEqual(running['status'], 'RUNNING')
        current = self.space(space['id'])
        self.assertIsNone(current['retain_until'])
        self.assertEqual(current['status'], 'READY')
        self.assertEqual(self.s.resources.get(space['request_id'])['status'], 'ACTIVE')
        self.assertFalse(any(event[0] == 'stop' for event in remote.events))

        remote.hold_jobs = False
        finished = self.advance(second, space)
        retained = self.space(space['id'])
        deadline = datetime.fromisoformat(retained['retain_until']).replace(tzinfo=None)
        ended = datetime.fromisoformat(finished['ended_at']).replace(tzinfo=None)
        self.assertAlmostEqual((deadline - ended).total_seconds(), 86400, delta=2)
        expected_spec = {**original['spec'], 'retain_minutes': 1440}
        self.assertEqual(retained['spec'], expected_spec)
        with patch('hive.workflow_engine.now', return_value=deadline - timedelta(seconds=1)):
            self.s.workflows.tick_space(space['id'])
        self.assertEqual(self.space(space['id'])['status'], 'READY')
        with patch('hive.workflow_engine.now', return_value=deadline):
            self.s.workflows.tick_space(space['id'])
        self.assertEqual(self.space(space['id'])['status'], 'CLOSED')
        self.assertEqual(self.s.resources.get(space['request_id'])['status'], 'RELEASING')
        self.assertTrue(any(event[0] == 'stop' for event in remote.events))

    def test_zero_retention_waits_for_older_running_task_before_closing(self):
        spec = payload()
        spec['retain_minutes'] = 4320
        spec['jobs'] = [spec['jobs'][0]]
        spec['jobs'][0]['steps'] = [{'launch': '# BLOCK_UPSTREAM\ntrue'}]
        remote = multinode.MultiRemote()
        remote.hold_jobs = True
        first, space = self.start(spec, remote)
        self.advance(first, space, 'RUNNING')
        prepared = self.space(space['id'])
        remote.hold_host = prepared['environments'][0]['host']
        self.assertIsNotNone(remote.hold_host)
        remote.hold_jobs = False

        second = self.reuse(space['id'], 0)
        self.advance(second, space)
        self.assertEqual(self.client.get('/api/workflows/' + first['id']).json()['status'], 'RUNNING')
        current = self.space(space['id'])
        self.assertEqual(current['status'], 'READY')
        self.assertIsNone(current['retain_until'])
        self.assertEqual(self.s.resources.get(space['request_id'])['status'], 'ACTIVE')
        self.assertFalse(any(event[0] == 'stop' for event in remote.events))

        remote.hold_host = None
        self.advance(first, space)
        closed = self.space(space['id'])
        self.assertEqual(closed['status'], 'CLOSED')
        self.assertEqual(closed['spec'], {**prepared['spec'], 'retain_minutes': 0})
        self.assertIsNone(closed['retain_until'])
        self.assertEqual(self.s.resources.get(space['request_id'])['status'], 'RELEASING')
        self.assertTrue(any(event[0] == 'stop' for event in remote.events))

    def test_submission_during_last_job_completion_does_not_start_idle_deadline(self):
        class SubmitOnCompletion(Remote):
            on_completion = None

            def run(self, node, script, timeout=20):
                result = super().run(node, script, timeout)
                action = re.search(r'bash -s -- (/var/tmp/hive/container-attempts/\S+) status ', script)
                if (self.on_completion and action and result.stdout.startswith('EXITED')
                        and '# SUBMIT_WHILE_FINISHING' in self.attempts[action[1]]['script']):
                    callback, self.on_completion = self.on_completion, None
                    callback()
                return result

        spec = payload()
        spec['retain_minutes'] = 4320
        spec['jobs'] = [spec['jobs'][0]]
        spec['jobs'][0]['post'] = [{'launch': '# SUBMIT_WHILE_FINISHING\ntrue'}]
        remote = SubmitOnCompletion()
        first, space = self.start(spec, remote)
        submitted = []
        remote.on_completion = lambda: submitted.append(self.reuse(space['id'], 1440))
        self.advance(first, space)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(self.client.get('/api/workflows/' + submitted[0]['id']).json()['status'], 'QUEUED')
        self.assertIsNone(self.space(space['id'])['retain_until'])
        self.assertFalse(any(event[0] == 'stop' for event in remote.events))

        finished = self.advance(submitted[0], space)
        retained = self.space(space['id'])
        deadline = datetime.fromisoformat(retained['retain_until'])
        ended = datetime.fromisoformat(finished['ended_at'])
        self.assertAlmostEqual((deadline - ended).total_seconds(), 86400, delta=2)
