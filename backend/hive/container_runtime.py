"""Generic persistent attempts inside an identity-pinned Docker container.

No business commands, container creation recipes or scheduling decisions belong
here. Callers persist the returned attempt before launch and retain allocations
until close is explicitly confirmed. Transport errors are deliberately not
translated into successful completion or permission to retry a launch.
"""
import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
import shlex
import uuid

from .domain import DomainError


SCRIPTS = Path(__file__).parent / 'scripts'
LOG_CAP = 20 * 1024 * 1024
LOG_CHUNK = 65536
ROOT = '/var/tmp/hive/container-attempts'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
ATTEMPT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}')
HEX64 = re.compile(r'[0-9a-f]{64}')
BOOT = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
START = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z')


def _require(condition, message):
    if not condition:
        raise DomainError(message, 422)


def _identity(value, require_init=True):
    _require(isinstance(value, dict), 'Container identity is required')
    for key, pattern in (('container_name', NAME), ('container_id', HEX64),
                         ('host_boot_id', BOOT), ('started_at', START)):
        _require(isinstance(value.get(key), str) and pattern.fullmatch(value[key]),
                 'Invalid container identity: ' + key)
    _require(isinstance(value.get('image_id'), str) and
             re.fullmatch(r'sha256:[0-9a-f]{64}', value['image_id']), 'Invalid container image identity')
    if require_init:
        _require(isinstance(value.get('init_start_ticks'), str) and
                 re.fullmatch(r'[1-9][0-9]*', value['init_start_ticks']), 'Missing container init identity')
    return dict(value)


def _directory(identity, attempt_id):
    _require(isinstance(attempt_id, str) and ATTEMPT.fullmatch(attempt_id), 'Invalid attempt ID')
    return ROOT + '/' + identity['container_id'] + '/' + attempt_id


def _host_guard(identity, running=True):
    expected = ' '.join((identity['container_id'], identity['started_at'],
                         'true' if running else 'false', identity['image_id']))
    return ('test "$(cat /proc/sys/kernel/random/boot_id)" = ' + shlex.quote(identity['host_boot_id']) + '\n'
            'test "$(docker inspect --type container --format '
            + shlex.quote('{{.Id}} {{.State.StartedAt}} {{.State.Running}} {{.Image}}')
            + ' -- ' + shlex.quote(identity['container_id']) + ')" = ' + shlex.quote(expected) + '\n')


def _inside_guard(identity):
    return ('test "$(cat /proc/sys/kernel/random/boot_id)" = ' + shlex.quote(identity['host_boot_id']) + '\n'
            'raw=$(cat /proc/1/stat)\nread -ra fields <<<"${raw##*) }"\n'
            'test "${fields[19]}" = ' + shlex.quote(identity['init_start_ticks']) + '\n')


def _safe_directory(directory):
    # Refuse symlink ancestors before creating or opening protocol files.
    parts, checks = directory.strip('/').split('/'), []
    for index in range(len(parts)):
        checks.append('test ! -L ' + shlex.quote('/' + '/'.join(parts[:index + 1])))
    return '\n'.join(checks) + '\n'


