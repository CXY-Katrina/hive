"""An executed merged-PR workflow becomes a reusable, privately derived preset."""
import copy
from dataclasses import replace
import io
import json
import os
import unittest
from urllib.parse import parse_qs, urlparse

from tests import test_workflow_multinode as multinode
from tests.test_workflows import github_http


@unittest.skipUnless(os.getenv('HIVE_TEST_MYSQL_PORT'), 'Needs isolated MySQL')
class PublishedWorkflowHTTP(unittest.TestCase):
    setUpClass = classmethod(multinode.MultiNodeHTTP.setUpClass.__func__)
    tearDownClass = classmethod(multinode.MultiNodeHTTP.tearDownClass.__func__)
    tearDown = multinode.MultiNodeHTTP.tearDown
    multi = multinode.MultiNodeHTTP.multi
    start = multinode.MultiNodeHTTP.start
    advance = multinode.MultiNodeHTTP.advance

    def setUp(self):
        self.settings = replace(self.settings, workflow_sample_preset_id='sample-case')
        multinode.MultiNodeHTTP.setUp(self)

    def login(self, username):
        response = self.client.post('/api/session', json={
            'username': username, 'password': 'test-admin' if username == 'admin' else ''})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_executed_merged_workflow_publication_and_private_variant_resubmission(self):
        merge_sha, calls = 'c' * 40, []

        def merged_github(request, timeout=20):
            url = request.full_url if hasattr(request, 'full_url') else request
            calls.append(url)
            response = github_http(request, timeout)
            if '/pulls/' in url:
                with response:
                    value = json.load(response)
                value.update(merged=True, merge_commit_sha=merge_sha)
                return io.BytesIO(json.dumps(value).encode())
            if '/contents/' in url:
                self.assertEqual(parse_qs(urlparse(url).query).get('ref'), [merge_sha])
            return response

        self.s.sources.opener = merged_github
        remote = multinode.MultiRemote()
        spec = self.multi()
        spec['source'].update(revision='merged', head_sha=merge_sha)
        spec['resource']['cards_per_node'] = 1
        template = '/tmp/hive-results/${task_id}/${job_id}/result.txt'
        spec['jobs'][0]['artifacts'] = [{'path': template}]
        original, space = self.start(spec, remote)
        publication = {'workflow_id': original['id'], 'item_id': 'sample-case',
                       'name': 'Executed merged sample', 'tags': {'cadence': 'nightly'}}
        self.login('admin')
        self.assertEqual(self.client.post('/api/presets/from-workflow', json=publication).status_code, 409)
        self.login('alice')
        completed = self.advance(original, space)
        self.assertEqual(completed['status'], 'SUCCEEDED')
        self.assertEqual(len([text for _, text in remote.prepared if '# HIVE_PHASE steps' in text]), 2)

        bob = self.login('bob')
        self.login('admin')
        granted = self.client.patch('/api/members/' + bob['id'], json={
            'can_request': True, 'can_view_credentials': False})
        self.assertEqual(granted.status_code, 200, granted.text)
        published = self.client.post('/api/presets/from-workflow', json=publication)
        self.assertEqual(published.status_code, 201, published.text)
        parent = published.json()
        self.assertEqual(parent['validation_status'], 'execution_passed')
        self.assertEqual(parent['source_workflow_id'], original['id'])
        self.assertEqual(parent['workflow']['jobs'][0]['artifacts'][0]['path'], template)
        self.assertFalse(parent['enabled'])
        enabled = self.client.post('/api/presets/' + parent['id'] + '/enable')
        self.assertEqual(enabled.status_code, 200, enabled.text)

        self.login('alice')
        adjusted = copy.deepcopy(parent['workflow'])
        adjusted['source'].pop('revision')  # Derivation must retain the parent's merged revision.
        adjusted['jobs'][0]['steps'][0]['args'] = ['personal-value']
        derived = self.client.post('/api/presets/' + parent['id'] + '/derive', json={
            'name': 'Personal adjusted sample', 'tags': {}, 'workflow': adjusted})
        self.assertEqual(derived.status_code, 201, derived.text)
        variant = derived.json()
        self.assertEqual(variant['workflow']['source']['revision'], 'merged')
        self.assertEqual(variant['source']['head_sha'], merge_sha)
        self.assertEqual(variant['validation_status'], 'unverified')
        self.assertEqual(parent['workflow']['jobs'][0]['steps'][0]['args'], [])

        submission = dict(copy.deepcopy(variant['workflow']), preset_id=variant['id'],
                          idempotency_key='run-personal-variant')
        accepted = self.client.post('/api/workflows', json=submission)
        self.assertEqual(accepted.status_code, 201, accepted.text)
        second = accepted.json()
        second_space = next(row for row in self.client.get('/api/spaces').json() if row['id'] == second['space_id'])
        allocation = self.s.resources.reserve(second_space['request_id'])
        self.assertIsNotNone(allocation)
        self.s.resources.deliver(allocation['id'], allocation['version'])
        second_result = self.advance(second, second_space)
        self.assertEqual(second_result['status'], 'SUCCEEDED')
        archives = [artifact for task in (completed, second_result) for job in task['jobs'] for artifact in job['artifacts']]
        self.assertEqual(len({artifact['path'] for artifact in archives}), 4)
        self.assertTrue(all('${' not in artifact['path'] for artifact in archives))
        self.assertTrue(all(self.client.get(artifact['download_url']).status_code == 200 for artifact in archives))
        personal_steps = [(host, text) for host, text in remote.prepared
                          if '# HIVE_PHASE steps' in text and 'personal-value' in text]
        self.assertEqual({host for host, _ in personal_steps}, {'10.0.0.1', '10.0.0.2'})
        self.assertEqual(len(personal_steps), 2)
        self.assertEqual(len(remote.containers), 4)

        self.login('bob')
        self.assertNotIn(variant['id'], [row['id'] for row in self.client.get('/api/presets').json()])
        before = len(calls)
        denied = self.client.post('/api/workflows', json=dict(submission, idempotency_key='stolen-variant'))
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertIn('参数变体', denied.json()['detail'])
        self.assertEqual(len(calls), before)
        self.assertEqual(len(self.client.get('/api/requests').json()), 2)
