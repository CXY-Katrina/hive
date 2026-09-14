"""Workflow HTTP contract, disposable MySQL and simulated GitHub/SSH boundaries."""
import base64
import copy
import hashlib
import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlparse

from fastapi.testclient import TestClient
from hive.api import create_app
from hive.services import build_services
from hive.domain import SYSTEM
from hive.domain import DeviceSample, Snapshot, now
from tests.workflow_remote import Remote, BOOT
from tests import test_integration as database_tests


ASCEND_SHA = 'a' * 40
VLLM_SHA = 'b' * 40
FILES = {'.github/vllm-main-verified.commit': VLLM_SHA + '\n',
         'scripts/job.sh': '#!/bin/bash\nprintf "hello\\n"\n',
         'scripts/check.py': 'print("checked")\n',
         'configs/test.yaml': 'message: hello\n'}


def github_http(request, timeout=20):
    url = request.full_url if hasattr(request, 'full_url') else request
    path = unquote(urlparse(url).path)
    if '/pulls/' in path:
        value = {'number': 42, 'head': {'sha': ASCEND_SHA},
                 'base': {'repo': {'full_name': 'vllm-project/vllm-ascend'}},
                 'html_url': 'https://github.com/vllm-project/vllm-ascend/pull/42'}
    elif '/git/trees/' in path:
        value = {'truncated': False, 'tree': [{'path': key, 'type': 'blob', 'mode': '100644'} for key in FILES]}
    elif '/contents/' in path:
        filename = path.split('/contents/', 1)[1]
        data = FILES[filename].encode()
        value = {'type': 'file', 'path': filename, 'encoding': 'base64', 'size': len(data),
                 'content': base64.b64encode(data).decode(),
                 'sha': hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()}
    else:
        raise AssertionError('Unexpected external HTTP: ' + path)
    response = io.BytesIO(json.dumps(value).encode())
    response.status = 200
    response.headers = {}
    return response


def payload():
    return {'idempotency_key': 'workflow-one', 'name': 'PR workflow',
            'source': {'pr': 42, 'head_sha': ASCEND_SHA, 'vllm_sha': VLLM_SHA},
            'resource': {'generation': 'A2', 'mode': 'partial', 'machine_count': 1, 'cards_per_node': 2},
            'retain_minutes': 30,
            'environments': [{'alias': alias, 'role': role, 'node_alias': 'node0', 'image': 'example/image:fixed',
                              'bootstrap': {'type': 'shell', 'external': True,
                                            'path': '/mnt/share/c00814587/start-docker-A3.sh',
                                            'args': ['${image}', '${container_name}']}}
                             for alias, role in [('server_env', 'server'), ('client_env', 'client')]],
            'jobs': [{'id': 'first', 'name': 'First', 'environment': 'server_env', 'npu_count': 1,
                      'steps': [{'type': 'shell', 'path': 'scripts/job.sh'}]},
                     {'id': 'second', 'name': 'Second', 'environment': 'client_env', 'npu_count': 0,
                      'depends_on': [{'job_id': 'first', 'condition': 'succeeded'}],
                      'steps': [{'type': 'python', 'path': 'scripts/check.py'}]}]}


