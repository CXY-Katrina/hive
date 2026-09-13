"""Owners see queued tasks and their actual environment preparation progress."""
import os
import unittest

from tests import test_workflow_multinode as multinode
from tests.test_workflows import payload


@unittest.skipUnless(os.getenv('HIVE_TEST_MYSQL_PORT'), 'Needs isolated MySQL')
class WorkflowProgressHTTP(unittest.TestCase):
    setUpClass = classmethod(multinode.MultiNodeHTTP.setUpClass.__func__)
    tearDownClass = classmethod(multinode.MultiNodeHTTP.tearDownClass.__func__)
    setUp = multinode.MultiNodeHTTP.setUp
    tearDown = multinode.MultiNodeHTTP.tearDown
    start = multinode.MultiNodeHTTP.start

    def test_owner_sees_queued_workflow_and_live_preparation_while_other_members_remain_denied(self):
        spec = payload()
        spec['environments'][0]['install'] = [{'launch': 'printf install'}]
        spec['environments'][0]['verify'] = [{'launch': 'printf verify'}]
        task, space = self.start(spec, multinode.MultiRemote())
        listed = self.client.get('/api/workflows').json()
        self.assertEqual([row['id'] for row in listed], [task['id']])
        self.assertEqual(listed[0]['status'], 'QUEUED')
        self.assertEqual(listed[0]['space_status'], 'QUEUED')
        self.assertIsNone(listed[0]['preparation_phase'])
        phases = set()
        for _ in range(10):
            self.s.workflows.tick_space(space['id'])
            row = self.client.get('/api/workflows').json()[0]
            detail = self.client.get('/api/workflows/'+task['id']).json()
            self.assertEqual(row['preparation_phase'], detail['preparation_phase'])
            if row['preparation_phase']:
                phases.add(row['preparation_phase'])
                self.assertEqual(row['status'], 'QUEUED')
                self.assertEqual(row['space_status'], 'PREPARING')
            if row['space_status'] == 'READY':
                self.assertIsNone(row['preparation_phase'])
                break
        self.assertEqual(phases, {'CHECKOUT','INSTALL','VERIFY'})
        self.client.post('/api/session', json={'username':'bob'})
        self.assertEqual(self.client.get('/api/workflows').json(), [])
        self.assertEqual(self.client.get('/api/workflows?scope=all').json(), [])
        for suffix, method in (('', 'get'), ('/cancel', 'post'), ('/jobs/first/logs/steps','get')):
            self.assertEqual(getattr(self.client, method)('/api/workflows/'+task['id']+suffix).status_code, 403)
        self.client.post('/api/session', json={'username':'admin','password':'test-admin'})
        self.assertEqual([row['id'] for row in self.client.get('/api/workflows').json()], [task['id']])
