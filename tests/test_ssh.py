import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hive.config import Settings
from hive.domain import DomainError
from hive.ssh import SSHTransport


class Channel:
    def __init__(self, output=b'hello', code=0):
        self.output, self.code = output, code
        self.command, self.sent, self.closed = None, None, False

    def settimeout(self, timeout):
        pass

    def exec_command(self, command):
        self.command = command

    def sendall(self, data):
        self.sent = data

    def shutdown_write(self):
        pass

    def recv_ready(self):
        return bool(self.output)

    def recv(self, limit):
        value, self.output = self.output[:limit], self.output[limit:]
        return value

    def recv_stderr_ready(self):
        return False

    def recv_stderr(self, limit):
        return b''

    def exit_status_ready(self):
        return True

    def recv_exit_status(self):
        return self.code

    def close(self):
        self.closed = True


class SSHTests(unittest.TestCase):
    def transport(self, channel, limit=1024):
        client = Mock()
        client.get_transport.return_value.is_active.return_value = True
        client.get_transport.return_value.open_session.return_value = channel
        patcher = patch('hive.ssh.paramiko.SSHClient', return_value=client)
        patcher.start()
        self.addCleanup(patcher.stop)
        transport = SSHTransport(Settings(ssh_workers=1, known_hosts=Path('enrolled_hosts')), max_output=limit)
        self.addCleanup(transport.close)
        return transport, client

    def test_host_keys_are_required_and_script_never_enters_command(self):
        channel = Channel()
        transport, client = self.transport(channel)
        script = "printf '%s' 'user $(data)'\n"
        result = transport.run({'host': 'example', 'password': 'secret'}, script, timeout=10)
        self.assertEqual(result.stdout, 'hello')
        self.assertEqual(channel.sent, script.encode())
        self.assertNotIn('secret', channel.command)
        self.assertNotIn('user', channel.command)
        client.load_host_keys.assert_called_once_with('enrolled_hosts')
        self.assertEqual(type(client.set_missing_host_key_policy.call_args.args[0]).__name__, 'RejectPolicy')
        self.assertTrue(channel.closed)

    def test_output_overflow_discards_partial_success(self):
        channel = Channel(b'a' * 100)
        transport, _ = self.transport(channel, limit=16)
        result = transport.run({'host': 'example'}, 'echo x')
        self.assertEqual(result.code, 125)
        self.assertEqual(result.stdout, '')
        self.assertTrue(channel.closed)

    def test_same_node_reuses_connection_without_replaying_command(self):
        channel = Channel()
        transport, client = self.transport(channel)
        transport.run({'host': 'example'}, 'one')
        transport.run({'host': 'example'}, 'two')
        self.assertEqual(client.connect.call_count, 1)
        self.assertEqual(client.get_transport.return_value.open_session.call_count, 2)

    def test_side_effect_failure_is_not_retried_and_errors_redacted(self):
        channel = Channel()
        transport, client = self.transport(channel)
        channel.sendall = Mock(side_effect=OSError('sensitive-secret'))
        with self.assertRaises(DomainError) as error:
            transport.run({'host': 'example', 'password': 'sensitive-secret'}, 'side_effect')
        self.assertNotIn('sensitive-secret', str(error.exception))
        self.assertEqual(channel.sendall.call_count, 1)

    def test_invalid_sftp_paths_are_rejected_before_ssh(self):
        transport = SSHTransport(Settings())
        for path in ('relative', '/safe/../unsafe', '/nul\x00'):
            with self.assertRaises(ValueError):
                transport.read({}, path)

    def test_closed_transport_rejects_new_work(self):
        transport = SSHTransport(Settings())
        transport.close()
        with self.assertRaises(DomainError):
            transport.run({'host': 'example'}, 'true')


if __name__ == '__main__':
    unittest.main()
