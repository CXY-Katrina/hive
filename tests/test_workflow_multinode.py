"""Multi-node execution observed through HTTP, MySQL and the SSH boundary."""
import os
import base64
import hashlib
import copy
import json
import shlex
from dataclasses import replace
from pathlib import Path
import re
import tempfile
import unittest
from hive.domain import SYSTEM, CommandResult, DeviceSample, Snapshot, now
from tests import test_workflows as workflow_fixtures
from tests.test_workflows import payload
from tests.workflow_remote import Remote, BOOT


class MultiRemote(Remote):
    def __init__(self):
        super().__init__()
        self.prepared = []
        self.hold_host = None
        self.unknown_host = None
        self.closed_hosts = []
        self.read_hosts = []
        self.bootstrap_scripts = []

    def run(self, node, script, timeout=20):
        if '# HIVE_BOOTSTRAP_RUN' in script:
            self.bootstrap_scripts.append(script)
        if '>job.sh.upload' in script:
            encoded = re.search(r'printf %s (\S+) \| base64 --decode >job.sh.upload', script)[1]
            self.prepared.append((node['host'], base64.b64decode(encoded.strip("'")).decode()))
        action = re.search(r'bash -s -- (/var/tmp/hive/container-attempts/\S+) (launch|status|close) ', script)
        if action:
            directory, operation = action.groups()
            if operation == 'status' and node['host'] == self.hold_host and '# BLOCK_UPSTREAM' in self.attempts[directory]['script']:
                return CommandResult('RUNNING\n', '', 0)
            if operation == 'close':
                if node['host'] == self.unknown_host:
                    return CommandResult('UNKNOWN\n', '', 0)
                self.closed_hosts.append(node['host'])
        if 'HIVE_FILE ' in script:
            self.read_hosts.append(node['host'])
            data = ('result from ' + node['host']).encode()
            return CommandResult('HIVE_FILE ' + str(len(data)) + ' ' + hashlib.sha256(data).hexdigest()
                                 + '\n' + base64.b64encode(data).decode(), '', 0)
        return super().run(node, script, timeout)


