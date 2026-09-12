"""Stateful SSH boundary for orchestration tests; no Hive service is mocked."""
import base64
import hashlib
import json
import re
import shlex
from hive.domain import CommandResult

BOOT = '11111111-2222-3333-4444-555555555555'


class Remote:
    def __init__(self):
        self.containers = {}
        self.attempts = {}
        self.events = []
        self.hold_jobs = False
        self.hold_services = False
        self.fail_jobs = False
        self.unknown_close = False
        self.bootstrap_failure = False

    def run(self, node, script, timeout=20):
        if '# HIVE_BOOTSTRAP_INSPECT' in script:
            return CommandResult('sha256:' + 'b' * 64 + '\n' + 'c' * 64 + '\n', '', 0)
        if '# HIVE_BOOTSTRAP_RUN' in script:
            name = re.search(r'# container_name (\S+)', script)[1]
            self.containers[name] = hashlib.sha256(name.encode()).hexdigest()
            self.events.append(('container', name))
            return CommandResult('', '', 1 if self.bootstrap_failure else 0)
        if 'HIVE_CONTAINER_V1' in script:
            name = shlex.split(script.splitlines()[-1])[-1]
            if name not in self.containers:
                return CommandResult('', '', 1)
            value = {'Name': '/' + name, 'Id': self.containers[name], 'Image': 'sha256:' + 'b' * 64,
                     'State': {'Running': True, 'StartedAt': '2026-09-12T00:00:00Z'}}
            return CommandResult('HIVE_CONTAINER_V1\n' + BOOT + '\n' + json.dumps([value]), '', 0)
        if 'HIVE_INIT' in script:
            return CommandResult('HIVE_INIT 100\n', '', 0)
        action = re.search(r'bash -s -- (/var/tmp/hive/container-attempts/\S+) (launch|status|close) ', script)
        if action:
            directory, operation = action.groups()
            attempt = self.attempts[directory]
            self.events.append((operation, directory))
            if operation == 'launch':
                attempt['launched'] = True
                return CommandResult('STARTING\n', '', 0)
            if operation == 'close':
                if self.unknown_close:
                    return CommandResult('UNKNOWN\n', '', 0)
                attempt['closed'] = True
                return CommandResult('CLOSED\n', '', 0)
            if attempt.get('closed'):
                return CommandResult('CLOSED\n', '', 0)
            if not attempt.get('launched'):
                return CommandResult('PREPARED\n', '', 0)
            if (self.hold_jobs and '# HIVE_PHASE steps' in attempt['script']) or (self.hold_services and '# HIVE_SERVICE' in attempt['script']):
                return CommandResult('RUNNING\n', '', 0)
            code = 7 if self.fail_jobs and '# HIVE_PHASE steps' in attempt['script'] else 0
            return CommandResult(f'EXITED {code}\n', '', 0)
        if '>job.sh.upload' in script:
            directory = re.search(r'cd -- (/var/tmp/hive/container-attempts/\S+)', script)[1]
            data = re.search(r'printf %s (\S+) \| base64 --decode >job.sh.upload', script)[1]
            self.attempts.setdefault(directory, {'script': base64.b64decode(data.strip("'")).decode()})
            return CommandResult('PREPARED\n', '', 0)
        if 'HIVE_LOG' in script:
            return CommandResult('HIVE_LOG 0\n', '', 0)
        if 'STOPPED' in script:
            self.events.append(('stop', node['id']))
            return CommandResult('STOPPED\n', '', 0)
        if '# HIVE_BOOTSTRAP_PROOF' in script:
            name = re.search(r'# container_name (\S+)', script)[1]
            return CommandResult(self.containers[name] + '\n', '', 1 if self.bootstrap_failure else 0)
        raise AssertionError('Unhandled SSH command: ' + script[:150])

    def put(self, node, path, content):
        self.events.append(('put', path))
