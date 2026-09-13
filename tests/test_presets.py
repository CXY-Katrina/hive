"""Preset HTTP behavior with disposable MySQL and simulated external GitHub."""
import json
import os
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from hive.api import respond
from hive.domain import DomainError
from hive.presets import Presets
from hive.presets_api import register_preset_routes
from hive.sources import SourceService
from tests import test_integration as integration
from tests.test_sources import FakeGitHub, github_file, API, HEAD, VLLM


@unittest.skipUnless(os.getenv('HIVE_TEST_MYSQL_PORT'), 'Requires isolated MySQL test service')
class PresetHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        integration.MySQLIntegration.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        integration.MySQLIntegration.tearDownClass.__func__(cls)

    def setUp(self):
        integration.MySQLIntegration.setUp(self)
        with self.db.transaction() as c:
            c.execute('DELETE FROM workflow_presets')
        self.github = FakeGitHub()
        self.sources = SourceService(opener=self.github)
        self.presets = Presets(self.db, self.sources, sample_item_id='sample-case')
        self.tokens = {name: self.identity.login(name, 'test-admin' if name == 'admin' else '')[0]
                       for name in ('admin', 'alice', 'bob')}
        app = FastAPI()
        @app.exception_handler(DomainError)
        async def error(request, exc):
            return respond({'detail': str(exc)}, exc.code)
        def current(request: Request):
            return self.identity.current(request.cookies.get('hive_session'))
        self.services = SimpleNamespace(presets=self.presets)
        register_preset_routes(app, self.services, current, respond)
        self.client = TestClient(app)
        self.client.cookies.set('hive_session', self.tokens['admin'])
        self.addCleanup(self.client.close)

    def test_empty_catalog_is_empty_and_read_requires_login(self):
        response = self.client.get('/api/presets')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/presets').status_code, 401)
        self.assertEqual(self.github.calls, [])

    def catalog_body(self, items=None):
        items = items if items is not None else [
            {'id': 'sample-case', 'name': 'Sample case', 'tags': {'cadence': 'nightly'}, 'workflow': {'name': 'case', 'jobs': []}},
            {'id': 'other-case', 'name': 'Other case', 'tags': {'cadence': 'weekly'}, 'workflow': {'name': 'other', 'jobs': []}}]
        path = 'tests/catalog.json'
        self.github.responses[f'{API}/contents/{path}?ref={HEAD}'] = github_file(path, json.dumps({'items': items}))
        return {'source': {'pr': 123, 'head_sha': HEAD, 'vllm_sha': VLLM}, 'path': path}

    def test_import_freezes_catalog_source_and_leaves_every_item_disabled(self):
        body = self.catalog_body()
        response = self.client.post('/api/presets/import', json=body)
        self.assertEqual(response.status_code, 201, response.text)
        rows = response.json()
        self.assertEqual({row['item_id'] for row in rows}, {'sample-case', 'other-case'})
        self.assertTrue(all(row['enabled'] is False for row in rows))
        self.assertTrue(all(row['source']['head_sha'] == HEAD and row['source']['vllm_sha'] == VLLM for row in rows))
        self.assertTrue(all(row['imported_by'] == 'admin' and row['approved_by'] is None for row in rows))
        self.assertTrue(all(row['path'] == 'tests/catalog.json' and len(row['sha256']) == 64 for row in rows))
        self.client.cookies.set('hive_session', self.tokens['alice'])
        self.assertEqual(len(self.client.get('/api/presets').json()), 2)
        self.assertEqual(self.github.calls[-1], f'{API}/contents/tests/catalog.json?ref={HEAD}')

    def test_only_admin_can_explicitly_enable_the_configured_single_sample(self):
        imported = self.client.post('/api/presets/import', json=self.catalog_body()).json()
        sample = next(row for row in imported if row['item_id'] == 'sample-case')
        other = next(row for row in imported if row['item_id'] == 'other-case')
        self.client.cookies.set('hive_session', self.tokens['alice'])
        self.assertEqual(self.client.post(f"/api/presets/{sample['id']}/enable").status_code, 403)
        self.assertEqual(self.client.post('/api/presets/import', json=self.catalog_body()).status_code, 403)
        self.client.cookies.set('hive_session', self.tokens['admin'])
        self.assertEqual(self.client.post(f"/api/presets/{other['id']}/enable").status_code, 409)
        response = self.client.post(f"/api/presets/{sample['id']}/enable")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['enabled'])
        self.assertEqual(response.json()['approved_by'], 'admin')
        self.assertIsNotNone(response.json()['approved_at'])
        listed = self.client.get('/api/presets').json()
        self.assertEqual(sum(row['enabled'] for row in listed), 1)
        self.assertEqual(self.client.post('/api/presets/enable').status_code, 404)

    def test_invalid_catalog_schema_is_rejected_atomically(self):
        body = self.catalog_body()
        valid = {'id':'sample-case','name':'Sample','tags':{},'workflow':{}}
        variants = [{'items':[valid,{**valid,'name':'Duplicate'}]},
                    {'items':[{**valid,'enabled':True}]}, {'items':[{**valid,'workflow':'run me'}]},
                    {'items':[{**valid,'tags':{'cadence':['nightly']}}]}, {'items':[{**valid,'id':'../sample'}]},
                    {'items':[{**valid,'name':' '}]}, {'items':'not a list'}]
        for value in variants:
            with self.subTest(value=value):
                self.github.responses[f'{API}/contents/tests/catalog.json?ref={HEAD}'] = github_file('tests/catalog.json', json.dumps(value))
                response = self.client.post('/api/presets/import', json=body)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.client.get('/api/presets').json(), [])

    def test_reimport_is_idempotent_and_cannot_replace_approved_content(self):
        body = self.catalog_body()
        first = self.client.post('/api/presets/import', json=body).json()
        sample = next(row for row in first if row['item_id'] == 'sample-case')
        self.client.post(f"/api/presets/{sample['id']}/enable")
        response = self.client.post('/api/presets/import', json=body)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual({row['id'] for row in response.json()}, {row['id'] for row in first})
        self.assertTrue(next(row for row in response.json() if row['id'] == sample['id'])['enabled'])
        changed = [{'id':'sample-case','name':'Changed','tags':{},'workflow':{'script':'changed'}}]
        self.catalog_body(changed)
        response = self.client.post('/api/presets/import', json=body)
        self.assertEqual(response.status_code, 409, response.text)
        listed = self.client.get('/api/presets').json()
        self.assertEqual(next(row for row in listed if row['id'] == sample['id'])['name'], 'Sample case')

    def test_only_json_catalog_paths_are_allowed_without_yaml_interpretation(self):
        body = self.catalog_body([])
        self.github.calls.clear()
        self.github.responses[f'{API}/contents/catalog.yaml?ref={HEAD}'] = github_file('catalog.yaml', '{"items":[]}')
        for path in ('catalog.yaml', '../catalog.json', 'dir/../catalog.json', '/catalog.json'):
            with self.subTest(path=path):
                response = self.client.post('/api/presets/import', json={**body,'path':path})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.github.calls, [])

    def test_missing_catalog_and_changed_head_do_not_create_entries(self):
        body = self.catalog_body()
        self.github.responses[f'{API}/contents/tests/catalog.json?ref={HEAD}'] = HTTPError('ignored',404,'not found',{},None)
        missing = self.client.post('/api/presets/import',json=body)
        self.assertEqual(missing.status_code,404)
        self.assertIn('预置 JSON 清单',missing.json()['detail'])
        changed = {**body,'source':{**body['source'],'head_sha':'3'*40}}
        self.assertEqual(self.client.post('/api/presets/import',json=changed).status_code,409)
        self.assertEqual(self.client.get('/api/presets').json(),[])

    def test_no_sample_configuration_keeps_imported_items_disabled(self):
        self.services.presets = Presets(self.db,self.sources)
        rows = self.client.post('/api/presets/import',json=self.catalog_body()).json()
        response = self.client.post(f"/api/presets/{rows[0]['id']}/enable")
        self.assertEqual(response.status_code,409)
        self.assertTrue(all(not row['enabled'] for row in self.client.get('/api/presets').json()))

    def test_fixed_catalog_revision_cannot_be_replaced_by_disjoint_item_ids(self):
        body = self.catalog_body()
        original = self.client.post('/api/presets/import',json=body).json()
        self.catalog_body([{'id':'replacement','name':'Replacement','tags':{},'workflow':{}}])
        response = self.client.post('/api/presets/import',json=body)
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual({row['id'] for row in self.client.get('/api/presets').json()}, {row['id'] for row in original})

    def test_second_catalog_cannot_enable_another_sample_at_the_same_time(self):
        body = self.catalog_body([{'id':'sample-case','name':'Sample','tags':{},'workflow':{}}])
        first = self.client.post('/api/presets/import',json=body).json()[0]
        self.github.responses[f'{API}/contents/other/catalog.json?ref={HEAD}'] = github_file(
            'other/catalog.json', '{"items":[{"id":"sample-case","name":"Other sample","tags":{},"workflow":{}}]}')
        second = self.client.post('/api/presets/import',json={**body,'path':'other/catalog.json'}).json()[0]
        self.assertEqual(self.client.post(f"/api/presets/{first['id']}/enable").status_code,200)
        self.assertEqual(self.client.post(f"/api/presets/{second['id']}/enable").status_code,409)
        self.assertEqual(sum(row['enabled'] for row in self.client.get('/api/presets').json()),1)

    def test_parameter_variant_is_private_and_does_not_change_approved_parent(self):
        from tests.test_workflows import payload
        body = self.catalog_body()
        parent = next(p for p in self.client.post('/api/presets/import', json=body).json() if p['item_id']=='sample-case')
        self.client.post('/api/presets/' + parent['id'] + '/enable')
        workflow = payload()
        workflow.pop('idempotency_key')
        workflow['source'] = {'pr':123,'head_sha':HEAD,'vllm_sha':VLLM}
        self.client.cookies.set('hive_session', self.tokens['alice'])
        response = self.client.post('/api/presets/' + parent['id'] + '/derive', json={'name':'Adjusted case','tags':{'variant':'small'},'workflow':workflow})
        self.assertEqual(response.status_code, 201, response.text)
        variant = response.json()
        self.assertEqual(variant['parent_id'], parent['id'])
        self.assertEqual(variant['validation_status'], 'unverified')
        self.assertEqual(variant['scope'], 'personal')
        self.assertTrue(variant['enabled'])
        own = self.client.get('/api/presets').json()
        self.assertIn(variant['id'], [p['id'] for p in own])
        self.assertEqual(next(p for p in own if p['id']==parent['id'])['name'], parent['name'])
        self.client.cookies.set('hive_session', self.tokens['bob'])
        self.assertNotIn(variant['id'], [p['id'] for p in self.client.get('/api/presets').json()])
        denied = self.client.post('/api/presets/' + variant['id'] + '/derive', json={'name':'Stolen','tags':{},'workflow':workflow})
        self.assertEqual(denied.status_code, 403, denied.text)

    def test_variant_preserves_merged_revision_when_client_omits_revision(self):
        from tests.test_workflows import payload
        from hive.domain import encode
        parent = next(p for p in self.client.post('/api/presets/import',json=self.catalog_body()).json() if p['item_id']=='sample-case')
        pinned = {**parent['source'],'revision':'merged'}
        with self.db.transaction() as c:
            c.execute('UPDATE workflow_presets SET source=%s WHERE id=%s',(encode(pinned),parent['id']))
        self.client.post('/api/presets/'+parent['id']+'/enable')
        workflow = payload()
        workflow.pop('idempotency_key')
        workflow['source'] = {'pr':123,'head_sha':HEAD,'vllm_sha':VLLM}
        response = self.client.post('/api/presets/'+parent['id']+'/derive',json={'name':'Merged variant','workflow':workflow})
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.json()['workflow']['source']['revision'],'merged')

    def test_admin_can_publish_successful_workflow_as_reusable_baseline_without_node_pin(self):
        from tests.test_workflows import payload
        from hive.domain import uid, encode, now
        workflow = payload()
        workflow['source'] = {'pr':123,'head_sha':HEAD,'vllm_sha':VLLM}
        workflow['resource']['target_node_ids'] = ['11111111-1111-1111-1111-111111111111']
        task_id, space_id, request_id = uid(), uid(), uid()
        actor = self.identity.current(self.tokens['admin'])
        with self.db.transaction() as c:
            c.execute('INSERT INTO resource_requests (id,owner_user_id,owner_name,idempotency_key,body_hash,spec,status,purpose,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)', (request_id,actor.id,actor.username,'publish-request','b'*64,encode(workflow['resource']),'RELEASED','task',now()))
            c.execute('INSERT INTO workflow_spaces (id,request_id,owner_user_id,owner_name,status,spec,runtime,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',(space_id,request_id,actor.id,actor.username,'CLOSED',encode(workflow),'{}',now()))
            c.execute('INSERT INTO workflows (id,space_id,owner_user_id,owner_name,idempotency_key,body_hash,name,spec,status,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(task_id,space_id,actor.id,actor.username,'publish-run','a'*64,'Actual run',encode(workflow),'SUCCEEDED',now()))
        try:
            request = {'workflow_id':task_id,'item_id':'sample-case','name':'Verified sample','tags':{'cadence':'nightly'}}
            self.client.cookies.set('hive_session', self.tokens['alice'])
            self.assertEqual(self.client.post('/api/presets/from-workflow', json=request).status_code,403)
            self.client.cookies.set('hive_session', self.tokens['admin'])
            response=self.client.post('/api/presets/from-workflow', json=request)
            self.assertEqual(response.status_code,201,response.text)
            case=response.json()
            self.assertEqual(case['source_workflow_id'],task_id)
            self.assertEqual(case['validation_status'],'execution_passed')
            self.assertFalse(case['enabled'])
            self.assertEqual(case['workflow']['resource']['target_node_ids'],[])
            self.assertEqual(self.client.post('/api/presets/'+case['id']+'/enable').status_code,200)
            self.assertEqual(self.client.post('/api/presets/from-workflow',json=request).json()['id'],case['id'])
            with self.db.transaction() as c:
                c.execute('UPDATE workflows SET status=%s WHERE id=%s', ('FAILED',task_id))
            rejected = self.client.post('/api/presets/from-workflow',json={**request,'item_id':'failed-case'})
            self.assertEqual(rejected.status_code,409,rejected.text)
            derived = self.client.post('/api/presets/'+case['id']+'/derive', json={
                'name':'Editable despite failed evidence','workflow':workflow})
            self.assertEqual(derived.status_code, 201, derived.text)
            self.assertEqual(derived.json()['validation_status'], 'unverified')
            self.assertTrue(derived.json()['loadable'])
        finally:
            with self.db.transaction() as c:
                c.execute('DELETE FROM workflows WHERE id=%s',(task_id,))
                c.execute('DELETE FROM workflow_spaces WHERE id=%s',(space_id,))

    def test_workflow_draft_follows_execution_without_changing_immutable_preset(self):
        from tests.test_workflows import payload
        from hive.domain import uid, encode, now
        workflow = payload()
        workflow['source'] = {'pr':123,'head_sha':HEAD,'vllm_sha':VLLM}
        task_id, space_id, request_id = uid(), uid(), uid()
        actor = self.identity.current(self.tokens['admin'])
        with self.db.transaction() as c:
            c.execute('INSERT INTO resource_requests (id,owner_user_id,owner_name,idempotency_key,body_hash,spec,status,purpose,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                      (request_id,actor.id,actor.username,'draft-request','b'*64,encode(workflow['resource']),'QUEUED','task',now()))
            c.execute('INSERT INTO workflow_spaces (id,request_id,owner_user_id,owner_name,status,spec,runtime,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                      (space_id,request_id,actor.id,actor.username,'QUEUED',encode(workflow),'{}',now()))
            c.execute('INSERT INTO workflows (id,space_id,owner_user_id,owner_name,idempotency_key,body_hash,name,spec,status,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                      (task_id,space_id,actor.id,actor.username,'draft-run','a'*64,'Queued run',encode(workflow),'QUEUED',now()))
        try:
            request = {'workflow_id':task_id,'item_id':'sample-case','name':'Waiting sample','tags':{}}
            response = self.client.post('/api/presets/from-workflow', json=request)
            self.assertEqual(response.status_code, 201, response.text)
            original = response.json()
            self.assertFalse(original['enabled'])
            self.assertEqual(original['validation_status'], 'pending_execution')
            self.assertEqual(original['source_workflow_status'], 'QUEUED')
            self.assertTrue(original['reason'])
            for status in ('QUEUED', 'PREPARING', 'RUNNING', 'FAILED', 'CANCELLED'):
                with self.subTest(status=status):
                    with self.db.transaction() as c:
                        c.execute('UPDATE workflows SET status=%s WHERE id=%s', (status,task_id))
                    current = next(item for item in self.client.get('/api/presets').json() if item['id'] == original['id'])
                    expected = {'FAILED': 'execution_failed', 'CANCELLED': 'cancelled'}.get(status, 'pending_execution')
                    self.assertEqual(current['validation_status'], expected)
                    self.assertTrue(current['loadable'])
                    self.assertEqual(current['source_workflow_status'], status)
                    self.assertFalse(current['enabled'])
                    self.assertTrue(current['reason'])
                    self.assertEqual(self.client.post('/api/presets/'+original['id']+'/enable').status_code,409)
                    if status in {'QUEUED','PREPARING','RUNNING'}:
                        saved = self.client.post('/api/presets/from-workflow', json=request)
                        self.assertEqual(saved.status_code, 201, saved.text)
                        self.assertEqual(saved.json()['id'], original['id'])
            with self.db.transaction() as c:
                c.execute('UPDATE workflows SET status=%s WHERE id=%s', ('SUCCEEDED',task_id))
            passed = next(item for item in self.client.get('/api/presets').json() if item['id'] == original['id'])
            self.assertEqual(passed['validation_status'], 'execution_passed')
            self.assertFalse(passed['enabled'])
            self.assertEqual(passed['sha256'], original['sha256'])
            self.assertEqual(passed['workflow'], original['workflow'])
            self.assertEqual(self.client.post('/api/presets/'+original['id']+'/enable').status_code,200)
            with self.db.transaction() as c:
                c.execute('DELETE FROM workflows WHERE id=%s', (task_id,))
            missing = next(item for item in self.client.get('/api/presets').json() if item['id'] == original['id'])
            self.assertEqual(missing['validation_status'], 'unknown')
            self.assertFalse(missing['enabled'])
            self.assertEqual(self.client.post('/api/presets/'+original['id']+'/enable').status_code,409)
        finally:
            with self.db.transaction() as c:
                c.execute('DELETE FROM workflows WHERE id=%s', (task_id,))
                c.execute('DELETE FROM workflow_spaces WHERE id=%s', (space_id,))