class ContainerRuntime:
    def __init__(self, transport):
        self.transport = transport

    def _run(self, node, command, timeout=30):
        _require(len(command.encode()) <= 1024 * 1024, 'Container command exceeds SSH payload limit')
        result = self.transport.run(node, command, timeout=timeout)
        if result.code:
            # Scripts/environment may contain user secrets. Do not echo remote stderr.
            raise DomainError('Container operation failed or identity changed; completion unconfirmed', 503)
        return result.stdout.strip()

    def _exec(self, node, identity, script, timeout=30, check_init=True):
        identity = _identity(identity, require_init=check_init)
        marker = 'HIVE_CONTAINER_' + uuid.uuid4().hex
        inside = 'set -euo pipefail\numask 077\n'
        if check_init:
            inside += _inside_guard(identity)
        inside += script
        command = ('set -euo pipefail\n' + _host_guard(identity)
                   + 'docker exec -i --user 0 -- ' + shlex.quote(identity['container_id'])
                   + " bash -s -- <<'" + marker + "'\n" + inside + '\n' + marker + '\n'
                   + _host_guard(identity))
        return self._run(node, command, timeout)

    def read_file(self, node, identity, path, max_bytes=4 * 1024 * 1024):
        """Read a bounded regular container file without following symlink ancestors."""
        _require(isinstance(path,str) and path.startswith('/') and len(path)<=4096 and '\x00' not in path
                 and all(part not in {'.','..',''} for part in path[1:].split('/')), 'Invalid artifact path')
        _require(type(max_bytes) is int and 0 <= max_bytes <= 4 * 1024 * 1024, 'Invalid artifact size limit')
        quoted, chunk_size = shlex.quote(path), 1024 * 1024
        raw, expected = bytearray(), None
        for index in range(4):
            script = (_safe_directory(path) + 'test -f ' + quoted + '\n'
                      'size=$(stat -c %s -- ' + quoted + ')\n'
                      'test "$size" -le ' + str(max_bytes) + '\n'
                      'digest=$(sha256sum -- ' + quoted + " | cut -d ' ' -f 1)\n"
                      'printf "HIVE_FILE %s %s\\n" "$size" "$digest"\n'
                      'dd if=' + quoted + f' bs={chunk_size} skip={index} count=1 status=none | base64 -w0\n'
                      'test "$(stat -c %s -- ' + quoted + ')" = "$size"\n'
                      'test "$(sha256sum -- ' + quoted + ' | cut -d \' \' -f 1)" = "$digest"\n')
            output = self._exec(node,identity,script)
            header, _, encoded = output.partition('\n')
            match = re.fullmatch(r'HIVE_FILE ([0-9]+) ([0-9a-f]{64})',header)
            try:
                if not match or int(match[1]) > max_bytes:
                    raise ValueError()
                evidence = (int(match[1]),match[2])
                if expected is not None and evidence != expected:
                    raise ValueError()
                expected = evidence
                chunk = base64.b64decode(encoded,validate=True)
                if len(chunk) != min(chunk_size,expected[0]-len(raw)):
                    raise ValueError()
                raw.extend(chunk)
            except (ValueError,binascii.Error):
                raise DomainError('Artifact read is incomplete, oversized or changed during collection',503) from None
            if len(raw) == expected[0]:
                if hashlib.sha256(raw).hexdigest() != expected[1]:
                    raise DomainError('Artifact content digest does not match',503)
                return bytes(raw)
        raise DomainError('Artifact exceeds the supported size limit',422)

    def inspect(self, node, container_name):
        _require(isinstance(container_name, str) and NAME.fullmatch(container_name), 'Invalid container name')
        output = self._run(node, "set -euo pipefail\nprintf 'HIVE_CONTAINER_V1\\n'\n"
                           'cat /proc/sys/kernel/random/boot_id\n'
                           'docker inspect --type container -- ' + shlex.quote(container_name) + '\n')
        try:
            marker, boot, document = output.split('\n', 2)
            rows = json.loads(document)
            if marker != 'HIVE_CONTAINER_V1' or len(rows) != 1 or rows[0]['State']['Running'] is not True:
                raise ValueError('Not a unique running container')
            row = rows[0]
            if row['Name'] != '/' + container_name:
                raise ValueError('Container name changed')
            identity = _identity(dict(container_name=container_name, container_id=row['Id'],
                                      started_at=row['State']['StartedAt'], host_boot_id=boot,
                                      image_id=row['Image']), require_init=False)
        except (ValueError, KeyError, TypeError, IndexError):
            raise DomainError('Container identity inspection is incomplete', 503) from None
        output = self._exec(node, identity, 'raw=$(cat /proc/1/stat)\n'
                            'read -ra fields <<<"${raw##*) }"\nprintf "HIVE_INIT %s\\n" "${fields[19]}"\n',
                            check_init=False)
        if not re.fullmatch(r'HIVE_INIT [1-9][0-9]*', output):
            raise DomainError('Container init identity inspection is incomplete', 503)
        identity['init_start_ticks'] = output.split()[1]
        return identity

    def prepare(self, node, identity, attempt_id, script, environment, timeout_seconds, workdir):
        identity = _identity(identity)
        directory = _directory(identity, attempt_id)
        _require(isinstance(script, str) and script.strip() and '\x00' not in script
                 and len(script.encode()) <= 512 * 1024, 'Task script must contain 1–512 KiB of text')
        _require(type(timeout_seconds) is int and 1 <= timeout_seconds <= 604800, 'Invalid task timeout')
        _require(isinstance(workdir, str) and workdir.startswith('/') and '\x00' not in workdir
                 and '..' not in workdir.split('/') and len(workdir) <= 4096, 'Invalid container work directory')
        _require(isinstance(environment, dict) and all(isinstance(key, str) and
                 re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) and isinstance(value, str) and '\x00' not in value
                 for key, value in environment.items()), 'Invalid task environment')
        _require(len(json.dumps(environment, ensure_ascii=False).encode()) <= 65536, 'Task environment exceeds 64 KiB')
        job = '#!/usr/bin/env bash\nset -euo pipefail\ncd -- ' + shlex.quote(workdir) + '\n'
        job += ''.join('export ' + key + '=' + shlex.quote(value) + '\n' for key, value in sorted(environment.items()))
        job += script + '\n'
        files = {'runner.sh': (SCRIPTS / 'task_runner.sh').read_text(encoding='utf-8'),
                 'job.sh': job, 'timeout': str(timeout_seconds)}
        fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        command = _safe_directory(directory) + 'mkdir -p -- ' + shlex.quote(directory) + '\n'
        command += 'chmod 700 -- ' + shlex.quote(directory) + '\ncd -- ' + shlex.quote(directory) + '\n'
        command += 'test ! -L lock\nexec 9>lock\nflock -x 9\n'
        command += ('if test -e fingerprint; then\n test ! -L fingerprint\n'
                    ' test "$(cat fingerprint)" = ' + shlex.quote(fingerprint)
                    + "\n printf 'PREPARED\\n'\n exit\nfi\ntest ! -e intent\ntest ! -e closed\n")
        for name, content in files.items():
            encoded = base64.b64encode(content.encode()).decode()
            command += ('test ! -L ' + name + '\ntest ! -L ' + name + '.upload\n'
                        'printf %s ' + shlex.quote(encoded) + ' | base64 --decode >' + name + '.upload\n'
                        'mv -- ' + name + '.upload ' + name + '\n')
        command += ('test ! -L fingerprint.upload\nprintf %s ' + fingerprint
                    + " >fingerprint.upload\nmv -- fingerprint.upload fingerprint\nprintf 'PREPARED\\n'\n")
        if self._exec(node, identity, command) != 'PREPARED':
            raise DomainError('Container attempt preparation is unconfirmed', 503)
        return dict(identity=identity, attempt_id=attempt_id, remote_path=directory, fingerprint=fingerprint,
                    file_hashes={name: hashlib.sha256(content.encode()).hexdigest() for name, content in files.items()})

    def _attempt(self, attempt):
        _require(isinstance(attempt, dict), 'Attempt is required')
        identity = _identity(attempt.get('identity'))
        directory = _directory(identity, attempt.get('attempt_id'))
        _require(attempt.get('remote_path') == directory, 'Attempt directory does not match its identity')
        _require(isinstance(attempt.get('fingerprint'), str) and HEX64.fullmatch(attempt['fingerprint']),
                 'Invalid attempt fingerprint')
        return identity, directory

    def _control(self, node, attempt, action):
        identity, directory = self._attempt(attempt)
        marker = 'HIVE_CONTROL_' + uuid.uuid4().hex
        command = _safe_directory(directory)
        if action == 'launch':
            hashes = attempt.get('file_hashes')
            _require(isinstance(hashes, dict) and set(hashes) == {'runner.sh', 'job.sh', 'timeout'}
                     and all(isinstance(value, str) and HEX64.fullmatch(value) for value in hashes.values()),
                     'Prepared file hashes are required before launch')
            for name, digest in sorted(hashes.items()):
                path = shlex.quote(directory + '/' + name)
                command += ('test ! -L ' + path + '\ntest -f ' + path + '\n'
                            'test "$(sha256sum -- ' + path + " | cut -d ' ' -f 1)\" = " + digest + '\n')
        command += ('test ! -L ' + shlex.quote(directory + '/fingerprint') + '\n'
                   'test "$(cat ' + shlex.quote(directory + '/fingerprint') + ')" = '
                   + shlex.quote(attempt['fingerprint']) + '\n'
                   'bash -s -- ' + shlex.quote(directory) + ' ' + action + " <<'" + marker + "'\n"
                   + (SCRIPTS / 'task_control.sh').read_text(encoding='utf-8') + '\n' + marker + '\n')
        output = self._exec(node, identity, command)
        if action == 'status' and re.fullmatch(r'EXITED (?:0|[1-9][0-9]{0,2})', output):
            code = int(output.split()[1])
            if code <= 255:
                return {'status': 'EXITED', 'exit_code': code}
        allowed = {'launch': {'STARTING', 'EXISTING', 'CLOSED'},
                   'status': {'PREPARED', 'RUNNING', 'CLOSED', 'UNKNOWN'}, 'close': {'CLOSED', 'UNKNOWN'}}
        if output not in allowed[action]:
            raise DomainError('Container attempt control returned incomplete evidence', 503)
        return {'status': 'STARTING' if output == 'EXISTING' else output}

    def launch(self, node, attempt):
        return self._control(node, attempt, 'launch')

    def status(self, node, attempt):
        return self._control(node, attempt, 'status')

    def close(self, node, attempt):
        return self._control(node, attempt, 'close')

    def log(self, node, attempt, offset=0, limit=LOG_CHUNK):
        identity, directory = self._attempt(attempt)
        _require(type(offset) is int and 0 <= offset <= LOG_CAP and type(limit) is int
                 and 1 <= limit <= LOG_CHUNK, 'Invalid container log range')
        if offset == LOG_CAP:
            return dict(data=b'', next_offset=offset, eof=True, truncated=True, missing=False)
        limit = min(limit, LOG_CAP - offset)
        file = shlex.quote(directory + '/output.log')
        command = (_safe_directory(directory) + 'test ! -L ' + file + '\n'
                   'if test ! -e ' + file + "; then printf 'HIVE_LOG_MISSING\\n'; exit; fi\n"
                   'size=$(stat -c %s -- ' + file + ')\nprintf "HIVE_LOG %s\\n" "$size"\n'
                   'count=$((size - ' + str(offset) + '))\n((count >= 0))\n'
                   'if ((count > ' + str(limit) + ')); then count=' + str(limit) + '; fi\n'
                   'dd if=' + file + ' bs=1 skip=' + str(offset) + ' count="$count"'
                   + ' status=none | base64 -w 0\nprintf "\\n"\n')
        output = self._exec(node, identity, command)
        if output == 'HIVE_LOG_MISSING':
            return dict(data=b'', next_offset=offset, eof=False, truncated=False, missing=True)
        try:
            header, _, encoded = output.partition('\n')
            if not re.fullmatch(r'HIVE_LOG [0-9]+', header):
                raise ValueError('Invalid header')
            size = int(header.split()[1])
            data = base64.b64decode(encoded, validate=True)
            if size < offset or len(data) > limit or len(data) > size - offset:
                raise ValueError('Truncated or inconsistent log')
            if len(data) != min(limit, size - offset):
                raise ValueError('Incomplete log transfer')
        except (ValueError, binascii.Error):
            raise DomainError('Container log transfer is incomplete', 503) from None
        next_offset = offset + len(data)
        return dict(data=data, next_offset=next_offset, eof=next_offset >= min(size, LOG_CAP),
                    truncated=size >= LOG_CAP, missing=False)

    def stop(self, node, identity):
        """Stop exactly a platform-owned container, after its caller closes attempts."""
        identity = _identity(identity)
        # First validate PID 1 from inside; never treat stopping an SSH client as
        # stopping the container or its business processes.
        marker = 'HIVE_STOP_' + uuid.uuid4().hex
        stopped = ' '.join((identity['container_id'], identity['started_at'], 'false', identity['image_id']))
        command = ('set -euo pipefail\n'
                   + 'test "$(cat /proc/sys/kernel/random/boot_id)" = ' + shlex.quote(identity['host_boot_id']) + '\n'
                   + 'current=$(docker inspect --type container --format '
                   + shlex.quote('{{.Id}} {{.State.StartedAt}} {{.State.Running}} {{.Image}}')
                   + ' -- ' + shlex.quote(identity['container_id']) + ')\n'
                   + 'if test "$current" = ' + shlex.quote(stopped)
                   + "; then printf 'STOPPED\\n'; exit; fi\n" + _host_guard(identity)
                   + 'docker exec -i --user 0 -- ' + shlex.quote(identity['container_id'])
                   + " bash -s -- <<'" + marker + "'\nset -euo pipefail\n" + _inside_guard(identity)
                   + marker + '\n' + _host_guard(identity)
                   + 'docker stop --time 10 -- ' + shlex.quote(identity['container_id']) + ' >/dev/null\n'
                   + _host_guard(identity, running=False) + "printf 'STOPPED\\n'\n")
        if self._run(node, command, timeout=30) != 'STOPPED':
            raise DomainError('Container stop is unconfirmed', 503)
        return {'status': 'STOPPED'}
