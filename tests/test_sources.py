"""Public source HTTP API tests; only GitHub's external HTTP is simulated."""
import base64
import hashlib
import io
import json
import unittest
from urllib.error import HTTPError, URLError
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from hive.api import respond
from hive.domain import Actor, DomainError
from hive.sources import SourceService
from hive.sources_api import register_source_routes


HEAD = '1' * 40
VLLM = '2' * 40
REPOSITORY = 'vllm-project/vllm-ascend'
API = 'https://api.github.com/repos/' + REPOSITORY
COMMIT_FILE = '.github/vllm-main-verified.commit'


def github_file(path, content):
    raw = content.encode() if isinstance(content, str) else content
    digest = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    return {'type': 'file', 'path': path, 'encoding': 'base64', 'size': len(raw),
            'sha': digest, 'content': base64.b64encode(raw).decode()}


class FakeGitHub:
    """Reusable urllib-compatible external HTTP fixture; unexpected URLs fail."""
    def __init__(self, pr=123, head_sha=HEAD, vllm_sha=VLLM):
        self.calls = []
        self.responses = {
            f'{API}/pulls/{pr}': {'number': pr, 'base': {'repo': {'full_name': REPOSITORY}},
                                'head': {'sha': head_sha}},
            f'{API}/contents/{COMMIT_FILE}?ref={head_sha}': github_file(COMMIT_FILE, vllm_sha + '\n')}

    def __call__(self, request, timeout=15):
        self.calls.append(request.full_url)
        value = self.responses[request.full_url]
        if isinstance(value, Exception):
            raise value
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        return io.BytesIO(raw)


