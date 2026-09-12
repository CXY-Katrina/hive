"""Pinned vllm-ascend PR source retrieval; GitHub HTTP is the only boundary."""
import base64
import hashlib
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .domain import DomainError, now


REPOSITORY = 'vllm-project/vllm-ascend'
API = 'https://api.github.com/repos/' + REPOSITORY
COMMIT_FILE = '.github/vllm-main-verified.commit'
MAX_FILE = 1024 * 1024
MAX_RESPONSE = 2 * 1024 * 1024


def validate_file_request(source, path):
    if (not isinstance(source, dict) or source.get('repository') != REPOSITORY
            or type(source.get('pr')) is not int or not 1 <= source['pr'] <= 2_147_483_647
            or source.get('commit_file') != COMMIT_FILE
            or any(not isinstance(source.get(key), str) or not re.fullmatch(pattern, source[key])
                   for key, pattern in (('head_sha', r'[0-9a-f]{40}'), ('vllm_sha', r'[0-9a-f]{40}'),
                                        ('commit_file_sha256', r'[0-9a-f]{64}')))):
        raise DomainError('来源必须是已解析的 vllm-ascend PR 和完整提交 SHA', 422)
    if (not isinstance(path, str) or len(path) > 512
            or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', path)
            or any(part in {'.', '..'} for part in path.split('/'))
            or not path.lower().endswith(('.sh', '.py', '.yaml', '.yml', '.json'))):
        raise DomainError('文件路径必须是仓库内的 sh/py/yaml/yml/json 文件，禁止路径跳转', 422)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SourceService:
    def __init__(self, opener=None):
        self.opener = opener or build_opener(NoRedirects()).open

    def _json(self, url):
        request = Request(url, headers={'Accept': 'application/vnd.github+json',
                          'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'Hive-NPU'})
        try:
            with self.opener(request, timeout=15) as response:
                raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise DomainError('GitHub 响应过大', 502)
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except HTTPError as exc:
            message = 'GitHub PR 或文件不存在' if exc.code == 404 else 'GitHub 访问受限或限流，请稍后重试' if exc.code in (401, 403, 429) else 'GitHub 请求失败'
            exc.close()
            raise DomainError(message, 404 if exc.code == 404 else 502) from None
        except (URLError, OSError, ValueError, UnicodeError):
            raise DomainError('GitHub 响应无效或网络不可用', 502) from None

    def _content(self, head_sha, path):
        value = self._json(f'{API}/contents/{quote(path, safe="/")}?ref={head_sha}')
        try:
            if (value.get('type') != 'file' or value.get('path') != path or value.get('encoding') != 'base64'
                    or value.get('target') or value.get('submodule_git_url')
                    or type(value.get('size')) is not int or not 0 <= value['size'] <= MAX_FILE
                    or not isinstance(value.get('content'), str)):
                raise ValueError()
            raw = base64.b64decode(''.join(value['content'].split()), validate=True)
            blob_sha = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            if len(raw) != value['size'] or len(raw) > MAX_FILE or value.get('sha') != blob_sha or b'\0' in raw:
                raise ValueError()
            content = raw.decode('utf-8')
        except (KeyError, ValueError, UnicodeError):
            raise DomainError('GitHub 文件类型、大小、SHA 或 UTF-8 内容校验失败（单文件最多 1 MiB）', 502) from None
        return {'repository': REPOSITORY, 'head_sha': head_sha, 'path': path,
                'content': content, 'sha256': hashlib.sha256(raw).hexdigest(),
                'git_blob_sha': value['sha'], 'size': len(raw)}

    def resolve(self, pr):
        if isinstance(pr, str):
            match = re.fullmatch(r'(?:https://github\.com/vllm-project/vllm-ascend/pull/)?([1-9][0-9]{0,9})/?', pr.strip())
            pr = int(match[1]) if match else None
        if type(pr) is not int or not 1 <= pr <= 2_147_483_647:
            raise DomainError('请输入有效的 vllm-ascend PR 编号', 422)
        value = self._json(f'{API}/pulls/{pr}')
        try:
            if value['number'] != pr or value['base']['repo']['full_name'] != REPOSITORY:
                raise ValueError()
            head_sha = value['head']['sha']
            if not re.fullmatch(r'[0-9a-f]{40}', head_sha):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise DomainError('GitHub PR 没有有效的固定提交 SHA', 502) from None
        commit = self._content(head_sha, COMMIT_FILE)
        vllm_sha = commit['content'].strip()
        if not re.fullmatch(r'[0-9a-f]{40}', vllm_sha):
            raise DomainError('vLLM commit 文件必须包含唯一的完整提交 SHA', 422)
        return {'pr': pr, 'repository': REPOSITORY, 'head_sha': head_sha, 'vllm_sha': vllm_sha,
                'commit_file': COMMIT_FILE, 'commit_file_sha256': commit['sha256'],
                'resolved_at': str(now()), 'url': f'https://github.com/{REPOSITORY}/pull/{pr}'}

    def file(self, source, path):
        """Read the immutable revision resolved and retained by the server."""
        validate_file_request(source, path)
        return self._content(source['head_sha'], path)

    def upload_path(self, source, name, tree=None):
        """Resolve a picked local filename in one complete, immutable PR tree."""
        validate_file_request(source, name)
        tree = tree if tree is not None else self._json(f"{API}/git/trees/{source['head_sha']}?recursive=1")
        if tree.get('truncated') is not False or not isinstance(tree.get('tree'), list):
            raise DomainError('PR 文件清单不完整，无法确认上传文件来源', 502)
        matches = [item['path'] for item in tree['tree'] if item.get('type') == 'blob'
                   and item.get('mode') in {'100644', '100755'} and isinstance(item.get('path'), str)
                   and (item['path'] == name or item['path'].endswith('/' + name))]
        if name in matches:
            return name, tree
        if len(matches) != 1:
            raise DomainError('上传文件在 PR 中不存在或重名，请使用 PR 相对路径命名: ' + name, 422)
        return matches[0], tree
