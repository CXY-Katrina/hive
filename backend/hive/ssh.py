"""Bounded SSH transport. Host keys must be enrolled out of band in known_hosts."""
from contextlib import contextmanager
from collections import OrderedDict
import hashlib
import posixpath
import threading
import time
import uuid

import paramiko

from .domain import CommandResult, DomainError


class SSHTransport:
    def __init__(self, settings, max_output=4 * 1024 * 1024):
        self.settings = settings
        self.max_output = max_output
        self._condition = threading.Condition()
        self._clients = OrderedDict()
        self._closed = False

    @contextmanager
    def _connection(self, node):
        key = (node['host'], int(node.get('port', 22)), node.get('ssh_user', 'root'),
               hashlib.sha256(node.get('password', '').encode()).digest())
        deadline = time.monotonic() + self.settings.ssh_timeout
        with self._condition:
            while True:
                if self._closed:
                    raise DomainError('SSH transport is closed', 503)
                entry = self._clients.get(key)
                if entry and not entry['busy']:
                    entry['busy'] = True
                    self._clients.move_to_end(key)
                    break
                if not entry and len(self._clients) >= self.settings.ssh_workers:
                    idle = next((k for k, v in self._clients.items() if not v['busy']), None)
                    if idle is not None:
                        old = self._clients.pop(idle)
                        if old['client']:
                            old['client'].close()
                if not entry and len(self._clients) < self.settings.ssh_workers:
                    entry = {'client': None, 'busy': True}
                    self._clients[key] = entry
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DomainError('SSH capacity wait timed out', 503)
                self._condition.wait(remaining)
        try:
            client = entry['client']
            if client is None or not client.get_transport() or not client.get_transport().is_active():
                if client:
                    client.close()
                client = paramiko.SSHClient()
                client.load_host_keys(str(self.settings.known_hosts))
                client.set_missing_host_key_policy(paramiko.RejectPolicy())
                entry['client'] = client
                client.connect(hostname=node['host'], port=int(node.get('port', 22)),
                               username=node.get('ssh_user', 'root'), password=node.get('password') or None,
                               timeout=self.settings.ssh_timeout, auth_timeout=self.settings.ssh_timeout,
                               banner_timeout=self.settings.ssh_timeout, allow_agent=False, look_for_keys=False)
                client.get_transport().set_keepalive(30)
            yield client
        except (OSError, EOFError, paramiko.SSHException):
            if entry['client']:
                entry['client'].close()
                entry['client'] = None
            # Do not leak credentials/server-controlled error text; never replay a command.
            raise DomainError('SSH operation failed; verify connectivity, credentials and enrolled host key', 503) from None
        finally:
            with self._condition:
                entry['busy'] = False
                self._condition.notify_all()

    def run(self, node, script, timeout=20):
        if not isinstance(timeout, int) or not 1 <= timeout <= 3600:
            raise ValueError('Invalid command timeout')
        payload = script.encode('utf-8')
        if len(payload) > 1024 * 1024:
            raise ValueError('SSH script exceeds 1 MiB')
        with self._connection(node) as client:
            channel = client.get_transport().open_session(timeout=self.settings.ssh_timeout)
            try:
                channel.settimeout(timeout)
                # Only validated integer enters the remote command. All script bytes use stdin.
                channel.exec_command(f'timeout --signal=TERM --kill-after=2 {timeout}s bash -s --')
                channel.sendall(payload)
                channel.shutdown_write()
                out, err = bytearray(), bytearray()
                deadline = time.monotonic() + timeout + 3
                while True:
                    for ready, receive, buffer in ((channel.recv_ready, channel.recv, out),
                                                    (channel.recv_stderr_ready, channel.recv_stderr, err)):
                        if ready():
                            buffer.extend(receive(32768))
                            if len(out) + len(err) > self.max_output:
                                return CommandResult('', 'SSH output limit exceeded; result incomplete', 125)
                    if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                        return CommandResult(out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace'),
                                             channel.recv_exit_status())
                    if time.monotonic() >= deadline:
                        return CommandResult('', 'SSH command timed out; result incomplete', 124)
                    time.sleep(0.01)
            finally:
                channel.close()

    @staticmethod
    def _path(path):
        if not isinstance(path, str) or not path.startswith('/') or '\x00' in path or '..' in path.split('/'):
            raise ValueError('Expected absolute remote path without parent traversal')
        return path

    def put(self, node, path, content):
        path = self._path(path)
        if len(content) > 16 * 1024 * 1024:
            raise ValueError('SFTP upload exceeds 16 MiB')
        temporary = posixpath.join(posixpath.dirname(path), '.hive-upload-' + uuid.uuid4().hex)
        with self._connection(node) as client:
            with client.open_sftp() as sftp:
                sftp.get_channel().settimeout(self.settings.ssh_timeout)
                try:
                    with sftp.file(temporary, 'wx') as handle:
                        handle.set_pipelined(False)
                        handle.write(content)
                        handle.flush()
                    sftp.chmod(temporary, 0o600)
                    # OpenSSH extension gives atomic replacement; unsupported servers fail closed.
                    sftp.posix_rename(temporary, path)
                finally:
                    try:
                        sftp.remove(temporary)
                    except OSError:
                        pass

    def read(self, node, path, offset=0, limit=65536):
        self._path(path)
        if offset < 0 or not 0 <= limit <= self.max_output:
            raise ValueError('Invalid SFTP read range')
        with self._connection(node) as client:
            with client.open_sftp() as sftp:
                sftp.get_channel().settimeout(self.settings.ssh_timeout)
                with sftp.file(path, 'rb') as handle:
                    handle.seek(offset)
                    return handle.read(limit)

    def close(self):
        with self._condition:
            self._closed = True
            for entry in self._clients.values():
                if entry['client']:
                    entry['client'].close()
            self._condition.notify_all()
