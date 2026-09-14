"""Public preset loading and submission; isolated MySQL and external HTTP only."""
import json
import tempfile
import os
import unittest
import io
import copy
from pathlib import Path

from tests import test_presets as preset_tests
from hive.presets import Presets
from tests import test_workflows as workflow_tests
from tests.test_sources import github_file


@unittest.skipUnless(os.getenv('HIVE_TEST_MYSQL_PORT'), 'Needs isolated MySQL')
class ArchiveHTTPTests(unittest.TestCase):
    setUpClass = classmethod(preset_tests.PresetHTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(preset_tests.PresetHTTPTests.tearDownClass.__func__)
    setUp = preset_tests.PresetHTTPTests.setUp

    def test_reloading_personal_variant_preserves_edited_scripts_and_main_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            case = root / 'sample'
            case.mkdir()
            (case / 'source.json').write_text(json.dumps({
                'upstream_commit': '1' * 40, 'vllm_commit': '2' * 40,
                'nightly_yaml': 'tests/e2e/nightly/sample.yaml', 'created_by': 'admin',
                'helper_files': ['run.py']}))
            (case / 'run.py').write_bytes(b'print("v1")\n')
            workflow = workflow_tests.payload()
            workflow['environments'][0]['install'] = [{'launch': 'python3 hive_presets/sample/run.py'}]
            (case / 'workflow.json').write_text(json.dumps(workflow))
            self.services.presets = Presets(self.db, self.sources, archive_root=root)
            self.client.cookies.set('hive_session', self.tokens['alice'])
            parent = self.client.get('/api/presets').json()[0]
            parent['workflow']['jobs'][0]['timeout_seconds'] = 120
            parent['workflow']['environments'][0]['install'][0]['files'][0]['content'] = 'print("personal")\n'
            parent['workflow']['source'] = {'revision': 'branch', 'branch': 'main', 'head_sha': '3' * 40, 'vllm_sha': '4' * 40}
            saved = self.client.post('/api/presets/' + parent['id'] + '/derive', json={
                'name': 'My case', 'tags': {}, 'workflow': parent['workflow']})
            self.assertEqual(saved.status_code, 201, saved.text)
            (case / 'run.py').write_bytes(b'print("v2")\n')
            variant = next(row for row in self.client.get('/api/presets').json() if row['id'] == saved.json()['id'])
            self.assertEqual(variant['workflow']['environments'][0]['install'][0]['files'][0]['content'], 'print("personal")\n')
            self.assertEqual(variant['source']['branch'], 'main')
            self.assertEqual(variant['workflow']['source']['head_sha'], '3' * 40)
            self.assertEqual(variant['workflow']['jobs'][0]['timeout_seconds'], 120)
            self.assertNotIn('archive_refreshed', variant)
            self.assertEqual(variant['yaml_path'], 'tests/e2e/nightly/sample.yaml')

    def test_one_nightly_archive_is_loadable_by_unprivileged_user_without_github(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            case = root / 'sample'
            case.mkdir()
            (case / 'source.json').write_text(json.dumps({
                'upstream_commit': '1' * 40, 'vllm_commit': '2' * 40,
                'nightly_yaml': 'tests/e2e/nightly/models/configs/sample.yaml',
                'created_by': 'admin', 'name': 'Sample nightly', 'id': 'sample-case',
                'helper_files': ['run.py'], 'tags': {'schedule': 'nightly'}}))
            (case / 'run.py').write_bytes(b'print("sample")\n')
            (case / 'workflow.json').write_text(json.dumps({
                'name': 'Sample nightly', 'environments': [{'install': [{'launch': 'python3 hive_presets/sample/run.py'}]}],
                'jobs': [{'id': 'serve'}, {'id': 'bench'}, {'id': 'verify'}]}))
            self.services.presets = Presets(self.db, self.sources, archive_root=root)
            self.identity.permissions(self.admin, self.alice.id, {'can_request': False, 'can_view_credentials': False})
            self.client.cookies.set('hive_session', self.tokens['alice'])
            response = self.client.get('/api/presets')
            self.assertEqual(response.status_code, 200, response.text)
            rows = response.json()
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]['loadable'])
            self.assertEqual(rows[0]['created_by'], 'admin')
            self.assertEqual(rows[0]['source']['commit'], '1' * 40)
            self.assertNotIn('pr', rows[0]['source'])
            self.assertEqual(rows[0]['yaml_path'], 'tests/e2e/nightly/models/configs/sample.yaml')
            self.assertEqual(len(rows[0]['workflow']['jobs']), 3)
            self.assertEqual(rows[0]['workflow']['environments'][0]['install'][0]['files'][0]['content'], 'print("sample")\n')
            self.assertEqual(self.github.calls, [])


