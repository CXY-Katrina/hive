"""Administrator-owned public definitions and plain-text display overrides."""
import hashlib
import re

from pydantic import ValidationError

from .domain import DomainError, decode, encode, now, uid
from .identity import Identity
from .workflow_schemas import WorkflowCreate


class PresetManagement:
    def __init__(self, presets):
        self.presets = presets
        self.db = presets.db

    @staticmethod
    def public(row):
        row = dict(row)
        for key in ('workflow', 'source', 'tags'):
            row[key] = decode(row[key], {})
        row.update(scope='public', origin='manual', enabled=True, loadable=True,
                   item_id=row['id'], path='@public/' + row['id'], default_ref='main',
                   validation_status='unverified', reason='管理员维护的配置；实际结果以每次执行记录为准。')
        return row

    def list(self):
        return [self.public(row) for row in self.db.all('SELECT * FROM workflow_public_presets ORDER BY name,id')]

    def get(self, ident):
        row = self.db.one('SELECT * FROM workflow_public_presets WHERE id=%s', (ident,))
        return self.public(row) if row else None

    def decorate(self, rows):
        overrides = {row['preset_id']: row['remarks'] for row in self.db.all('SELECT preset_id,remarks FROM workflow_preset_remarks')}
        return [{**row, 'remarks': overrides.get(row['id'])} for row in rows]

    def remarks(self, actor, ident, remarks):
        Identity.require_admin(actor)
        self.presets.get(ident, actor=actor)
        if not isinstance(remarks, str) or len(remarks) > 4000 or '\x00' in remarks:
            raise DomainError('备注必须是最多 4000 字符的纯文本', 422)
        with self.db.transaction() as cursor:
            cursor.execute('''INSERT INTO workflow_preset_remarks (preset_id,remarks,updated_by,updated_at)
                VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE remarks=VALUES(remarks),
                updated_by=VALUES(updated_by),updated_at=VALUES(updated_at)''', (ident,remarks,actor.username,now()))
            self.db.audit(cursor,actor,'preset.remarks',ident,{'length':len(remarks)})
        return self.presets.get(ident, actor=actor)

    def create(self, actor, name, tags, workflow, remarks='', yaml_path='', base_preset_id=None):
        Identity.require_admin(actor)
        name = name.strip()
        if (not name or len(name) > 128 or '\x00' in name or len(tags) > 32
                or any(not isinstance(k,str) or not 1 <= len(k) <= 64 or '\x00' in k
                       or not isinstance(v,str) or len(v) > 256 or '\x00' in v for k,v in tags.items())):
            raise DomainError('名称最多 128 字符；标签最多 32 个，键最多 64 字符、值最多 256 字符',422)
        if len(remarks) > 4000 or '\x00' in remarks:
            raise DomainError('备注最多 4000 字符，不能包含 NUL',422)
        if yaml_path and (len(yaml_path) > 512 or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.(?:yaml|yml)',yaml_path)
                          or any(part in {'.','..'} for part in yaml_path.split('/'))):
            raise DomainError('YAML 来源须为仓库内相对路径',422)
        try:
            if len(encode(workflow).encode()) > 2 * 1024 * 1024:
                raise DomainError('预置配置最多 2 MiB',422)
            parsed = WorkflowCreate.model_validate({**workflow,'name':name,'idempotency_key':'public-preset',
                                                   'source':workflow.get('source') or {'revision':'branch','branch':'main'}})
        except (ValidationError, ValueError, TypeError) as exc:
            message = exc.errors()[0]['msg'] if isinstance(exc,ValidationError) else '预置配置必须是有效的 JSON 对象'
            raise DomainError(message,422) from None
        if parsed.space_id:
            raise DomainError('公共预置需要机器规格，不能绑定已有运行空间',422)
        source = parsed.source or {'revision':'branch','branch':'main'}
        if source.get('repository') not in (None,'vllm-project/vllm-ascend'):
            raise DomainError('仅支持 vllm-project/vllm-ascend 来源',422)
        base = self.presets.get(base_preset_id,actor=actor) if base_preset_id else None
        if (base and base.get('origin') in {'archive','manual'} and source.get('head_sha') and source == base.get('source')):
            resolved = base['source']
        else:
            resolved = self.presets.sources.resolve_spec(source)
            if any(source.get(key) and source[key] != resolved[key] for key in ('head_sha','vllm_sha')):
                raise DomainError('来源已经变化，请重新解析来源后保存',409)
        # Store an immutable reference even when the author selected main or a PR.
        frozen = {**resolved,'revision':'commit','commit':resolved['head_sha']}
        frozen.pop('pr',None)
        frozen.pop('branch',None)
        config = parsed.model_dump(exclude={'idempotency_key','space_id','preset_id'})
        config.update(source=frozen, retain_minutes=0)
        config['resource']['target_node_ids'] = []
        ident, stamp = uid(), now()
        with self.db.transaction() as cursor:
            cursor.execute('''INSERT INTO workflow_public_presets
                (id,name,tags,workflow,source,yaml_path,sha256,created_by,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (ident,name,encode(tags),encode(config),encode(frozen),yaml_path,
                 hashlib.sha256(encode(config).encode()).hexdigest(),actor.username,stamp))
            cursor.execute('INSERT INTO workflow_preset_remarks (preset_id,remarks,updated_by,updated_at) VALUES (%s,%s,%s,%s)',
                           (ident,remarks,actor.username,stamp))
            self.db.audit(cursor,actor,'preset.create',ident,{'source_head':frozen['head_sha'],'base_preset_id':base_preset_id})
        return self.presets.get(ident,actor=actor)
