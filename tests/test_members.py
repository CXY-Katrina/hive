import os
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from hive.api import create_app
from hive.domain import DomainError, uid
from hive.schemas import ResourceSpec
from tests import test_integration as database_tests


@unittest.skipUnless(os.getenv('HIVE_TEST_MYSQL_PORT'), 'Set HIVE_TEST_MYSQL_PORT for isolated MySQL tests')
class MemberIntegration(unittest.TestCase):
    setUpClass = classmethod(database_tests.MySQLIntegration.setUpClass.__func__)
    tearDownClass = classmethod(database_tests.MySQLIntegration.tearDownClass.__func__)
    setUp = database_tests.MySQLIntegration.setUp
    node = database_tests.MySQLIntegration.node

    def test_admin_password_and_legacy_session(self):
        for password in ('', 'wrong'):
            with self.assertRaises(DomainError): self.identity.login('admin', password)
        token, actor = self.identity.login('admin', 'test-admin')
        self.assertTrue(actor.admin)
        self.assertTrue(self.identity.current(token).admin)
        with self.db.transaction() as c:
            c.execute('UPDATE sessions SET admin_authenticated=FALSE WHERE token_hash=%s', (self.identity.digest(token),))
        with self.assertRaises(DomainError): self.identity.current(token)

    def test_new_members_denied_and_permission_changes_apply_to_existing_session(self):
        token, member = self.identity.login('new-member')
        self.assertFalse(member.can_request)
        self.assertFalse(member.can_view_credentials)
        node = self.node()
        spec = ResourceSpec(generation='A2').model_dump()
        with self.assertRaises(DomainError): self.resources.create(member, spec, uid())
        with self.assertRaises(DomainError): self.inventory.credentials(node['id'], member)
        self.identity.permissions(self.admin, member.id, {'can_request': True, 'can_view_credentials': True})
        self.assertTrue(self.identity.current(token).can_request)
        self.assertTrue(self.identity.current(token).can_view_credentials)
        self.assertEqual(self.inventory.credentials(node['id'], member)['password'], 'test-secret')
        req = self.resources.create(member, spec, uid())
        self.identity.permissions(self.admin, member.id, {'can_request': False, 'can_view_credentials': False})
        self.assertFalse(self.identity.current(token).can_request)
        with self.assertRaises(DomainError): self.resources.create(member, spec, uid())
        with self.assertRaises(DomainError): self.inventory.credentials(node['id'], member)
        self.assertEqual(self.resources.release(req['id'], member)['status'], 'CANCELLED')

    def test_delete_revokes_sessions_and_prevents_self_registration(self):
        token, member = self.identity.login('removed-member')
        self.identity.remove(self.admin, member.id)
        with self.assertRaises(DomainError): self.identity.current(token)
        with self.assertRaises(DomainError): self.identity.login(member.username)
        self.assertNotIn(member.id, [row['id'] for row in self.identity.members(self.admin)])
        self.assertIsNotNone(self.db.one('SELECT deleted_at FROM users WHERE id=%s', (member.id,))['deleted_at'])

    def test_members_cannot_manage_members_and_admin_cannot_be_deleted(self):
        with self.assertRaises(DomainError): self.identity.members(self.alice)
        with self.assertRaises(DomainError): self.identity.permissions(self.alice, self.bob.id, {'can_request': True, 'can_view_credentials': True})
        with self.assertRaises(DomainError): self.identity.remove(self.alice, self.bob.id)
        with self.assertRaises(DomainError): self.identity.remove(self.admin, self.admin.id)
        with self.assertRaises(DomainError): self.identity.permissions(self.admin, self.admin.id, {'can_request': False, 'can_view_credentials': False})

    def test_delete_rejects_members_with_active_requests(self):
        req = self.resources.create(self.alice, ResourceSpec(generation='A2').model_dump(), uid())
        with self.assertRaises(DomainError): self.identity.remove(self.admin, self.alice.id)
        self.resources.release(req['id'], self.alice)
        self.identity.remove(self.admin, self.alice.id)

    def test_http_password_and_permission_enforcement(self):
        services = SimpleNamespace(db=self.db, identity=self.identity, inventory=self.inventory, resources=self.resources)
        with TestClient(create_app(services, self.settings)) as client:
            self.assertEqual(client.post('/api/session', json={'username': 'admin'}).status_code, 401)
            self.assertEqual(client.post('/api/session', json={'username': 'admin', 'password': 'test-admin'}).status_code, 200)
            self.assertEqual(client.get('/api/members').status_code, 200)
            client.post('/api/session', json={'username': 'viewer'})
            self.assertEqual(client.get('/api/members').status_code, 403)
            self.assertEqual(client.patch('/api/members/'+self.bob.id, json={'can_request': True, 'can_view_credentials': True}).status_code, 403)
            self.assertEqual(client.delete('/api/members/'+self.bob.id).status_code, 403)
            self.assertEqual(client.post('/api/requests', json={'idempotency_key': uid(), 'spec': {'generation': 'A2'}}).status_code, 403)
