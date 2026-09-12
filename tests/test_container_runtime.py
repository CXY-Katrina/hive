"""Container runtime contract exercised only through the public SSH boundary."""
import base64
import json
import shutil
import subprocess
import unittest

from hive.container_runtime import ContainerRuntime
from hive.domain import CommandResult, DomainError


IDENTITY = dict(container_name='hive-test', container_id='a' * 64,
                started_at='2026-09-12T00:00:00.123456789Z',
                host_boot_id='11111111-2222-3333-4444-555555555555',
                image_id='sha256:' + 'b' * 64, init_start_ticks='100')
NODE = {'id': 'node-one'}


class SSH:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def run(self, node, script, timeout=20):
        self.calls.append((node, script, timeout))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, CommandResult) else CommandResult(reply, '', 0)


def attempt():
    return dict(identity=dict(IDENTITY), attempt_id='attempt-1',
                remote_path='/var/tmp/hive/container-attempts/' + IDENTITY['container_id'] + '/attempt-1',
                fingerprint='c' * 64, file_hashes={name: 'd' * 64 for name in ('runner.sh', 'job.sh', 'timeout')})


class ContainerRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('bash'), 'Bash is required for shell syntax checks')
    def test_generated_scripts_have_valid_bash_syntax(self):
        ssh = SSH('PREPARED\n', 'STARTING\n', 'RUNNING\n', 'CLOSED\n', 'HIVE_LOG 0\n', 'STOPPED\n')
        runtime = ContainerRuntime(ssh)
        item = runtime.prepare(NODE, IDENTITY, 'attempt-1', "printf '%s' \"$VALUE\"", {'VALUE': "a'b\n$(false)"}, 30, '/tmp/a b')
        runtime.launch(NODE, item)
        runtime.status(NODE, item)
        runtime.close(NODE, item)
        runtime.log(NODE, item)
        runtime.stop(NODE, IDENTITY)
        for _, script, _ in ssh.calls:
            result = subprocess.run([shutil.which('bash'), '-n'], input=script, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_inspect_freezes_complete_running_identity(self):
        row = {'Id': IDENTITY['container_id'], 'Name': '/hive-test', 'Image': IDENTITY['image_id'],
               'State': {'Running': True, 'StartedAt': IDENTITY['started_at']}}
        ssh = SSH('HIVE_CONTAINER_V1\n' + IDENTITY['host_boot_id'] + '\n' + json.dumps([row]),
                  'HIVE_INIT 100\n')
        self.assertEqual(ContainerRuntime(ssh).inspect(NODE, 'hive-test'), IDENTITY)
        self.assertIn(IDENTITY['container_id'], ssh.calls[1][1])

    def test_inspect_rejects_invalid_name_without_ssh(self):
        ssh = SSH()
        for name in ('../bad', 'name;touch /tmp/x', '--all', ''):
            with self.assertRaises(DomainError):
                ContainerRuntime(ssh).inspect(NODE, name)
        self.assertEqual(ssh.calls, [])

    def test_inspect_rejects_short_id_stopped_container_and_restart_during_inspection(self):
        for overrides in ({'Id': 'abc123'}, {'State': {'Running': False, 'StartedAt': IDENTITY['started_at']}},
                          {'Name': '/replacement'}):
            row = {'Id': IDENTITY['container_id'], 'Name': '/hive-test', 'Image': IDENTITY['image_id'],
                   'State': {'Running': True, 'StartedAt': IDENTITY['started_at']}, **overrides}
            ssh = SSH('HIVE_CONTAINER_V1\n' + IDENTITY['host_boot_id'] + '\n' + json.dumps([row]))
            with self.assertRaises(DomainError):
                ContainerRuntime(ssh).inspect(NODE, 'hive-test')
        row = {'Id': IDENTITY['container_id'], 'Name': '/hive-test', 'Image': IDENTITY['image_id'],
               'State': {'Running': True, 'StartedAt': IDENTITY['started_at']}}
        ssh = SSH('HIVE_CONTAINER_V1\n' + IDENTITY['host_boot_id'] + '\n' + json.dumps([row]),
                  CommandResult('HIVE_INIT 999\n', '', 1))
        with self.assertRaises(DomainError):
            ContainerRuntime(ssh).inspect(NODE, 'hive-test')

    def test_prepare_returns_persistable_attempt_and_never_runs_payload_on_host(self):
        ssh = SSH('PREPARED\n')
        result = ContainerRuntime(ssh).prepare(NODE, IDENTITY, 'attempt-1', 'printf hello',
                                               {'TEXT': "$(touch /tmp/bad)'"}, 60, '/workspace')
        self.assertEqual(result['remote_path'], attempt()['remote_path'])
        self.assertEqual(result['identity'], IDENTITY)
        self.assertEqual(len(result['fingerprint']), 64)
        command = ssh.calls[0][1]
        self.assertIn('docker exec -i', command)
        self.assertIn(IDENTITY['container_id'], command)
        self.assertNotIn('printf hello', command)
        self.assertNotIn('touch /tmp/bad', command)

    def test_prepare_invalid_inputs_do_not_contact_node(self):
        for change in ({'attempt_id': '../escape'}, {'script': ''}, {'timeout_seconds': True},
                       {'environment': {'BAD;': 'value'}}, {'workdir': '/tmp/../escape'}):
            values = dict(attempt_id='attempt-1', script='true', environment={}, timeout_seconds=30, workdir='/tmp')
            values.update(change)
            ssh = SSH()
            with self.assertRaises(DomainError):
                ContainerRuntime(ssh).prepare(NODE, IDENTITY, **values)
            self.assertEqual(ssh.calls, [])

    def test_failed_prepare_does_not_report_prepared(self):
        ssh = SSH(CommandResult('', 'identity mismatch', 73))
        with self.assertRaises(DomainError):
            ContainerRuntime(ssh).prepare(NODE, IDENTITY, 'attempt-1', 'true', {}, 30, '/tmp')

    def test_prepare_fingerprint_is_stable_and_changes_with_execution_inputs(self):
        runtime = ContainerRuntime(SSH('PREPARED\n', 'PREPARED\n', 'PREPARED\n'))
        first = runtime.prepare(NODE, IDENTITY, 'attempt-1', 'true', {'B': '2', 'A': '1'}, 30, '/tmp')
        same = runtime.prepare(NODE, IDENTITY, 'attempt-1', 'true', {'A': '1', 'B': '2'}, 30, '/tmp')
        changed = runtime.prepare(NODE, IDENTITY, 'attempt-1', 'false', {'A': '1', 'B': '2'}, 30, '/tmp')
        self.assertEqual(first['fingerprint'], same['fingerprint'])
        self.assertNotEqual(first['fingerprint'], changed['fingerprint'])

    def test_launch_handoff_already_persisted_is_not_claimed_as_running(self):
        self.assertEqual(ContainerRuntime(SSH('EXISTING\n')).launch(NODE, attempt()), {'status': 'STARTING'})

    def test_launch_requires_actual_prepared_file_digests(self):
        ssh = SSH('PREPARED\n', 'STARTING\n')
        runtime = ContainerRuntime(ssh)
        item = runtime.prepare(NODE, IDENTITY, 'attempt-1', 'true', {}, 30, '/tmp')
        self.assertEqual(set(item['file_hashes']), {'runner.sh', 'job.sh', 'timeout'})
        runtime.launch(NODE, item)
        for name, digest in item['file_hashes'].items():
            self.assertIn('sha256sum -- ' + item['remote_path'] + '/' + name, ssh.calls[-1][1])
            self.assertIn(digest, ssh.calls[-1][1])
        ssh = SSH(CommandResult('', 'checksum mismatch', 1))
        with self.assertRaises(DomainError):
            ContainerRuntime(ssh).launch(NODE, item)

    def test_missing_file_evidence_blocks_launch_but_does_not_block_closing(self):
        item = attempt()
        del item['file_hashes']
        ssh = SSH('CLOSED\n')
        with self.assertRaises(DomainError):
            ContainerRuntime(ssh).launch(NODE, item)
        self.assertEqual(ssh.calls, [])
        self.assertEqual(ContainerRuntime(ssh).close(NODE, item), {'status': 'CLOSED'})

    def test_public_control_statuses_and_exit_code(self):
        ssh = SSH('STARTING\n', 'RUNNING\n', 'EXITED 124\n', 'UNKNOWN\n', 'CLOSED\n')
        runtime = ContainerRuntime(ssh)
        self.assertEqual(runtime.launch(NODE, attempt()), {'status': 'STARTING'})
        self.assertEqual(runtime.status(NODE, attempt()), {'status': 'RUNNING'})
        self.assertEqual(runtime.status(NODE, attempt()), {'status': 'EXITED', 'exit_code': 124})
        self.assertEqual(runtime.close(NODE, attempt()), {'status': 'UNKNOWN'})
        self.assertEqual(runtime.close(NODE, attempt()), {'status': 'CLOSED'})
        for _, command, _ in ssh.calls:
            self.assertIn('docker exec -i', command)
            self.assertIn(IDENTITY['host_boot_id'], command)
            self.assertIn(IDENTITY['started_at'], command)

    def test_disconnected_or_failed_close_cannot_confirm_resource_release(self):
        for reply in (DomainError('SSH disconnected', 503), CommandResult('CLOSED\n', '', 73),
                      'unexpected noise\nCLOSED\n'):
            with self.assertRaises(DomainError):
                ContainerRuntime(SSH(reply)).close(NODE, attempt())

    def test_invalid_exit_status_is_not_accepted(self):
        for reply in ('EXITED 999\n', 'EXITED x\n', '', 'RUNNING\nRUNNING\n'):
            with self.assertRaises(DomainError):
                ContainerRuntime(SSH(reply)).status(NODE, attempt())

    def test_attempt_path_cannot_escape_its_container_namespace(self):
        bad = attempt()
        bad['remote_path'] = '/var/tmp/other-task'
        ssh = SSH()
        with self.assertRaises(DomainError):
            ContainerRuntime(ssh).close(NODE, bad)
        self.assertEqual(ssh.calls, [])

    def test_logs_resume_with_bounded_bytes(self):
        payload = '中文'.encode()
        ssh = SSH('HIVE_LOG 12\n' + base64.b64encode(payload).decode() + '\n')
        result = ContainerRuntime(ssh).log(NODE, attempt(), offset=6, limit=6)
        self.assertEqual(result, dict(data=payload, next_offset=12, eof=True, truncated=False, missing=False))
        self.assertIn('docker exec -i', ssh.calls[0][1])

    def test_logs_reject_offset_rewind_and_oversized_or_invalid_responses(self):
        for reply in ('HIVE_LOG 1\nYQ==\n', 'HIVE_LOG 9\nYWJj\n', 'not a log'):
            with self.assertRaises(DomainError):
                ContainerRuntime(SSH(reply)).log(NODE, attempt(), offset=2, limit=2)

    def test_log_limit_does_not_contact_node_after_cap(self):
        ssh = SSH()
        result = ContainerRuntime(ssh).log(NODE, attempt(), offset=20 * 1024 * 1024)
        self.assertTrue(result['truncated'])
        self.assertTrue(result['eof'])
        self.assertEqual(ssh.calls, [])

    def test_missing_log_is_distinct_from_an_existing_empty_log(self):
        runtime = ContainerRuntime(SSH('HIVE_LOG_MISSING\n', 'HIVE_LOG 0\n'))
        missing = runtime.log(NODE, attempt())
        self.assertTrue(missing['missing'])
        self.assertFalse(missing['eof'])
        empty = runtime.log(NODE, attempt())
        self.assertFalse(empty['missing'])
        self.assertTrue(empty['eof'])

    def test_stop_requires_verified_identity_and_checks_remote_result(self):
        ssh = SSH('STOPPED\n')
        self.assertEqual(ContainerRuntime(ssh).stop(NODE, IDENTITY), {'status': 'STOPPED'})
        self.assertIn('docker stop', ssh.calls[0][1])
        self.assertIn(IDENTITY['started_at'], ssh.calls[0][1])
        with self.assertRaises(DomainError):
            ContainerRuntime(SSH(CommandResult('STOPPED\n', '', 73))).stop(NODE, IDENTITY)


if __name__ == '__main__':
    unittest.main()
