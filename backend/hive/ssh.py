"""Bounded SSH transport. Host keys must be enrolled out of band in known_hosts."""
from contextlib import contextmanager
from collections import OrderedDict
import hashlib
import base64
import socket
import posixpath
import threading
import time
import uuid

import paramiko

from .domain import CommandResult, DomainError


def host_key_info(key):
    return {'algorithm': key.get_name(),
            'fingerprint': 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')}


class HostKeyRequired(DomainError):
    def __init__(self, key):
        super().__init__('SSH 主机公钥未登记，请先检测连接并核验主机指纹', 409)
        self.host_key = host_key_info(key)


class RejectPolicy(paramiko.RejectPolicy):
    def __init__(self, expected=None):
        self.expected = expected

    def missing_host_key(self, client, hostname, key):
        if not self.expected:
            raise HostKeyRequired(key)
        if self.expected != host_key_info(key)['fingerprint']:
            raise DomainError('SSH 主机指纹与确认值不一致，请核验服务器身份', 409)


def ssh_error(exc):
    if isinstance(exc, DomainError):
        return exc
    if isinstance(exc, paramiko.BadHostKeyException):
        return DomainError('SSH 主机公钥已变化，请由管理员核验并更新已登记公钥', 409)
    if isinstance(exc, paramiko.AuthenticationException):
        return DomainError('SSH 认证失败，请检查账号、密码及服务器密码登录配置', 422)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return DomainError('SSH 连接或操作超时，请检查网络、防火墙和 SSH 端口', 503)
    if isinstance(exc, (ConnectionError, paramiko.ssh_exception.NoValidConnectionsError, socket.gaierror)):
        return DomainError('无法连接 SSH，请检查服务器地址、端口和网络', 503)
    return DomainError('SSH 操作失败，请检查 SSH 服务和节点命令是否可用', 503)


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
               hashlib.sha256(node.get('password', '').encode()).digest(), node.get('host_key_fingerprint'))
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
                entry['client'] = client
                try:
                    client.load_host_keys(str(self.settings.known_hosts))
                except FileNotFoundError:
                    pass
                client.set_missing_host_key_policy(RejectPolicy(node.get('host_key_fingerprint')))
                client.connect(hostname=node['host'], port=int(node.get('port', 22)),
                               username=node.get('ssh_user', 'root'), password=node.get('password') or None,
                               timeout=self.settings.ssh_timeout, auth_timeout=self.settings.ssh_timeout,
                               banner_timeout=self.settings.ssh_timeout, allow_agent=False, look_for_keys=False)
                client.get_transport().set_keepalive(30)
            yield client
        except (OSError, EOFError, paramiko.SSHException, DomainError) as exc:
            if entry['client']:
                entry['client'].close()
                entry['client'] = None
            # Do not leak credentials/server-controlled error text; never replay a command.
            raise ssh_error(exc) from None
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
