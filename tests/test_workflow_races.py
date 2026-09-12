"""Lifecycle races through HTTP and an event-gated external SSH boundary."""
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from hive.api import create_app
from hive.domain import SYSTEM, DeviceSample, Snapshot, now
from tests import test_workflows as fixtures
from tests.workflow_remote import BOOT, Remote


class StopBarrierRemote(Remote):
    def __init__(self):
        super().__init__()
        self.stopping = threading.Event()
        self.allow_stop = threading.Event()

    def run(self, node, script, timeout=20):
        if 'STOPPED' in script:
            self.stopping.set()
            if not self.allow_stop.wait(15):
                raise AssertionError('Test did not release the SSH stop barrier')
        return super().run(node, script, timeout)


@unittest.skipUnless(os.getenv('HIVE_TEST_MYSQL_PORT'), 'Needs isolated MySQL')
class WorkflowRacesHTTP(unittest.TestCase):
    setUpClass = classmethod(fixtures.WorkflowHTTP.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.WorkflowHTTP.tearDownClass.__func__)

    def setUp(self):
        fixtures.WorkflowHTTP.setUp(self)

    def tearDown(self):
        fixtures.WorkflowHTTP.tearDown(self)

    def reuse(self, space_id):
        body = fixtures.payload()
        body.update(idempotency_key='concurrent-reuse', space_id=space_id, environments=[])
        body.pop('resource')
        return body

    def test_closing_space_rejects_concurrent_reuse_before_releasing_resources(self):
        node = self.s.inventory.create(SYSTEM, {
            'name': 'race-node', 'host': '10.0.0.1', 'password': 'test-only',
            'generation': 'A2', 'model': 'test'})
        self.s.telemetry.ingest(node['id'], Snapshot([
            DeviceSample(str(i), str(i), '0', str(i), 64 * 1024**3,
                         0, 0, 'OK', process_complete=True) for i in range(2)], BOOT, now()))
        remote = StopBarrierRemote()
        self.s.workflows.transport = remote
        body = fixtures.payload()
        body['retain_minutes'] = 0
        response = self.client.post('/api/workflows', json=body)
        self.assertEqual(response.status_code, 201, response.text)
        task = response.json()
        space = self.client.get('/api/spaces').json()[0]
        reservation = self.s.resources.reserve(space['request_id'])
        self.assertIsNotNone(reservation)
        self.assertTrue(self.s.resources.deliver(reservation['id'], reservation['version']))

        def reconcile():
            for _ in range(40):
                self.s.workflows.tick_space(space['id'])

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(reconcile)
            try:
                reached_stop = remote.stopping.wait(30)
                if not reached_stop and future.done():
                    future.result()
                self.assertTrue(reached_stop, 'Workflow did not reach the SSH stop boundary: ' + str(self.client.get('/api/workflows/' + task['id']).json()))
                with TestClient(create_app(self.s, self.settings)) as concurrent:
                    concurrent.cookies.update(self.client.cookies)
                    current = concurrent.get('/api/spaces').json()[0]
                    self.assertEqual(current['status'], 'CLOSING', current)
                    request = concurrent.get('/api/requests').json()[0]
                    self.assertEqual(request['status'], 'ACTIVE', request)
                    rejected = concurrent.post('/api/workflows', json=self.reuse(space['id']))
                    self.assertEqual(rejected.status_code, 409, rejected.text)
                    tasks = concurrent.get('/api/workflows').json()
                    self.assertEqual([t['id'] for t in tasks], [task['id']])
                    self.assertEqual(concurrent.get('/api/requests').json()[0]['status'], 'ACTIVE')
            finally:
                remote.allow_stop.set()
            future.result(timeout=10)
        self.assertEqual(self.client.get('/api/spaces').json()[0]['status'], 'CLOSED')
        self.assertEqual(self.client.get('/api/requests').json()[0]['status'], 'RELEASING')
        self.assertEqual(self.client.get('/api/workflows/' + task['id']).json()['status'], 'SUCCEEDED')

    def test_revoked_request_permission_blocks_existing_space_reuse(self):
        response = self.client.post('/api/workflows', json=fixtures.payload())
        self.assertEqual(response.status_code, 201, response.text)
        task = response.json()
        alice = self.client.get('/api/session').json()
        with TestClient(create_app(self.s, self.settings)) as admin:
            login = admin.post('/api/session', json={'username': 'admin', 'password': 'test-admin'})
            self.assertEqual(login.status_code, 200, login.text)
            revoked = admin.patch('/api/members/' + alice['id'], json={
                'can_request': False, 'can_view_credentials': True})
            self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertFalse(self.client.get('/api/session').json()['can_request'])
        denied = self.client.post('/api/workflows', json=self.reuse(task['space_id']))
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual([t['id'] for t in self.client.get('/api/workflows').json()], [task['id']])
        self.assertEqual(len(self.client.get('/api/requests').json()), 1)
        self.assertEqual(self.client.get('/api/spaces').json()[0]['status'], 'QUEUED')
