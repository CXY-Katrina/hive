import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import paramiko
from fastapi.testclient import TestClient

from hive.config import Settings
from hive.domain import DomainError, CommandResult
from hive.onboarding import Onboarding
from hive.ssh import HostKeyRequired, RejectPolicy, host_key_info, ssh_error
from hive.telemetry import Telemetry
from tests.test_hardware import envelope, BOOT


class OnboardingTests(unittest.TestCase):
    def test_telemetry_preserves_safe_domain_error_not_exception_class(self):
        cursor = Mock()
        @contextmanager
        def transaction():
            yield cursor
        db = SimpleNamespace(transaction=transaction)
        inventory = Mock()
        inventory.connection.return_value = {'adapter': 'ascend'}
        from hive.hardware import AscendAdapter
        transport = Mock()
        transport.run.side_effect = DomainError('SSH 主机公钥未登记，请先核验主机指纹')
        adapter = AscendAdapter(transport)
        telemetry = Telemetry(db, inventory, {'ascend': adapter}, None, Settings())
        with self.assertRaises(DomainError):
            telemetry.collect_node('node')
        reasons = [call.args[1][0] for call in cursor.execute.call_args_list if 'reason=%s' in call.args[0]]
        self.assertTrue(reasons)
        self.assertTrue(all('主机公钥未登记' in reason for reason in reasons))

    def test_unknown_key_requires_confirmation_and_mismatch_fails(self):
        key = paramiko.RSAKey.generate(1024)
        with self.assertRaises(HostKeyRequired):
            RejectPolicy().missing_host_key(None, 'node', key)
        with self.assertRaises(DomainError):
            RejectPolicy('SHA256:wrong').missing_host_key(None, 'node', key)
        RejectPolicy(host_key_info(key)['fingerprint']).missing_host_key(None, 'node', key)

    def test_authentication_errors_are_actionable_without_echoing_passwords(self):
        error = ssh_error(paramiko.AuthenticationException('password=do-not-expose'))
        self.assertIn('认证失败', str(error))
        self.assertNotIn('do-not-expose', str(error))

    def test_basic_model_is_detected_and_check_does_not_enroll(self):
        key = paramiko.RSAKey.generate(1024)
        client = Mock()
        client.get_transport.return_value.get_remote_server_key.return_value = key
        @contextmanager
        def connection(node):
            yield client
        transport = Mock()
        transport._connection = connection
        transport.run.return_value = CommandResult(envelope({'boot': BOOT, 'uname': 'Linux host x86_64',
            'machine': 'x86_64', 'system': 'AC222', 'npu_smi': '/usr/local/bin/npu-smi'}), '', 0)
        with tempfile.TemporaryDirectory() as directory, patch('hive.onboarding.SSHTransport', return_value=transport):
            path = Path(directory) / 'known_hosts'
            onboarding = Onboarding(Settings(known_hosts=path))
            result = onboarding.check({'host': 'example', 'port': 2222})
            self.assertEqual(result['model'], 'AC222')
            self.assertEqual(result['metadata']['hardware_profile']['host_system']['architecture'], 'x86_64')
            self.assertFalse(path.exists())
            onboarding.check({'host': 'example', 'port': 2222}, enroll=True)
            self.assertTrue(paramiko.HostKeys(str(path)).check('[example]:2222', key))

    def test_failed_preflight_does_not_persist_node(self):
        from hive.api import create_app
        actor = SimpleNamespace(admin=True)
        services = SimpleNamespace(identity=Mock(), inventory=Mock(), onboarding=Mock())
        services.identity.current.return_value = actor
        services.onboarding.check.side_effect = DomainError('SSH 认证失败', 422)
        client = TestClient(create_app(services=services, settings=Settings(cookie_secure=False)))
        response = client.post('/api/nodes', json={'name': 'node', 'host': '10.0.0.1', 'password': 'secret', 'generation': 'A3'})
        self.assertEqual(response.status_code, 422)
        services.inventory.create.assert_not_called()

    def test_non_admin_cannot_scan_or_admit_nodes(self):
        from hive.api import create_app
        from hive.inventory import Inventory
        services = SimpleNamespace(identity=Mock(), inventory=Mock(), onboarding=Mock())
        services.identity.current.return_value = SimpleNamespace(admin=False)
        services.inventory.require_admin = Inventory.require_admin
        client = TestClient(create_app(services=services, settings=Settings(cookie_secure=False)))
        connection = {'host': '10.0.0.1', 'password': 'secret'}
        self.assertEqual(client.post('/api/nodes/check', json=connection).status_code, 403)
        self.assertEqual(client.post('/api/nodes', json={**connection, 'name': 'node', 'generation': 'A3'}).status_code, 403)
        services.onboarding.check.assert_not_called()

    def test_failed_authentication_never_saves_candidate_host_key(self):
        @contextmanager
        def connection(node):
            raise DomainError('SSH 认证失败', 422)
            yield
        transport = Mock()
        transport._connection = connection
        with tempfile.TemporaryDirectory() as directory, patch('hive.onboarding.SSHTransport', return_value=transport):
            path = Path(directory) / 'known_hosts'
            with self.assertRaises(DomainError):
                Onboarding(Settings(known_hosts=path)).check({'host': 'example'}, enroll=True)
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
