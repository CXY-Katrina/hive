"""Repository-owned task bundles, independent of scheduling and upstream code."""
import hashlib
import json
import re
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .domain import DomainError, encode


DEFAULT_ROOT = Path(__file__).resolve().parents[2] / 'preset_tasks'
PREFIX = 'hive_presets/'


class PresetArchive:
    def __init__(self, root=DEFAULT_ROOT):
        self.root = Path(root)

    def file(self, path, uploaded=None):
        if not path.startswith(PREFIX):
            return None
        relative = path[len(PREFIX):]
        if (not re.fullmatch(r'[a-z0-9][a-z0-9_-]*/[A-Za-z0-9_.-]+', relative)
                or not relative.endswith(('.py', '.sh', '.yaml', '.yml'))):
            raise DomainError('预置脚本路径无效', 422)
        folder, name = relative.split('/')
        directory = self.root / folder
        try:
            if directory.is_symlink() or (directory / name).is_symlink():
                raise ValueError()
            source = json.loads((directory / 'source.json').read_text(encoding='utf-8'))
            if name not in source['helper_files']:
                raise ValueError()
            raw = (directory / name).read_bytes()
            if len(raw) > 262144 or b'\0' in raw:
                raise ValueError()
            content = raw.decode('utf-8')
        except (OSError, ValueError, KeyError):
            raise DomainError('预置脚本不在本地任务归档中', 422) from None
        if uploaded is not None and uploaded != content:
            raise DomainError('上传脚本与 Hive 任务归档不一致，请更新归档后重新载入', 409)
        return {'path': path, 'content': content, 'sha256': hashlib.sha256(raw).hexdigest(),
                'size': len(raw), 'uploaded': True, 'origin': 'hive_archive'}

    def list(self):
        rows, seen = [], set()
        for path in sorted(self.root.glob('*/source.json')):
            directory = path.parent
            workflow_path = directory / 'workflow.json'
            if not workflow_path.is_file():
                continue
            metadata = json.loads(path.read_text(encoding='utf-8'))
            head, yaml = metadata['upstream_commit'], metadata['nightly_yaml']
            if yaml in seen:
                raise DomainError('同一 nightly YAML 只能归档一个公共预置: ' + yaml, 500)
            seen.add(yaml)
            source = {'revision': 'commit', 'commit': head, 'head_sha': head,
                      'vllm_sha': metadata['vllm_commit'], 'repository': 'vllm-project/vllm-ascend',
                      'url': 'https://github.com/vllm-project/vllm-ascend/commit/' + head}
            workflow = json.loads(workflow_path.read_text(encoding='utf-8'))
            workflow['source'] = source
            # Freeze the maintained files through the same upload boundary as UI scripts.
            files = [self.file(PREFIX + directory.name + '/' + name) for name in metadata['helper_files']]
            workflow['environments'][0]['install'][0]['files'] = [
                {'name': file['path'], 'content': file['content']} for file in files]
            item_id = metadata.get('id', directory.name)
            rows.append({'id': str(uuid5(NAMESPACE_URL, 'hive:preset:' + yaml)), 'item_id': item_id,
                         'name': metadata.get('name', workflow['name']), 'scope': 'public', 'origin': 'archive',
                         'enabled': True, 'loadable': True, 'validation_status': 'unverified',
                         'reason': '可载入并修改；实际结果以每次执行记录为准。',
                         'created_by': metadata['created_by'], 'imported_by': metadata['created_by'],
                         'yaml_path': yaml, 'path': 'preset_tasks/' + directory.name,
                         'tags': metadata.get('tags', {}), 'source': source, 'workflow': workflow,
                         'sha256': hashlib.sha256(encode(workflow).encode()).hexdigest()})
        return rows

    def get(self, ident):
        return next((row for row in self.list() if row['id'] == ident), None)
