"""Small read-only cache of the official Ascend Quay repository's live tags."""
import copy
import json
import re
import threading
import time
from urllib.request import Request, build_opener
from .sources import NoRedirects
from .domain import DomainError


class ImageCatalog:
    def __init__(self, opener=None):
        self.opener = opener or build_opener(NoRedirects()).open
        self.cache = {}
        self.lock = threading.Lock()

    def tags(self, page=1):
        if type(page) is not int or not 1 <= page <= 1000:
            raise DomainError('镜像标签页码无效', 422)
        with self.lock:
            cached = self.cache.get(page)
            if cached and time.monotonic() - cached[0] < 300:
                return copy.deepcopy(cached[1])
        request = Request(f'https://quay.io/api/v1/repository/ascend/vllm-ascend/tag/?onlyActiveTags=true&page={page}&limit=50',
                          headers={'Accept': 'application/json', 'User-Agent': 'Hive-NPU'})
        try:
            with self.opener(request, timeout=15) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError()
            document = json.loads(raw)
            if not isinstance(document.get('tags'), list) or len(document['tags']) > 50:
                raise ValueError()
            tags = []
            for tag in document['tags']:
                name = tag.get('name')
                if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', name):
                    raise ValueError()
                tags.append({'name': name, 'image': 'quay.io/ascend/vllm-ascend:' + name,
                             'last_modified': tag.get('last_modified'), 'manifest_digest': tag.get('manifest_digest')})
            result = {'tags': tags, 'has_more': document.get('has_additional') is True, 'page': page,
                      'repository': 'quay.io/ascend/vllm-ascend'}
        except (OSError, ValueError, TypeError, AttributeError):
            raise DomainError('暂时无法读取 Quay 镜像标签，请重试或填写已有镜像名称', 502) from None
        with self.lock:
            if len(self.cache) >= 64:
                self.cache.pop(next(iter(self.cache)))
            self.cache[page] = (time.monotonic(), result)
        return copy.deepcopy(result)


def register_image_routes(app, catalog, current, respond):
    from fastapi import Depends, Query
    @app.get('/api/images/vllm-ascend/tags')
    def tags(page: int = Query(default=1, ge=1, le=1000), actor=Depends(current)):
        return respond(catalog.tags(page))
