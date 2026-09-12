"""Node resource mappings through authenticated HTTP and a disposable MySQL DB."""
import os
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import paramiko

from fastapi.testclient import TestClient
from hive.api import create_app
from hive.domain import CommandResult
from hive.onboarding import Onboarding
from tests import test_integration as database_tests
from tests.test_hardware import envelope, BOOT


ENTRIES = [
    {'kind': 'model', 'name': 'Qwen/Qwen3-8B', 'target': '/mnt/weights/Qwen3-8B'},
    {'kind': 'dataset', 'name': 'modelscope/GSM8K', 'target': '/mnt/datasets/gsm8k'},
    {'kind': 'image', 'name': 'ascend-base', 'target': 'registry.local:5000/ascend/base:v1'},
    {'kind': 'package', 'name': 'torch-npu', 'target': '/mnt/packages/torch_npu.whl'},
]


@unittest.skipUnless(os.getenv('HIVE_TEST_MYSQL_PORT'), 'Set HIVE_TEST_MYSQL_PORT for isolated MySQL tests')
class NodeMappingsIntegration(unittest.TestCase):
    setUpClass = classmethod(database_tests.MySQLIntegration.setUpClass.__func__)
    tearDownClass = classmethod(database_tests.MySQLIntegration.tearDownClass.__func__)
    setUp = database_tests.MySQLIntegration.setUp
    node = database_tests.MySQLIntegration.node

    def client(self, username='admin', onboarding=None):
        services = SimpleNamespace(db=self.db, identity=self.identity, inventory=self.inventory, resources=self.resources)
        if onboarding:
            services.onboarding = onboarding
        client = TestClient(create_app(services, self.settings))
        self.addCleanup(client.close)
        reply = client.post('/api/session', json={'username': username, 'password': 'test-admin' if username == 'admin' else ''})
        self.assertEqual(reply.status_code, 200)
        return client

    def test_admin_can_register_all_mapping_kinds_and_read_them_back(self):
        node = self.node()
        client = self.client()
        path = '/api/nodes/' + node['id'] + '/mappings'
        initial = client.get(path)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()['entries'], [])
        self.assertEqual(initial.json()['version'], 0)
        saved = client.put(path, json={'version': 0, 'entries': ENTRIES})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()['version'], 1)
        self.assertEqual(client.get(path).json()['entries'], ENTRIES)

    def test_stale_editor_cannot_overwrite_changes_and_members_are_read_only(self):
        node = self.node()
        admin, viewer = self.client(), self.client('alice')
        path = '/api/nodes/' + node['id'] + '/mappings'
        self.assertEqual(admin.put(path, json={'version': 0, 'entries': ENTRIES}).status_code, 200)
        self.assertEqual(viewer.get(path).json()['entries'], ENTRIES)
        self.assertEqual(viewer.put(path, json={'version': 1, 'entries': []}).status_code, 403)
        self.assertEqual(admin.put(path, json={'version': 0, 'entries': []}).status_code, 409)
        self.assertEqual(admin.get(path).json()['entries'], ENTRIES)
        self.assertEqual(admin.put(path, json={'version': 1, 'entries': []}).json()['version'], 2)
        self.assertEqual(viewer.get(path).json()['entries'], [])
        events = admin.get('/api/audit').json()
        self.assertEqual(len([row for row in events if row['action'] == 'node.mappings.replace']), 2)

    def test_invalid_paths_names_and_duplicate_aliases_are_rejected_atomically(self):
        node, client = self.node(), self.client()
        path = '/api/nodes/' + node['id'] + '/mappings'
        invalid = [
            [{'kind': 'model', 'name': 'Qwen/Qwen3-8B', 'target': '../weights'}],
            [{'kind': 'dataset', 'name': 'modelscope/GSM8K', 'target': '/mnt/../etc'}],
            [{'kind': 'model', 'name': '../Qwen', 'target': '/mnt/model'}],
            [{'kind': 'image', 'name': 'base', 'target': 'image;touch /tmp/x'}],
            [{'kind': 'package', 'name': 'torch-npu', 'target': '/mnt/file\x00'}],
            [ENTRIES[0], ENTRIES[0]],
        ]
        for entries in invalid:
            with self.subTest(entries=entries):
                self.assertEqual(client.put(path, json={'version': 0, 'entries': entries}).status_code, 422)
                self.assertEqual(client.get(path).json()['entries'], [])

    def test_admission_optionally_saves_mappings_with_the_new_node(self):
        key = paramiko.RSAKey.generate(1024)
        connection = Mock()
        connection.get_transport.return_value.get_remote_server_key.return_value = key
        @contextmanager
        def connected(_):
            yield connection
        ssh = Mock()
        ssh._connection = connected
        ssh.run.return_value = CommandResult(envelope({'boot': BOOT, 'uname': 'Linux test aarch64',
            'machine': 'aarch64', 'system': 'TestServer', 'npu_smi': '/usr/local/bin/npu-smi'}), '', 0)
        with tempfile.TemporaryDirectory() as directory, patch('hive.onboarding.SSHTransport', return_value=ssh):
            onboarding = Onboarding(replace(self.settings, known_hosts=Path(directory) / 'known_hosts'))
            client = self.client(onboarding=onboarding)
            body = {'name': 'mapped-node', 'host': '10.0.0.8', 'password': 'test-only',
                    'generation': 'A3', 'mappings': ENTRIES}
            response = client.post('/api/nodes', json=body)
            self.assertEqual(response.status_code, 201, response.text)
            node_id = response.json()['id']
            self.assertEqual(client.get('/api/nodes/' + node_id + '/mappings').json()['entries'], ENTRIES)
            invalid = {**body, 'host': '10.0.0.9', 'mappings': [ENTRIES[0], ENTRIES[0]]}
            self.assertEqual(client.post('/api/nodes', json=invalid).status_code, 422)
            self.assertEqual(len(client.get('/api/nodes').json()), 1)

    def test_removed_nodes_and_unauthenticated_callers_cannot_read_mappings(self):
        node, client = self.node(), self.client()
        path = '/api/nodes/' + node['id'] + '/mappings'
        self.assertEqual(client.delete('/api/nodes/' + node['id']).status_code, 200)
        self.assertEqual(client.get(path).status_code, 404)
        client.delete('/api/session')
        self.assertEqual(client.get(path).status_code, 401)

    def test_mapping_table_is_bounded_for_later_environment_injection(self):
        node, client = self.node(), self.client()
        path = '/api/nodes/' + node['id'] + '/mappings'
        entries = [{'kind': 'package', 'name': 'package-' + str(index), 'target': '/mnt/' + 'a' * 3990}
                   for index in range(5)]
        self.assertEqual(client.put(path, json={'version': 0, 'entries': entries}).status_code, 422)
        self.assertEqual(client.get(path).json()['version'], 0)


if __name__ == '__main__':
    unittest.main()
