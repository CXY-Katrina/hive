"""Authenticated Quay tag browsing through the public HTTP endpoint."""
import io
import json
import unittest
from types import SimpleNamespace
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from hive.api import respond
from hive.domain import DomainError
from hive.image_catalog import ImageCatalog, register_image_routes


class ImageCatalogHTTP(unittest.TestCase):
    def setUp(self):
        self.calls = []
        def quay(request, timeout=15):
            self.calls.append(request.full_url)
            return io.BytesIO(json.dumps({'tags': [{'name': 'v0.11.0rc1', 'last_modified': '2026-09-01'}],
                                           'has_additional': True}).encode())
        self.catalog = ImageCatalog(opener=quay)
        app = FastAPI()
        @app.exception_handler(DomainError)
        async def error(request, exc):
            return respond({'detail': str(exc)}, exc.code)
        def current(request: Request):
            if request.cookies.get('hive_session') != 'test':
                raise DomainError('请先登录', 401)
            return SimpleNamespace(username='alice')
        register_image_routes(app, self.catalog, current, respond)
        self.client = TestClient(app)
        self.client.cookies.set('hive_session', 'test')
        self.addCleanup(self.client.close)

    def test_tags_are_real_paged_quay_references_and_cached(self):
        result = self.client.get('/api/images/vllm-ascend/tags?page=2')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['tags'][0]['image'], 'quay.io/ascend/vllm-ascend:v0.11.0rc1')
        self.assertTrue(result.json()['has_more'])
        self.assertEqual(result.json()['page'], 2)
        self.client.get('/api/images/vllm-ascend/tags?page=2')
        self.assertEqual(self.calls, ['https://quay.io/api/v1/repository/ascend/vllm-ascend/tag/?onlyActiveTags=true&page=2&limit=50'])
        self.assertEqual(self.client.get('/api/images/vllm-ascend/tags?page=0').status_code, 422)

    def test_requires_login_and_reports_upstream_failure_without_fake_tags(self):
        def fail(*args, **kwargs):
            raise OSError('network down')
        self.catalog.opener = fail
        self.assertEqual(self.client.get('/api/images/vllm-ascend/tags').status_code, 502)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/images/vllm-ascend/tags').status_code, 401)