@unittest.skipUnless(os.getenv('HIVE_TEST_MYSQL_PORT'), 'Needs isolated MySQL')
class MultiNodeHTTP(unittest.TestCase):
    def test_shell_and_python_jobs_receive_one_unified_environment_on_every_node(self):
        spec = self.multi()
        spec['runtime_variables'] = 'minimal'
        spec['environments'].append({**copy.deepcopy(spec['environments'][0]),'alias':'aaa_client'})
        for env in spec['environments']:
            env['install'] = [{'launch':'printf "%s\\n" "$HIVE_CONTAINER0_NAME"'}]
        spec['jobs'][0]['steps'] = [{'launch':'printf "%s\\n" "$HIVE_NODE0_IP $HIVE_CONTAINER0_NAME"'}]
        spec['jobs'].append({'id':'client','environment':'aaa_client','npu_count':0,
                            'depends_on':[{'job_id':'first','condition':'succeeded'}],
                            'steps':[{'type':'python','path':'scripts/check.py'}]})
        remote = MultiRemote()
        task, space = self.start(spec, remote)
        self.advance(task, space)
        live = self.client.get('/api/spaces').json()[0]
        lookup = {(env['logical_alias'],env['node_alias']):env for env in live['environments']}
        names = [lookup[alias,node]['container_name'] for alias in ('server_env','aaa_client')
                 for node in ('node0','node1')]
        expected = {'HIVE_CONTAINER'+str(i)+'_NAME':name for i,name in enumerate(names)}
        expected.update({'HIVE_NODE'+str(i)+'_IP':lookup['server_env','node'+str(i)]['host'] for i in range(2)})
        scripts = [script for _,script in remote.prepared]+remote.bootstrap_scripts
        for script in scripts:
            if '# HIVE_BOOTSTRAP_RUN' in script:
                script = shlex.split(script.split('timeout --signal=TERM --kill-after=5 45 bash -c ',1)[1])[0]
            exports = dict(shlex.split(line)[1].split('=',1) for line in script.splitlines()
                           if line.startswith('export HIVE_'))
            for key,value in expected.items():
                self.assertEqual(exports.get(key),value,(key,script[:200]))
            allowed = set(expected)|{'HIVE_SOURCE_DIR'}
            if re.search(r'# HIVE_PHASE (?:input|pre|steps|post|ready)\b',script):
                allowed |= {'HIVE_TASK_ID','HIVE_JOB_ID'}
                self.assertEqual(exports['HIVE_TASK_ID'],task['id'])
            self.assertEqual(set(exports),allowed)
            self.assertIn('export ASCEND_RT_VISIBLE_DEVICES=',script)

    setUpClass = classmethod(workflow_fixtures.WorkflowHTTP.setUpClass.__func__)
    tearDownClass = classmethod(workflow_fixtures.WorkflowHTTP.tearDownClass.__func__)
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = replace(self.settings, data_dir=Path(directory.name))
        workflow_fixtures.WorkflowHTTP.setUp(self)
    tearDown = workflow_fixtures.WorkflowHTTP.tearDown

    def multi(self):
        spec = payload()
        spec['resource']['machine_count'] = 2
        spec['environments'] = [spec['environments'][0]]
        env = spec['environments'][0]
        env.pop('role')
        env.pop('node_alias')
        env['node_aliases'] = ['node0', 'node1']
        spec['jobs'] = [spec['jobs'][0]]
        return spec

    def start(self, spec, remote):
        for host in ('10.0.0.1', '10.0.0.2'):
            node = self.s.inventory.create(SYSTEM, {'name': host, 'host': host, 'password': 'fixture', 'generation': 'A2', 'model': 'test'})
            self.s.telemetry.ingest(node['id'], Snapshot([DeviceSample(str(i), str(i), '0', str(i), 64 * 1024**3, 0, 0, 'OK', process_complete=True) for i in range(2)], BOOT, now()))
        self.s.workflows.transport = remote
        response = self.client.post('/api/workflows', json=spec)
        self.assertEqual(response.status_code, 201, response.text)
        task = response.json()
        space = self.client.get('/api/spaces').json()[0]
        request = self.s.resources.reserve(space['request_id'])
        self.s.resources.deliver(request['id'], request['version'])
        return task, space

    def advance(self, task, space, status='SUCCEEDED', limit=30):
        for _ in range(limit):
            self.s.workflows.tick_space(space['id'])
            detail = self.client.get('/api/workflows/' + task['id']).json()
            if detail['status'] == status:
                return detail
        self.fail('Did not reach ' + status + ': ' + str(detail))

    def test_logical_environment_creates_distinct_node_instances_without_losing_editor_spec(self):
        response = self.client.post('/api/workflows', json=self.multi())
        self.assertEqual(response.status_code, 201, response.text)
        task = response.json()
        self.assertEqual(len(task['spec']['environments']), 1)
        self.assertEqual(task['spec']['environments'][0]['node_aliases'], ['node0', 'node1'])
        self.assertEqual(len(task['jobs']), 2)
        self.assertEqual({j['node_alias'] for j in task['jobs']}, {'node0', 'node1'})
        self.assertEqual(len({j['id'] for j in task['jobs']}), 2)
        self.assertEqual(task['logical_jobs'][0]['id'], 'first')
        self.assertEqual(len(task['logical_jobs'][0]['instances']), 2)
        space = self.client.get('/api/spaces').json()[0]
        self.assertEqual(len(space['environments']), 2)
        self.assertEqual(len(space['logical_environments']), 1)
        self.assertEqual(self.client.get('/api/requests').json()[0]['spec']['machine_count'], 2)

    def test_two_node_jobs_reach_both_hosts_with_independent_identity_and_frozen_node_context(self):
        nodes = []
        for host in ('10.0.0.1', '10.0.0.2'):
            node = self.s.inventory.create(SYSTEM, {'name': host, 'host': host, 'password': 'fixture', 'generation': 'A2', 'model': 'test'})
            self.s.telemetry.ingest(node['id'], Snapshot([DeviceSample(str(i), str(i), '0', str(i), 64 * 1024**3, 0, 0, 'OK', process_complete=True) for i in range(2)], BOOT, now()))
            nodes.append(node)
        class TracedRemote(Remote):
            def __init__(self):
                super().__init__()
                self.prepared = []
            def run(self, node, script, timeout=20):
                if '>job.sh.upload' in script:
                    encoded = re.search(r'printf %s (\S+) \| base64 --decode >job.sh.upload', script)[1]
                    self.prepared.append((node['host'], base64.b64decode(encoded.strip("'")).decode()))
                return super().run(node, script, timeout)
        remote = TracedRemote()
        self.s.workflows.transport = remote
        task = self.client.post('/api/workflows', json=self.multi()).json()
        space = self.client.get('/api/spaces').json()[0]
        request = self.s.resources.reserve(space['request_id'])
        self.s.resources.deliver(request['id'], request['version'])
        for _ in range(25):
            self.s.workflows.tick_space(space['id'])
            detail = self.client.get('/api/workflows/' + task['id']).json()
            if detail['status'] == 'SUCCEEDED':
                break
        self.assertEqual(detail['status'], 'SUCCEEDED', detail.get('reason'))
        self.assertEqual(detail['logical_jobs'][0]['status'], 'SUCCEEDED')
        steps = [(host, script) for host, script in remote.prepared if '# HIVE_PHASE steps' in script]
        self.assertEqual({host for host, _ in steps}, {'10.0.0.1', '10.0.0.2'})
        self.assertEqual(len(steps), 2)
        environments = self.client.get('/api/spaces').json()[0]['environments']
        by_node = {env['node_alias']:env for env in environments}
        for host, script in steps:
            self.assertNotIn('export HIVE_HOST_IP=', script)
            self.assertNotIn('export HIVE_NODES_JSON=', script)
            self.assertIn('export HIVE_NODE0_IP=', script)
            self.assertIn('export HIVE_NODE1_IP=', script)
            for index in range(2):
                self.assertIn('export HIVE_NODE'+str(index)+'_IP='+by_node['node'+str(index)]['host'],script)
                self.assertIn('export HIVE_CONTAINER'+str(index)+'_NAME='+by_node['node'+str(index)]['container_name'],script)
        self.assertEqual(len({env['container_name'] for env in environments}), 2)
        before = len(remote.containers)
        self.s.workflows.tick_space(space['id'])
        self.assertEqual(len(remote.containers), before)

    def test_preexisting_unversioned_space_preserves_legacy_runtime_variables(self):
        remote = MultiRemote()
        task,space = self.start(self.multi(),remote)
        # Emulate a persisted pre-upgrade row in the disposable test database.
        prior = json.loads(self.db.one('SELECT spec FROM workflow_spaces WHERE id=%s',(space['id'],))['spec'])
        prior.pop('runtime_variables',None)
        with self.db.transaction() as cursor:
            cursor.execute('UPDATE workflow_spaces SET spec=%s WHERE id=%s',(json.dumps(prior),space['id']))
        self.advance(task,space)
        scripts = [script for _,script in remote.prepared if '# HIVE_PHASE steps' in script]
        self.assertEqual(len(scripts),2)
        for script in scripts:
            self.assertIn('export HIVE_CONTEXT_JSON=',script)
            self.assertIn('export HIVE_RESOURCE_MAP_JSON=',script)
            self.assertIn('export HIVE_HOST_IP=',script)
            self.assertIn('export HIVE_CONTAINER_NAME=',script)

    def test_artifact_targets_select_exact_instances_once_and_job_node_subset_is_enforced(self):
        spec = self.multi()
        spec['jobs'][0]['node_aliases'] = ['node1']
        spec['jobs'][0]['artifacts'] = [{'path': '/tmp/result.json', 'targets': [
            {'environment': 'server_env', 'node_alias': 'node0'},
            {'environment': 'server_env', 'node_alias': 'node1'}]}]
        response = self.client.post('/api/workflows', json=spec)
        self.assertEqual(response.status_code, 201, response.text)
        task = response.json()
        self.assertEqual([job['node_alias'] for job in task['jobs']], ['node1'])
        space = self.client.get('/api/spaces').json()[0]
        expected = {env['alias'] for env in space['environments']}
        artifacts = task['jobs'][0]['spec']['artifacts']
        self.assertEqual({item['environment'] for item in artifacts}, expected)
        self.assertTrue(all('targets' not in item for item in artifacts))
        self.assertEqual(len(task['spec']['jobs'][0]['artifacts']), 1)
        invalid = self.multi()
        invalid['idempotency_key'] = 'invalid-target'
        invalid['jobs'][0]['artifacts'] = [{'path': '/tmp/result.json', 'targets': [
            {'environment': 'server_env', 'node_alias': 'node2'}]}]
        self.assertEqual(self.client.post('/api/workflows', json=invalid).status_code, 422)
        self.assertEqual(len(self.client.get('/api/requests').json()), 1)

    def test_omitted_artifact_targets_cover_environment_nodes_outside_job_subset(self):
        spec = self.multi()
        spec['jobs'][0]['node_aliases'] = ['node0']
        spec['jobs'][0]['artifacts'] = [{'path': '/outputs', 'label': 'All outputs'}]
        response = self.client.post('/api/workflows', json=spec)
        self.assertEqual(response.status_code, 201, response.text)
        task = response.json()
        self.assertEqual(len(task['jobs']), 1)
        artifacts = task['jobs'][0]['spec']['artifacts']
        space = self.client.get('/api/spaces').json()[0]
        self.assertEqual({item['environment'] for item in artifacts},
                         {item['alias'] for item in space['environments']})

    def test_one_directory_on_many_nodes_does_not_hit_logical_path_limit(self):
        spec = self.multi()
        spec['resource']['machine_count'] = 32
        spec['environments'][0]['node_aliases'] = ['node'+str(i) for i in range(32)]
        spec['jobs'][0]['node_aliases'] = ['node0']
        spec['jobs'][0]['artifacts'] = [{'path':'/outputs'}]
        response = self.client.post('/api/workflows',json=spec)
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(len(response.json()['jobs'][0]['spec']['artifacts']),32)

    def test_cross_node_dependencies_expand_barrier_and_endpoint_sources(self):
        spec = self.multi()
        spec['jobs'][0]['ports'] = [8123]
        downstream = payload()['jobs'][1]
        downstream.update(environment='server_env', node_aliases=['node1'], steps=[{
            'launch': 'printf "%s %s" "${first.node0.endpoint}" "${first.endpoint}"'}])
        spec['jobs'].append(downstream)
        response = self.client.post('/api/workflows', json=spec)
        self.assertEqual(response.status_code, 201, response.text)
        jobs = response.json()['jobs']
        upstream = {j['node_alias']: j['id'] for j in jobs if j['logical_job_id'] == 'first'}
        downstream = next(j for j in jobs if j['logical_job_id'] == 'second')['spec']
        self.assertEqual({d['job_id'] for d in downstream['depends_on']}, set(upstream.values()))
        self.assertEqual(downstream['endpoint_sources']['first.node0'], upstream['node0'])
        self.assertEqual(downstream['endpoint_sources']['first'], upstream['node1'])

    def test_remote_barrier_waits_for_both_nodes_then_renders_endpoints_and_archives_each_target(self):
        spec = self.multi()
        spec['jobs'][0].update(ports=[8123], steps=[{'launch': '# BLOCK_UPSTREAM\nprintf done'}])
        second = payload()['jobs'][1]
        second.update(environment='server_env', node_aliases=['node1'], steps=[{
            'launch': '# DOWNSTREAM\nprintf "%s %s" "${first.node0.endpoint}" "${first.endpoint}"'}],
            artifacts=[{'path': '/tmp/result.txt', 'targets': [
                {'environment': 'server_env', 'node_alias': 'node0'},
                {'environment': 'server_env', 'node_alias': 'node1'}]}])
        spec['jobs'].append(second)
        remote = MultiRemote()
        remote.hold_host = '10.0.0.2'
        task, space = self.start(spec, remote)
        for _ in range(18):
            self.s.workflows.tick_space(space['id'])
        detail = self.client.get('/api/workflows/' + task['id']).json()
        self.assertEqual(next(j for j in detail['jobs'] if j['logical_job_id'] == 'second')['status'], 'PENDING')
        self.assertFalse(any('# DOWNSTREAM' in script for _, script in remote.prepared))
        remote.hold_host = None
        detail = self.advance(task, space)
        scripts = [(host, script) for host, script in remote.prepared if '# DOWNSTREAM' in script]
        self.assertEqual(len(scripts), 1)
        self.assertIn('http://10.0.0.1:8123', scripts[0][1])
        self.assertIn('http://10.0.0.2:8123', scripts[0][1])
        job = next(j for j in detail['jobs'] if j['logical_job_id'] == 'second')
        self.assertEqual(len(job['artifacts']), 2)
        self.assertEqual({a['node_alias'] for a in job['artifacts']}, {'node0', 'node1'})
        self.assertEqual({a['logical_environment'] for a in job['artifacts']}, {'server_env'})
        self.assertEqual(set(remote.read_hosts), {'10.0.0.1', '10.0.0.2'})
        self.assertEqual({self.client.get(a['download_url']).content for a in job['artifacts']},
                         {b'result from 10.0.0.1', b'result from 10.0.0.2'})

    def test_logical_space_reuse_keeps_containers_and_cancellation_on_one_unknown_host_keeps_lease(self):
        remote = MultiRemote()
        task, space = self.start(self.multi(), remote)
        self.advance(task, space)
        names = set(remote.containers)
        reuse = self.multi()
        reuse.pop('resource')
        reuse.update(space_id=space['id'], environments=[], idempotency_key='multinode-reuse')
        response = self.client.post('/api/workflows', json=reuse)
        self.assertEqual(response.status_code, 201, response.text)
        second = response.json()
        self.assertEqual(len(second['spec']['environments']), 1)
        self.assertEqual(len(second['jobs']), 2)
        remote.hold_jobs = True
        for _ in range(6):
            self.s.workflows.tick_space(space['id'])
        detail = self.client.get('/api/workflows/' + second['id']).json()
        self.assertEqual({j['status'] for j in detail['jobs']}, {'RUNNING'})
        self.assertEqual(set(remote.containers), names)
        remote.unknown_host = '10.0.0.2'
        self.client.post('/api/workflows/' + second['id'] + '/cancel')
        self.s.workflows.tick_space(space['id'])
        self.assertNotIn(self.client.get('/api/workflows/' + second['id']).json()['status'], {'SUCCEEDED', 'CANCELLED'})
        request = self.client.get('/api/requests').json()[0]
        self.assertEqual(request['status'], 'ACTIVE')
        remote.unknown_host = None
        self.advance(second, space, 'CANCELLED')
        self.assertEqual(set(remote.closed_hosts), {'10.0.0.1', '10.0.0.2'})
        self.assertEqual(len(self.client.get('/api/requests').json()), 1)

    def test_invalid_node_selections_and_expansion_limits_reject_before_request_creation(self):
        cases = []
        duplicate = self.multi()
        duplicate['environments'][0]['node_aliases'] = ['node0', 'node0']
        cases.append(duplicate)
        outside = self.multi()
        outside['jobs'][0]['node_aliases'] = ['node2']
        cases.append(outside)
        overflow = self.multi()
        overflow['resource']['machine_count'] = 64
        overflow['environments'][0]['node_aliases'] = ['node' + str(i) for i in range(64)]
        overflow['jobs'] = [dict(copy.deepcopy(overflow['jobs'][0]), id='job' + str(i)) for i in range(5)]
        cases.append(overflow)
        ambiguous = self.multi()
        ambiguous['resource']['machine_count'] = 3
        extra = copy.deepcopy(ambiguous['environments'][0])
        extra.update(alias='third_env', node_aliases=['node2'])
        ambiguous['environments'].append(extra)
        ambiguous['jobs'][0]['ports'] = [8123]
        second = payload()['jobs'][1]
        second.update(environment='third_env', steps=[{'launch': 'curl ${first.endpoint}'}])
        ambiguous['jobs'].append(second)
        cases.append(ambiguous)
        for index, spec in enumerate(cases):
            with self.subTest(index=index):
                spec['idempotency_key'] = 'invalid-' + str(index)
                self.assertEqual(self.client.post('/api/workflows', json=spec).status_code, 422)
        self.assertEqual(self.client.get('/api/requests').json(), [])

    def test_multi_node_services_remain_ready_until_all_consumers_finish(self):
        remote = MultiRemote()
        remote.hold_services = True
        remote.hold_host = '10.0.0.2'
        spec = self.multi()
        spec['jobs'][0].update(kind='service', ports=[8123], ready=[{'launch': 'true'}])
        second = payload()['jobs'][1]
        second.update(environment='server_env', depends_on=[{'job_id': 'first', 'condition': 'ready'}],
                      steps=[{'launch': '# BLOCK_UPSTREAM\nprintf done'}])
        spec['jobs'].append(second)
        task, space = self.start(spec, remote)
        for _ in range(20):
            self.s.workflows.tick_space(space['id'])
        detail = self.client.get('/api/workflows/' + task['id']).json()
        self.assertEqual({j['status'] for j in detail['jobs'] if j['logical_job_id'] == 'first'}, {'READY'})
        self.assertEqual({j['status'] for j in detail['jobs'] if j['logical_job_id'] == 'second'}, {'SUCCEEDED', 'RUNNING'})
        remote.hold_host = None
        detail = self.advance(task, space)
        self.assertEqual({j['status'] for j in detail['jobs']}, {'SUCCEEDED'})

    def test_artifact_paths_accept_documented_hive_variables_and_preserve_template(self):
        for index,template in enumerate(('/tmp/${HIVE_TASK_ID}/${HIVE_JOB_ID}/outputs',
                                         '/tmp/$HIVE_TASK_ID/$HIVE_JOB_ID/outputs')):
            spec = self.multi()
            spec['idempotency_key'] = 'hive-artifact-variable-'+str(index)
            spec['jobs'][0]['artifacts'] = [{'path':template}]
            response = self.client.post('/api/workflows',json=spec)
            self.assertEqual(response.status_code,201,response.text)
            task = response.json()
            self.assertEqual(task['spec']['jobs'][0]['artifacts'][0]['path'],template)
            for job in task['jobs']:
                self.assertEqual(job['spec']['artifacts'][0]['path'],
                                 '/tmp/'+task['id']+'/'+job['id']+'/outputs')

    def test_artifact_variable_expansion_does_not_replace_longer_variable_names(self):
        template = '/tmp/$HIVE_TASK_ID_suffix/$HIVE_JOB_ID2/outputs'
        spec = self.multi()
        spec['jobs'][0]['artifacts'] = [{'path':template}]
        response = self.client.post('/api/workflows',json=spec)
        self.assertEqual(response.status_code,201,response.text)
        for job in response.json()['jobs']:
            self.assertEqual(job['spec']['artifacts'][0]['path'],template)

    def test_artifact_paths_bind_task_and_instance_ids_while_reuse_retains_templates(self):
        template = '/tmp/hive-results/${task_id}/${job_id}/result.txt'
        spec = self.multi()
        spec['jobs'][0]['artifacts'] = [{'path': template}]
        first = self.client.post('/api/workflows', json=spec)
        self.assertEqual(first.status_code, 201, first.text)
        first = first.json()
        reuse = copy.deepcopy(spec)
        reuse.pop('resource')
        reuse.update(space_id=first['space_id'], environments=[], idempotency_key='template-reuse')
        second = self.client.post('/api/workflows', json=reuse)
        self.assertEqual(second.status_code, 201, second.text)
        second = second.json()
        paths = []
        for task in (first, second):
            self.assertEqual(task['spec']['jobs'][0]['artifacts'][0]['path'], template)
            for job in task['jobs']:
                path = job['spec']['artifacts'][0]['path']
                self.assertEqual(path, '/tmp/hive-results/' + task['id'] + '/' + job['id'] + '/result.txt')
                paths.append(path)
        self.assertEqual(len(set(paths)), 4)
        self.assertEqual(first['space_id'], second['space_id'])
        self.assertEqual(self.client.post('/api/workflows', json=spec).json()['id'], first['id'])
        for index, path in enumerate(('/tmp/${host}/result', '/tmp/${task_id', '/tmp/${task_id:-fallback}/result')):
            invalid = copy.deepcopy(spec)
            invalid['idempotency_key'] = 'bad-template-' + str(index)
            invalid['jobs'][0]['artifacts'][0]['path'] = path
            self.assertEqual(self.client.post('/api/workflows', json=invalid).status_code, 422)
        collision = copy.deepcopy(spec)
        collision['idempotency_key'] = 'resolved-template-collision'
        collision['jobs'][0]['artifacts'] = [{'path': '/tmp/${job_id}'}, {'path': '/tmp/' + first['jobs'][0]['id']}]
        self.assertEqual(self.client.post('/api/workflows', json=collision).status_code, 422)
        self.assertEqual(len(self.client.get('/api/requests').json()), 1)


if __name__ == '__main__':
    unittest.main()