@unittest.skipUnless(os.getenv('HIVE_TEST_MYSQL_PORT'), 'Needs isolated MySQL')
class WorkflowHTTP(unittest.TestCase):
    setUpClass = classmethod(database_tests.MySQLIntegration.setUpClass.__func__)
    tearDownClass = classmethod(database_tests.MySQLIntegration.tearDownClass.__func__)

    def setUp(self):
        with self.db.transaction() as c:
            for table in ('workflow_jobs', 'workflows', 'workflow_environments', 'workflow_spaces'):
                c.execute('SHOW TABLES LIKE %s', (table,))
                if c.fetchone():
                    c.execute('DELETE FROM ' + table)
            c.execute("SHOW TABLES LIKE 'workflow_claims'")
            if c.fetchone():
                c.execute('DELETE FROM workflow_claims')
            for table in ('audit_events', 'allocation_devices', 'device_ownership', 'resource_requests', 'device_samples', 'devices', 'nodes', 'sessions', 'users'):
                c.execute('DELETE FROM ' + table)
        self.http_patch = patch('urllib.request.urlopen', github_http)
        self.http_patch.start()
        self.s = build_services(self.settings)
        self.s.sources.opener = github_http
        self.client = TestClient(create_app(self.s, self.settings))
        actor = self.client.post('/api/session', json={'username': 'alice'}).json()
        if hasattr(self.s.identity, 'permissions'):
            self.s.identity.permissions(SYSTEM, actor['id'], {'can_request': True, 'can_view_credentials': True})

    def tearDown(self):
        self.client.close()
        self.http_patch.stop()
        self.s.transport.close()
        self.s.telemetry_transport.close()
        self.s.benchmark_transport.close()

    def test_same_machine_roles_submit_once_and_read_through_http(self):
        response = self.client.post('/api/workflows', json=payload())
        self.assertEqual(response.status_code, 201, response.text)
        task = response.json()
        again = self.client.post('/api/workflows', json=payload())
        self.assertEqual(again.json()['id'], task['id'])
        spaces = self.client.get('/api/spaces').json()
        self.assertEqual(len(spaces), 1)
        self.assertEqual([e['node_alias'] for e in spaces[0]['environments']], ['node0', 'node0'])
        requests = self.client.get('/api/requests').json()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['spec']['machine_count'], 1)
        detail = self.client.get('/api/workflows/' + task['id']).json()
        self.assertEqual(detail['spec']['source']['head_sha'], ASCEND_SHA)
        self.assertEqual(len(detail['jobs']), 2)
        self.assertEqual(detail['status'], 'QUEUED')

    def test_invalid_graph_and_ambiguous_bindings_reject_before_allocating(self):
        cases = []
        cyclic = payload()
        cyclic['jobs'][0]['depends_on'] = [{'job_id': 'second'}]
        cases.append(cyclic)
        unknown = payload()
        unknown['jobs'][0]['environment'] = 'missing'
        cases.append(unknown)
        wrong_node = payload()
        wrong_node['environments'][1]['node_alias'] = 'node1'
        cases.append(wrong_node)
        yaml = payload()
        yaml['jobs'][0]['steps'] = [{'type': 'yaml', 'path': 'configs/test.yaml'}]
        cases.append(yaml)
        for case in cases:
            with self.subTest(case=case):
                response = self.client.post('/api/workflows', json=case)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get('/api/requests').json(), [])

    def test_cancel_during_environment_preparation_closes_owned_install_attempts(self):
        node = self.s.inventory.create(SYSTEM, {'name': 'test-node', 'host': '10.0.0.1', 'password': 'test-only', 'generation': 'A2', 'model': 'test'})
        self.s.telemetry.ingest(node['id'], Snapshot([DeviceSample(str(i), str(i), '0', str(i), 64 * 1024**3, 0, 0, 'OK', process_complete=True) for i in range(2)], BOOT, now()))
        self.s.workflows.transport = Remote()
        task = self.client.post('/api/workflows', json=payload()).json()
        space = self.client.get('/api/spaces').json()[0]
        req = self.s.resources.reserve(space['request_id'])
        self.s.resources.deliver(req['id'], req['version'])
        self.s.workflows.tick_space(space['id'])
        self.client.post('/api/workflows/' + task['id'] + '/cancel')
        self.s.workflows.tick_space(space['id'])
        self.assertEqual(self.client.get('/api/workflows/' + task['id']).json()['status'], 'CANCELLED')
        self.assertEqual(self.s.resources.get(req['id'])['status'], 'RELEASING')

    def test_submission_freezes_edited_pr_files_and_rejects_forged_source(self):
        changed = payload()
        changed['jobs'][0]['steps'][0]['uploaded_content'] = 'echo changed'
        changed['idempotency_key'] = 'edited-pr-file'
        edited = self.client.post('/api/workflows', json=changed)
        self.assertEqual(edited.status_code, 201, edited.text)
        self.assertEqual(edited.json()['spec']['files']['scripts/job.sh']['content'], 'echo changed')
        forged = payload()
        forged['source']['head_sha'] = 'c' * 40
        self.assertEqual(self.client.post('/api/workflows', json=forged).status_code, 409)
        accepted = self.client.post('/api/workflows', json=payload())
        self.assertEqual(accepted.status_code, 201, accepted.text)
        files = accepted.json()['spec']['files']
        self.assertEqual(files['scripts/job.sh']['sha256'], hashlib.sha256(FILES['scripts/job.sh'].encode()).hexdigest())

    def test_reuse_keeps_one_request_and_enforces_owner_and_cancel(self):
        first = self.client.post('/api/workflows', json=payload()).json()
        second = payload()
        second.update(idempotency_key='second-task', space_id=first['space_id'])
        second.pop('resource')
        second['environments'] = []
        response = self.client.post('/api/workflows', json=second)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['space_id'], first['space_id'])
        self.assertEqual(len(self.client.get('/api/requests').json()), 1)
        self.assertEqual(self.client.post('/api/workflows/' + first['id'] + '/cancel').status_code, 200)
        self.assertTrue(self.client.get('/api/workflows/' + first['id']).json()['cancel_requested'])
        self.assertFalse(self.client.get('/api/workflows/' + response.json()['id']).json()['cancel_requested'])
        self.client.post('/api/session', json={'username': 'bob'})
        second['idempotency_key'] = 'bob-reuse'
        self.assertEqual(self.client.post('/api/workflows', json=second).status_code, 403)
        self.assertEqual(self.client.get('/api/workflows/' + first['id']).status_code, 403)

    def test_worker_runs_container_steps_and_reuses_prepared_environments(self):
        node = self.s.inventory.create(SYSTEM, {'name': 'test-node', 'host': '10.0.0.1', 'password': 'test-only', 'generation': 'A2', 'model': 'test'})
        self.s.telemetry.ingest(node['id'], Snapshot([DeviceSample(str(i), str(i), '0', str(i), 64 * 1024**3, 0, 0, 'OK', process_complete=True) for i in range(2)], BOOT, now()))
        remote = Remote()
        self.s.workflows.transport = remote
        first = self.client.post('/api/workflows', json=payload()).json()
        space = self.client.get('/api/spaces').json()[0]
        req = self.s.resources.reserve(space['request_id'])
        self.assertIsNotNone(req)
        self.assertTrue(self.s.resources.deliver(req['id'], req['version']))
        for _ in range(35):
            self.s.workflows.tick_space(space['id'])
        result = self.client.get('/api/workflows/' + first['id']).json()
        self.assertEqual(result['status'], 'SUCCEEDED', result)
        self.assertEqual([j['status'] for j in result['jobs']], ['SUCCEEDED', 'SUCCEEDED'])
        second = payload()
        second.update(idempotency_key='reuse-after-run', space_id=space['id'], environments=[])
        second.pop('resource')
        accepted = self.client.post('/api/workflows', json=second)
        self.assertEqual(accepted.status_code, 201, accepted.text)
        for _ in range(20):
            self.s.workflows.tick_space(space['id'])
        self.assertEqual(self.client.get('/api/workflows/' + accepted.json()['id']).json()['status'], 'SUCCEEDED')
        self.assertEqual(len([e for e in remote.events if e[0] == 'container']), 2)

    def test_parallel_card_claims_and_unknown_cancel_preserve_ownership(self):
        node = self.s.inventory.create(SYSTEM, {'name': 'test-node', 'host': '10.0.0.1', 'password': 'test-only', 'generation': 'A2', 'model': 'test'})
        self.s.telemetry.ingest(node['id'], Snapshot([DeviceSample(str(i), str(i), '0', str(i), 64 * 1024**3, 0, 0, 'OK', process_complete=True) for i in range(2)], BOOT, now()))
        remote = Remote()
        remote.hold_jobs = True
        self.s.workflows.transport = remote
        body = payload()
        body['jobs'][1].update(depends_on=[], npu_count=1)
        third = copy.deepcopy(body['jobs'][1])
        third.update(id='third', name='Third')
        body['jobs'].append(third)
        task = self.client.post('/api/workflows', json=body).json()
        space = self.client.get('/api/spaces').json()[0]
        req = self.s.resources.reserve(space['request_id'])
        self.s.resources.deliver(req['id'], req['version'])
        for _ in range(12):
            self.s.workflows.tick_space(space['id'])
        detail = self.client.get('/api/workflows/' + task['id']).json()
        self.assertEqual([j['status'] for j in detail['jobs']], ['RUNNING', 'RUNNING', 'PENDING'])
        self.client.post('/api/workflows/' + task['id'] + '/cancel')
        remote.unknown_close = True
        self.s.workflows.tick_space(space['id'])
        self.assertEqual(self.client.get('/api/workflows/' + task['id']).json()['status'], 'CANCELLING')
        self.assertEqual(self.s.resources.get(req['id'])['status'], 'ACTIVE')
        remote.unknown_close = False
        for _ in range(3):
            self.s.workflows.tick_space(space['id'])
        self.assertEqual(self.client.get('/api/workflows/' + task['id']).json()['status'], 'CANCELLED')
        self.client.post('/api/spaces/' + space['id'] + '/close')
        self.s.workflows.tick_space(space['id'])
        self.assertEqual(self.s.resources.get(req['id'])['status'], 'RELEASING')

    def test_service_ready_starts_zero_card_client_and_finishes_owned_service(self):
        node = self.s.inventory.create(SYSTEM, {'name': 'test-node', 'host': '10.0.0.1', 'password': 'test-only', 'generation': 'A2', 'model': 'test'})
        self.s.telemetry.ingest(node['id'], Snapshot([DeviceSample(str(i), str(i), '0', str(i), 64 * 1024**3, 0, 0, 'OK', process_complete=True) for i in range(2)], BOOT, now()))
        remote = Remote()
        remote.hold_services = True
        self.s.workflows.transport = remote
        body = payload()
        body['jobs'][0].update(kind='service', ready=[{'type': 'python', 'path': 'scripts/check.py'}], ports=[18080])
        body['jobs'][1]['depends_on'] = [{'job_id': 'first', 'condition': 'ready'}]
        task = self.client.post('/api/workflows', json=body).json()
        space = self.client.get('/api/spaces').json()[0]
        req = self.s.resources.reserve(space['request_id'])
        self.s.resources.deliver(req['id'], req['version'])
        states = []
        for _ in range(18):
            self.s.workflows.tick_space(space['id'])
            detail = self.client.get('/api/workflows/' + task['id']).json()
            states.append([j['status'] for j in detail['jobs']])
            if detail['status'] in {'SUCCEEDED', 'FAILED'}:
                break
        self.assertIn(['READY', 'RUNNING'], states)
        self.assertEqual(detail['status'], 'SUCCEEDED', detail)

    def test_failed_main_still_runs_always_post_without_overwriting_failure(self):
        node = self.s.inventory.create(SYSTEM, {'name': 'test-node', 'host': '10.0.0.1', 'password': 'test-only', 'generation': 'A2', 'model': 'test'})
        self.s.telemetry.ingest(node['id'], Snapshot([DeviceSample(str(i), str(i), '0', str(i), 64 * 1024**3, 0, 0, 'OK', process_complete=True) for i in range(2)], BOOT, now()))
        remote = Remote()
        remote.fail_jobs = True
        self.s.workflows.transport = remote
        body = payload()
        body['jobs'][0].update(post_policy='always', post=[{'type': 'python', 'path': 'scripts/check.py'}])
        task = self.client.post('/api/workflows', json=body).json()
        space = self.client.get('/api/spaces').json()[0]
        req = self.s.resources.reserve(space['request_id'])
        self.s.resources.deliver(req['id'], req['version'])
        for _ in range(16):
            self.s.workflows.tick_space(space['id'])
            detail = self.client.get('/api/workflows/' + task['id']).json()
            if detail['status'] in {'SUCCEEDED', 'FAILED'}:
                break
        self.assertEqual(detail['status'], 'FAILED')
        self.assertTrue(any('# HIVE_PHASE post' in a['script'] and a.get('launched') for a in remote.attempts.values()))

    def test_worker_dispatches_cancelled_queued_workflow_without_node_side_effects(self):
        from hive.worker import Worker
        task = self.client.post('/api/workflows', json=payload()).json()
        self.client.post('/api/workflows/' + task['id'] + '/cancel')
        worker = Worker(self.s)
        try:
            worker.tick()
            for future in getattr(worker, 'workflow_futures', {}).values():
                future.result(timeout=10)
            self.assertEqual(self.client.get('/api/workflows/' + task['id']).json()['status'], 'CANCELLED')
        finally:
            for name in ('probe_pool','preflight_pool','control_pool','benchmark_pool','workflow_pool'):
                if hasattr(worker, name):
                    getattr(worker, name).shutdown(wait=True)

    def test_failed_bootstrap_cannot_adopt_same_named_container(self):
        node = self.s.inventory.create(SYSTEM, {'name': 'test-node', 'host': '10.0.0.1', 'password': 'test-only', 'generation': 'A2', 'model': 'test'})
        self.s.telemetry.ingest(node['id'], Snapshot([DeviceSample(str(i), str(i), '0', str(i), 64 * 1024**3, 0, 0, 'OK', process_complete=True) for i in range(2)], BOOT, now()))
        remote = Remote()
        remote.bootstrap_failure = True
        self.s.workflows.transport = remote
        task = self.client.post('/api/workflows', json=payload()).json()
        space = self.client.get('/api/spaces').json()[0]
        req = self.s.resources.reserve(space['request_id'])
        self.s.resources.deliver(req['id'], req['version'])
        self.s.workflows.tick_space(space['id'])
        self.s.workflows.tick_space(space['id'])
        self.assertFalse(remote.attempts, 'Unproven container must never receive attempts')
        self.assertEqual(self.s.resources.get(req['id'])['status'], 'ACTIVE')

    def test_invalid_environment_and_endpoint_parameters_reject_at_submission(self):
        cases = []
        body = payload()
        body['environments'][0]['install'] = [{'type': 'shell', 'path': 'scripts/job.sh', 'args': ['${task_id}']}]
        cases.append(body)
        body = payload()
        body['jobs'][0]['steps'][0]['args'] = ['${second.endpoint}']
        cases.append(body)
        for body in cases:
            response = self.client.post('/api/workflows', json=body)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get('/api/requests').json(), [])

    def test_direct_upload_launch_and_default_ten_minute_job(self):
        body = payload()
        body['jobs'][0]['steps'] = [{'launch': 'bash "${input}" --host "${node0.ip}" --port "$MY_PORT"',
                                    'files': [{'name': 'job.sh', 'content': FILES['scripts/job.sh']},
                                              {'name': 'test.yaml', 'content': 'message: override\n'}]}]
        response = self.client.post('/api/workflows', json=body)
        self.assertEqual(response.status_code, 201, response.text)
        task = response.json()
        self.assertEqual(task['jobs'][0]['spec']['timeout_seconds'], 600)
        self.assertEqual(task['spec']['files']['job.sh']['source_path'], 'scripts/job.sh')
        self.assertEqual(task['spec']['files']['test.yaml']['content'], 'message: override\n')
        node = self.s.inventory.create(SYSTEM, {'name': 'test-node', 'host': '10.0.0.1', 'password': 'test-only', 'generation': 'A2', 'model': 'test'})
        self.s.telemetry.ingest(node['id'], Snapshot([DeviceSample(str(i), str(i), '0', str(i), 64 * 1024**3, 0, 0, 'OK', process_complete=True) for i in range(2)], BOOT, now()))
        remote = Remote()
        self.s.workflows.transport = remote
        space = self.client.get('/api/spaces').json()[0]
        req = self.s.resources.reserve(space['request_id'])
        self.s.resources.deliver(req['id'], req['version'])
        for _ in range(20):
            self.s.workflows.tick_space(space['id'])
        detail = self.client.get('/api/workflows/' + task['id']).json()
        self.assertEqual(detail['status'], 'SUCCEEDED', detail)
        scripts = [a['script'] for a in remote.attempts.values() if '# HIVE_PHASE steps' in a['script']]
        self.assertTrue(any('job.sh' in script and '10.0.0.1' in script and '$MY_PORT' in script for script in scripts))

    def test_edited_execution_script_is_frozen_and_yaml_still_needs_command(self):
        body = payload()
        body['jobs'][0]['steps'] = [{'files': [{'name': 'test.yaml', 'content': 'message: config'}]}]
        self.assertEqual(self.client.post('/api/workflows', json=body).status_code, 422)
        self.assertEqual(self.client.get('/api/requests').json(), [])
        body['jobs'][0]['steps'] = [{'launch': 'bash job.sh', 'files': [{'name': 'job.sh', 'content': 'echo changed'}]}]
        response = self.client.post('/api/workflows', json=body)
        self.assertEqual(response.status_code, 201, response.text)
        frozen = response.json()['spec']['files']['job.sh']
        self.assertEqual(frozen['content'], 'echo changed')
        self.assertTrue(frozen['modified'])
        self.assertEqual(frozen['origin'], 'user_upload')
        self.assertEqual(frozen['base_sha256'], hashlib.sha256(FILES['scripts/job.sh'].encode()).hexdigest())

    def test_allocated_node_mapping_and_dynamic_ip_are_frozen_for_reuse(self):
        node = self.s.inventory.create(SYSTEM, {'name': 'test-node', 'host': '10.0.0.1', 'password': 'test-only', 'generation': 'A2', 'model': 'test'})
        entries = [{'kind': 'model', 'name': 'org/weights', 'target': '/mnt/models/weights'},
                   {'kind': 'dataset', 'name': 'org/data', 'target': '/mnt/datasets/data'},
                   {'kind': 'image', 'name': 'server+base', 'target': 'example/image:fixed'},
                   {'kind': 'package', 'name': 'torch', 'target': '/mnt/packages/torch.whl'}]
        self.s.node_mappings.replace(node['id'], SYSTEM, entries, 0)
        self.s.telemetry.ingest(node['id'], Snapshot([DeviceSample(str(i), str(i), '0', str(i), 64 * 1024**3, 0, 0, 'OK', process_complete=True) for i in range(2)], BOOT, now()))
        remote = Remote()
        self.s.workflows.transport = remote
        body = payload()
        body['environments'][0]['image'] = 'server+base'
        task = self.client.post('/api/workflows', json=body).json()
        space = self.client.get('/api/spaces').json()[0]
        req = self.s.resources.reserve(space['request_id'])
        self.s.resources.deliver(req['id'], req['version'])
        self.s.workflows.tick_space(space['id'])
        self.s.node_mappings.replace(node['id'], SYSTEM, [], 1)
        for _ in range(20):
            self.s.workflows.tick_space(space['id'])
        detail = self.client.get('/api/workflows/' + task['id']).json()
        self.assertEqual(detail['status'], 'SUCCEEDED', detail)
        actual = self.client.get('/api/spaces').json()[0]['environments'][1]
        self.assertEqual(actual['resource_mappings']['model']['org/weights'], '/mnt/models/weights')
        self.assertEqual(actual['mappings_version'], 1)
        scripts = [a['script'] for a in remote.attempts.values()]
        self.assertTrue(any('export HIVE_NODE0_IP=10.0.0.1' in script and 'hive_resource()' in script and '/mnt/datasets/data' in script for script in scripts))
        self.assertTrue(all('export HIVE_RESOURCE_MAP_JSON=' not in script for script in scripts))
        self.assertTrue(all('server+base' not in event[1] for event in remote.events if event[0] == 'image-inspect'))

    def test_upload_bootstrap_uses_frozen_command_and_invalid_inputs_do_not_allocate(self):
        for step in [{'launch': 'cat "${input}"'}, {'launch': '   '}, {'launch': 'bash job.sh', 'files': [{'name': '../job.sh', 'content': 'x'}]}]:
            body = payload()
            body['jobs'][0]['steps'] = [step]
            response = self.client.post('/api/workflows', json=body)
            self.assertEqual(response.status_code, 422, response.text)
        body = payload()
        body['jobs'][0]['artifacts'] = [{'environment': 'outside', 'label': 'Output', 'path': '/out.txt'}]
        self.assertEqual(self.client.post('/api/workflows', json=body).status_code, 422)
        self.assertEqual(self.client.get('/api/requests').json(), [])
        with patch.dict(FILES, {'other/job.sh': FILES['scripts/job.sh']}):
            body = payload()
            body['jobs'][0]['steps'] = [{'launch': 'bash job.sh', 'files': [{'name': 'job.sh', 'content': FILES['scripts/job.sh']}]}]
            self.assertEqual(self.client.post('/api/workflows', json=body).status_code, 422)
        body = payload()
        body['environments'][0]['bootstrap'] = {'launch': 'bash job.sh "${image}" "${container_name}"', 'files': [{'name': 'job.sh', 'content': FILES['scripts/job.sh']}]}
        response = self.client.post('/api/workflows', json=body)
        self.assertEqual(response.status_code, 201, response.text)
        node = self.s.inventory.create(SYSTEM, {'name': 'test-node', 'host': '10.0.0.1', 'password': 'test-only', 'generation': 'A2', 'model': 'test'})
        self.s.telemetry.ingest(node['id'], Snapshot([DeviceSample(str(i), str(i), '0', str(i), 64 * 1024**3, 0, 0, 'OK', process_complete=True) for i in range(2)], BOOT, now()))
        remote = Remote()
        self.s.workflows.transport = remote
        space = self.client.get('/api/spaces').json()[0]
        req = self.s.resources.reserve(space['request_id'])
        self.s.resources.deliver(req['id'], req['version'])
        for _ in range(20):
            self.s.workflows.tick_space(space['id'])
        self.assertEqual(self.client.get('/api/workflows/' + response.json()['id']).json()['status'], 'SUCCEEDED')