@unittest.skipUnless(os.getenv('HIVE_TEST_MYSQL_PORT'), 'Needs isolated MySQL')
class ArchiveWorkflowHTTP(unittest.TestCase):
    setUpClass = classmethod(workflow_tests.WorkflowHTTP.setUpClass.__func__)
    tearDownClass = classmethod(workflow_tests.WorkflowHTTP.tearDownClass.__func__)
    setUp = workflow_tests.WorkflowHTTP.setUp
    tearDown = workflow_tests.WorkflowHTTP.tearDown

    def test_bundled_nightly_submits_frozen_local_scripts_without_helper_pr(self):
        rows = self.client.get('/api/presets').json()
        row = next(r for r in rows if r.get('origin') == 'archive')
        self.assertEqual(len(rows), 1)
        self.assertEqual(row['created_by'], 'admin')
        head, vllm = row['source']['head_sha'], row['source']['vllm_sha']
        calls = []
        def upstream(request, timeout=15):
            calls.append(request.full_url)
            if request.full_url.endswith('/commits/' + head):
                value = {'sha': head}
            elif '/contents/.github/vllm-main-verified.commit?ref=' + head in request.full_url:
                value = github_file('.github/vllm-main-verified.commit', vllm + '\n')
            elif '/contents/' + row['yaml_path'] + '?ref=' + head in request.full_url:
                value = github_file(row['yaml_path'], (Path('preset_tasks/qwen3-30b-a3b-w8a8/case.yaml')).read_text(encoding='utf-8'))
            elif '/git/trees/' + head in request.full_url:
                value = {'tree': [{'path': row['yaml_path'], 'type': 'blob', 'mode': '100644'}], 'truncated': False}
            else:
                raise AssertionError('Unexpected upstream request: ' + request.full_url)
            return io.BytesIO(json.dumps(value).encode())
        self.s.sources.opener = upstream
        body = {**row['workflow'], 'idempotency_key': 'archived-task', 'preset_id': row['id']}
        result = self.client.post('/api/workflows', json=body)
        self.assertEqual(result.status_code, 201, result.text)
        task = result.json()
        self.assertEqual(task['status'], 'QUEUED')
        self.assertEqual(len(task['jobs']), 3)
        self.assertNotIn('pr', task['spec']['source'])
        files = task['spec']['files']
        self.assertEqual(len(files), 5)
        helpers = {name: file for name, file in files.items() if name.startswith('hive_presets/qwen3-30b-a3b-w8a8/')}
        self.assertTrue(all(f['origin'] == 'hive_archive' and f['uploaded'] for f in helpers.values()))
        self.assertEqual(files[row['yaml_path']]['origin'], 'upstream')
        self.assertFalse(any('/pulls/' in url or '/tools/' in url for url in calls))
        changed = copy.deepcopy(body)
        changed['idempotency_key'] = 'changed-archive'
        edited_name = changed['environments'][0]['install'][0]['files'][0]['name']
        def edit_shared(value):
            if isinstance(value, list):
                for child in value:
                    edit_shared(child)
            elif isinstance(value, dict):
                for attachment in value.get('files', []):
                    if attachment['name'] == edited_name:
                        attachment['content'] += '\n# user change\n'
                for key, child in value.items():
                    if key != 'files':
                        edit_shared(child)
        edit_shared(changed)
        stale = self.client.post('/api/workflows', json=changed)
        self.assertEqual(stale.status_code, 201, stale.text)
        edited_name = changed['environments'][0]['install'][0]['files'][0]['name']
        self.assertTrue(stale.json()['spec']['files'][edited_name]['modified'])
        self.assertEqual(stale.json()['spec']['files'][edited_name]['base_sha256'], files[edited_name]['sha256'])
        self.assertEqual(self.client.get('/api/workflows/' + task['id']).json()['spec']['files'][edited_name], files[edited_name])

    def test_ordinary_user_can_derive_archive_without_resource_permission(self):
        row = next(r for r in self.client.get('/api/presets').json() if r.get('origin') == 'archive')
        actor = self.client.get('/api/session').json()
        from hive.domain import SYSTEM
        self.s.identity.permissions(SYSTEM, actor['id'], {'can_request': False, 'can_view_credentials': False})
        body = {'name': 'My Qwen3 variant', 'tags': row['tags'], 'workflow': row['workflow']}
        result = self.client.post('/api/presets/' + row['id'] + '/derive', json=body)
        self.assertEqual(result.status_code, 201, result.text)
        self.assertTrue(result.json()['loadable'])
        self.assertEqual(result.json()['created_by'], 'alice')
        self.assertNotEqual(result.json()['id'], row['id'])
        self.assertEqual(self.client.post('/api/workflows', json={**row['workflow'], 'idempotency_key': 'forbidden'}).status_code, 403)