class SourceHTTPTests(unittest.TestCase):
    def test_main_branch_is_resolved_and_preview_rejects_branch_movement(self):
        self.github.responses[f'{API}/commits/main'] = {'sha': HEAD}
        result = self.client.post('/api/sources/resolve', json={'branch': 'main'})
        self.assertEqual(result.status_code, 200, result.text)
        source = result.json()
        self.assertEqual(source['branch'], 'main')
        self.assertEqual(source['head_sha'], HEAD)
        self.assertEqual(source['vllm_sha'], VLLM)
        self.assertNotIn('pr', source)
        path = 'tests/nightly/case.yaml'
        self.github.responses[f'{API}/contents/{path}?ref={HEAD}'] = github_file(path, 'batch_size: 45\n')
        self.assertEqual(self.client.post('/api/sources/file', json={'source': source, 'path': path}).status_code, 200)
        newer = '3' * 40
        self.github.responses[f'{API}/commits/main'] = {'sha': newer}
        self.github.responses[f'{API}/contents/{COMMIT_FILE}?ref={newer}'] = github_file(COMMIT_FILE, VLLM + '\n')
        response = self.client.post('/api/sources/file', json={'source': source, 'path': path})
        self.assertEqual(response.status_code, 409, response.text)

    def test_commit_source_resolves_without_pr_and_previews_original_yaml(self):
        path = 'tests/e2e/nightly/case.yaml'
        self.github.responses[f'{API}/commits/{HEAD}'] = {'sha': HEAD}
        self.github.responses[f'{API}/contents/{path}?ref={HEAD}'] = github_file(path, 'cases: []\n')
        response = self.client.post('/api/sources/resolve', json={'commit': HEAD})
        self.assertEqual(response.status_code, 200, response.text)
        source = response.json()
        self.assertEqual(source['revision'], 'commit')
        self.assertEqual(source['head_sha'], HEAD)
        self.assertEqual(source['vllm_sha'], VLLM)
        self.assertNotIn('pr', source)
        preview = self.client.post('/api/sources/file', json={'source': source, 'path': path})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()['content'], 'cases: []\n')
        self.assertFalse(any('/pulls/' in url for url in self.github.calls))

    def setUp(self):
        self.github = FakeGitHub()
        self.sources = SourceService(opener=self.github)
        app = FastAPI()
        @app.exception_handler(DomainError)
        async def error(request, exc):
            return respond({'detail': str(exc)}, exc.code)
        def current(request: Request):
            if request.cookies.get('hive_session') != 'test':
                raise DomainError('请先登录', 401)
            return Actor('alice', 'alice')
        register_source_routes(app, SimpleNamespace(sources=self.sources), current, respond)
        self.client = TestClient(app)
        self.client.cookies.set('hive_session', 'test')
        self.addCleanup(self.client.close)

    def test_resolve_pr_freezes_head_and_reads_vllm_revision_at_that_head(self):
        response = self.client.post('/api/sources/resolve', json={'pr': 123})
        self.assertEqual(response.status_code, 200, response.text)
        source = response.json()
        self.assertEqual(source['repository'], REPOSITORY)
        self.assertEqual(source['head_sha'], HEAD)
        self.assertEqual(source['vllm_sha'], VLLM)
        self.assertEqual(source['commit_file'], COMMIT_FILE)
        self.assertEqual(self.github.calls, [f'{API}/pulls/123', f'{API}/contents/{COMMIT_FILE}?ref={HEAD}'])

    def test_only_requested_vllm_ascend_pr_identity_is_accepted(self):
        self.github.responses[f'{API}/pulls/123']['base']['repo']['full_name'] = 'other/repository'
        response = self.client.post('/api/sources/resolve', json={'pr': 123})
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(self.github.calls, [f'{API}/pulls/123'])

    def test_file_preview_returns_frozen_utf8_content_and_digest(self):
        source = self.client.post('/api/sources/resolve', json={'pr': 123}).json()
        self.github.responses[f'{API}/contents/tests/nightly/run.sh?ref={HEAD}'] = github_file('tests/nightly/run.sh', 'echo hello\n')
        response = self.client.post('/api/sources/file', json={'source': source, 'path': 'tests/nightly/run.sh'})
        self.assertEqual(response.status_code, 200, response.text)
        file = response.json()
        self.assertEqual(file['content'], 'echo hello\n')
        self.assertEqual(file['sha256'], '5dbad7dd0b9b122dcd9956884390f4aac4738caba8ff53498a7ab6718b176c30')
        self.assertEqual(file['head_sha'], HEAD)
        self.assertEqual(file['path'], 'tests/nightly/run.sh')
        self.assertEqual(file['size'], 11)

    def test_untrusted_source_and_path_are_rejected_before_github(self):
        source = self.client.post('/api/sources/resolve', json={'pr': 123}).json()
        invalid = [({'repository': 'other/repo'}, 'run.sh'), ({'head_sha': 'main'}, 'run.sh'),
                   ({}, '../run.sh'), ({}, '/run.sh'), ({}, 'a/../run.sh'), ({}, 'a%2Frun.sh'),
                   ({}, 'a\\run.sh'), ({}, 'secrets.env'), ({}, 'https://example.com/run.sh')]
        for changes, path in invalid:
            with self.subTest(changes=changes, path=path):
                self.github.calls.clear()
                response = self.client.post('/api/sources/file', json={'source': {**source, **changes}, 'path': path})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.github.calls, [])

    def test_file_contents_require_matching_blob_sha_size_and_plain_file_type(self):
        source = self.client.post('/api/sources/resolve', json={'pr': 123}).json()
        endpoint = f'{API}/contents/test.py?ref={HEAD}'
        for changes in ({'sha': '0' * 40}, {'size': 999}, {'type': 'symlink'},
                        {'path': 'other.py'}, {'encoding': 'none'}, {'size': 1048577}):
            with self.subTest(changes=changes):
                self.github.responses[endpoint] = {**github_file('test.py', 'print(1)\n'), **changes}
                response = self.client.post('/api/sources/file', json={'source': source, 'path': 'test.py'})
                self.assertEqual(response.status_code, 502, response.text)

    def test_preview_rejects_pr_head_change_instead_of_silently_reading_new_code(self):
        source = self.client.post('/api/sources/resolve', json={'pr': 123}).json()
        self.github.responses[f'{API}/contents/run.sh?ref={HEAD}'] = github_file('run.sh', 'echo old\n')
        changed = '3' * 40
        self.github.responses[f'{API}/pulls/123']['head']['sha'] = changed
        self.github.responses[f'{API}/contents/{COMMIT_FILE}?ref={changed}'] = github_file(COMMIT_FILE, VLLM + '\n')
        response = self.client.post('/api/sources/file', json={'source': source, 'path': 'run.sh'})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertNotIn(f'{API}/contents/run.sh?ref={changed}', self.github.calls)

    def test_pr_input_accepts_number_or_canonical_repo_url_only(self):
        for value in ('123', 'https://github.com/vllm-project/vllm-ascend/pull/123'):
            response = self.client.post('/api/sources/resolve', json={'pr': value})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['pr'], 123)
        for value in (True, 0, -1, 'https://github.com/other/repo/pull/123',
                      'http://localhost/pull/123', 'https://github.com@evil.example/pull/123',
                      'https://github.com/vllm-project/vllm-ascend/pull/123?x=1'):
            self.github.calls.clear()
            response = self.client.post('/api/sources/resolve', json={'pr': value})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.github.calls, [])

    def test_auth_errors_and_untrusted_remote_errors_do_not_expose_details(self):
        self.client.cookies.clear()
        response = self.client.post('/api/sources/resolve', json={'pr': 123})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.github.calls, [])
        self.client.cookies.set('hive_session', 'test')
        for failure in (HTTPError(f'{API}/pulls/123', 403, 'secret-token', {}, None),
                        URLError('secret-token'), b'invalid secret-token JSON', b'x' * 2097153):
            self.github.responses[f'{API}/pulls/123'] = failure
            response = self.client.post('/api/sources/resolve', json={'pr': 123})
            self.assertEqual(response.status_code, 502)
            self.assertNotIn('secret-token', response.text)

    def test_malformed_verified_commit_file_never_defaults_to_latest_vllm(self):
        for content in ('main\n', VLLM + '\n' + HEAD, '# revision\n' + VLLM):
            self.github.responses[f'{API}/contents/{COMMIT_FILE}?ref={HEAD}'] = github_file(COMMIT_FILE, content)
            response = self.client.post('/api/sources/resolve', json={'pr': 123})
            self.assertEqual(response.status_code, 422)
        self.github.responses[f'{API}/contents/{COMMIT_FILE}?ref={HEAD}'] = HTTPError('ignored', 404, 'private', {}, None)
        self.assertEqual(self.client.post('/api/sources/resolve', json={'pr': 123}).status_code, 404)

    def test_rejected_http_response_is_closed_without_reading_its_body(self):
        body = io.BytesIO(b'private upstream error text')
        self.github.responses[f'{API}/pulls/123'] = HTTPError('ignored', 403, 'Denied', {}, body)
        self.assertEqual(self.client.post('/api/sources/resolve', json={'pr': 123}).status_code, 502)
        self.assertTrue(body.closed)

    def test_merged_pr_resolves_exact_merge_revision_for_nightly_baseline(self):
        merge = '3' * 40
        self.github.responses[f'{API}/pulls/123'].update(merged=True, merge_commit_sha=merge)
        self.github.responses[f'{API}/contents/{COMMIT_FILE}?ref={merge}'] = github_file(COMMIT_FILE, VLLM + '\n')
        response = self.client.post('/api/sources/resolve', json={'pr':123, 'revision':'merged'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['head_sha'], merge)
        self.assertEqual(response.json()['revision'], 'merged')
        self.github.responses[f'{API}/pulls/123']['merged'] = False
        self.assertEqual(self.client.post('/api/sources/resolve', json={'pr':123, 'revision':'merged'}).status_code, 422)

    def test_file_source_rejects_non_string_revision(self):
        source = self.client.post('/api/sources/resolve',json={'pr':123}).json()
        for revision in ([],{},None,1):
            response = self.client.post('/api/sources/file',json={'source':{**source,'revision':revision},'path':'case.yaml'})
            self.assertEqual(response.status_code,422,response.text)
