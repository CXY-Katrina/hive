"""Local task archives, historical publications and private parameter variants."""
import json
import hashlib
import re
from .domain import DomainError, decode, encode, now, uid
from .identity import Identity


def catalog_items(content):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    def invalid_constant(_):
        raise ValueError()
    try:
        catalog = json.loads(content, object_pairs_hook=unique_object, parse_constant=invalid_constant)
        if not isinstance(catalog, dict) or set(catalog) != {'items'}:
            raise ValueError()
        items = catalog['items']
        if not isinstance(items, list) or len(items) > 500:
            raise ValueError()
        seen = set()
        for item in items:
            if (not isinstance(item, dict) or set(item) != {'id','name','tags','workflow'}
                    or not isinstance(item['id'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', item['id'])
                    or item['id'] in seen or not isinstance(item['name'], str) or not 1 <= len(item['name'].strip()) <= 128
                    or not isinstance(item['workflow'], dict) or not isinstance(item['tags'], dict) or len(item['tags']) > 32
                    or any(not isinstance(key,str) or not 1 <= len(key) <= 64 or not isinstance(value,str) or len(value) > 256
                           for key,value in item['tags'].items())):
                raise ValueError()
            item['name'] = item['name'].strip()
            seen.add(item['id'])
        return items
    except (ValueError, TypeError, RecursionError):
        raise DomainError('清单必须包含 items 数组（最多 500 项），每项仅有唯一 id、name、字符串 tags 和 workflow 对象', 422) from None


class Presets:
    def __init__(self, db, sources, sample_item_id=None, archive_root=None):
        self.db, self.sources = db, sources
        self.sample_item_id = sample_item_id or None
        from .preset_archive import PresetArchive
        self.archive = PresetArchive(archive_root) if archive_root is not None else None

    def _decode(self, row):
        row = dict(row)
        for key in ('tags', 'workflow', 'source'):
            row[key] = decode(row[key], {})
        row['enabled'] = bool(row['enabled'])
        row['path'] = row.pop('catalog_path')
        row['created_by'] = row['imported_by']
        row['loadable'] = bool(row['workflow'])
        row['yaml_path'] = row['tags'].get('nightly_yaml', '')
        if row['path'].startswith('@workflow/'):
            row['source_workflow_id'] = row['path'].split('/', 1)[1]
            run = self.db.one('SELECT status FROM workflows WHERE id=%s', (row['source_workflow_id'],))
            status = run['status'] if run else None
            row['source_workflow_status'] = status
            row['validation_status'] = ({'SUCCEEDED': 'execution_passed', 'FAILED': 'execution_failed',
                                         'CANCELLED': 'cancelled'}.get(status, 'pending_execution') if run else 'unknown')
            if status != 'SUCCEEDED':
                row['enabled'] = False
                row['reason'] = ('来源任务不存在，无法核验执行结果' if not run else
                                 '来源任务状态为 ' + status + '；可载入修改，不代表执行验证通过')
        return row

    def from_workflow(self, actor, workflow_id, item_id, name, tags):
        """Save an immutable definition; execution evidence gates later approval."""
        from .workflow_schemas import WorkflowCreate
        Identity.require_admin(actor)
        run = self.db.one('SELECT * FROM workflows WHERE id=%s', (workflow_id,))
        if not run:
            raise DomainError('任务不存在', 404)
        if run['status'] not in {'QUEUED', 'PREPARING', 'RUNNING', 'SUCCEEDED'}:
            raise DomainError('只能保存排队中、准备中、运行中或执行成功的任务配置', 409)
        spec = decode(run['spec'], {})
        config = {key: value for key, value in spec.items() if key in WorkflowCreate.model_fields
                  and key not in {'idempotency_key', 'preset_id', 'space_id'}}
        if not config.get('resource') or not config.get('environments'):
            space = self.db.one('SELECT spec FROM workflow_spaces WHERE id=%s', (run['space_id'],))
            original = decode(space['spec'], {}) if space else {}
            config['resource'] = original.get('resource')
            config['environments'] = original.get('environments', [])
        if not config.get('resource') or not config.get('environments'):
            raise DomainError('任务缺少可重建的资源或环境定义', 409)
        config['resource']['target_node_ids'] = []
        config['name'] = name.strip()
        item = catalog_items(encode({'items':[{'id':item_id,'name':name,'tags':tags,'workflow':config}]}))[0]
        source = config['source']
        path = '@workflow/' + workflow_id
        digest = hashlib.sha256(encode(item).encode()).hexdigest()
        ident = uid()
        with self.db.transaction() as c:
            c.execute('''INSERT INTO workflow_presets
                (id,item_id,name,tags,workflow,source,source_head,catalog_path,sha256,enabled,imported_by,imported_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s)
                ON DUPLICATE KEY UPDATE id=id''',
                (ident,item_id,item['name'],encode(tags),encode(config),encode(source),source['head_sha'],path,digest,actor.username,now()))
            c.execute('SELECT id,sha256 FROM workflow_presets WHERE source_head=%s AND catalog_path=%s AND item_id=%s',
                      (source['head_sha'],path,item_id))
            saved = c.fetchone()
            if saved['sha256'] != digest:
                raise DomainError('该执行已发布；参数修改请另存新用例', 409)
            ident = saved['id']
            self.db.audit(c,actor,'preset.publish',ident,{'workflow_id':workflow_id,'sha256':digest,'source_status':run['status']})
        return self.get(ident)

    def list(self, actor=None):
        rows = [self._decode(row) for row in self.db.all('SELECT * FROM workflow_presets ORDER BY name,id')]
        archived = self.archive.list() if self.archive else []
        archived_items = {row['item_id'] for row in archived}
        archived_yaml = {row['yaml_path'] for row in archived}
        # Old publications remain addressable for historical tasks and personal variants.
        rows = [row for row in rows if row['item_id'] not in archived_items
                and (not row['yaml_path'] or row['yaml_path'] not in archived_yaml)]
        latest = {}
        for row in rows:
            key = row['yaml_path'] or (row['item_id'] if row['path'].startswith('@workflow/') else row['id'])
            if key not in latest or row['imported_at'] > latest[key]['imported_at']:
                latest[key] = row
        rows = archived + list(latest.values())
        if actor:
            rows.extend(self._variant(row) for row in self.db.all(
                'SELECT * FROM workflow_preset_variants WHERE owner_user_id=%s OR %s ORDER BY created_at,id', (actor.id, actor.admin)))
        return rows

    def _variant(self, row):
        value = dict(row)
        for key in ('workflow', 'source', 'tags'):
            value[key] = decode(value[key], {})
        value.update(scope='personal', validation_status='unverified', enabled=True, loadable=True, created_by=value['owner_name'],
                     reason='参数变体尚未执行验证', imported_by=value['owner_name'], imported_at=value['created_at'])
        if self.archive:
            parent = self.archive.get(value['root_id'])
            if parent:
                value['yaml_path'] = parent['yaml_path']
            def refresh(node):
                if isinstance(node, list):
                    for child in node:
                        refresh(child)
                elif isinstance(node, dict):
                    for file in node.get('files', []):
                        current = self.archive.file(file['name'])
                        if current and current['content'] != file['content']:
                            file['content'] = current['content']
                            value['archive_refreshed'] = True
                    for key, child in node.items():
                        if key != 'files':
                            refresh(child)
            try:
                refresh(value['workflow'])
            except DomainError:
                value.update(loadable=False, reason='此副本引用的归档脚本已移除，请从当前公共预置重新创建副本')
            if value.get('archive_refreshed'):
                value['reason'] = '已载入当前归档脚本，保留个人参数；需要重新执行验证'
                value['loaded_sha256'] = hashlib.sha256(encode(value['workflow']).encode()).hexdigest()
        return value

    def derive(self, actor, preset_id, name, tags, workflow):
        from .workflow_schemas import WorkflowCreate
        from pydantic import ValidationError
        parent = self.get(preset_id, actor=actor)
        name = name.strip()
        if not name or len(name) > 128 or len(tags) > 32 or any(not isinstance(k,str) or not 1 <= len(k) <= 64 or not isinstance(v,str) or len(v)>256 for k,v in tags.items()):
            raise DomainError('用例名称或标签无效', 422)
        if len(encode(workflow).encode()) > 2 * 1024 * 1024:
            raise DomainError('用例配置最多 2 MiB', 422)
        try:
            parsed = WorkflowCreate.model_validate({**workflow, 'name':name, 'idempotency_key':'preset-variant'})
        except ValidationError as exc:
            raise DomainError(exc.errors()[0]['msg'], 422) from None
        if parsed.space_id:
            raise DomainError('新用例需保存机器规格，不绑定已分配运行空间', 422)
        if any(str(parsed.source.get(k)) != str(parent['source'].get(k)) for k in ('pr','head_sha','vllm_sha')):
            raise DomainError('参数变体需保留原预置的代码版本', 422)
        config = parsed.model_dump(exclude={'idempotency_key','preset_id','space_id'})
        config['source'] = parent['source']
        ident, stamp = uid(), now()
        with self.db.transaction() as c:
            c.execute('INSERT INTO workflow_preset_variants (id,parent_id,root_id,owner_user_id,owner_name,name,tags,workflow,source,sha256,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                      (ident,preset_id,parent.get('root_id',preset_id),actor.id,actor.username,name,encode(tags),encode(config),encode(parent['source']),hashlib.sha256(encode(config).encode()).hexdigest(),stamp))
            self.db.audit(c,actor,'preset.derive',ident,{'parent_id':preset_id})
        return self.get(ident, actor=actor)

    def import_catalog(self, actor, source, path):
        Identity.require_admin(actor)
        if (not isinstance(path,str) or len(path)>512 or not path.lower().endswith('.json')
                or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*',path)
                or any(part in {'.','..'} for part in path.split('/'))):
            raise DomainError('预置导入只接受仓库内的 JSON 清单路径，不解释 YAML 或扫描业务目录',422)
        resolved = self.sources.resolve(source['pr'], revision=source.get('revision','head'))
        if any(source[key] != resolved[key] for key in ('head_sha', 'vllm_sha')):
            raise DomainError('PR 来源已变化，请重新解析后导入清单', 409)
        try:
            file = self.sources.file(resolved, path)
        except DomainError as exc:
            if exc.code == 404:
                raise DomainError('指定 PR 的固定提交中不存在预置 JSON 清单：' + path,404) from None
            raise
        items = catalog_items(file['content'])
        ids = []
        with self.db.transaction() as c:
            c.execute('SELECT sha256 FROM workflow_presets WHERE source_head=%s AND catalog_path=%s FOR UPDATE',
                      (resolved['head_sha'],path))
            if any(row['sha256'] != file['sha256'] for row in c.fetchall()):
                raise DomainError('同一固定提交的清单内容发生冲突，保留原预置和批准记录',409)
            for item in items:
                ident = uid()
                c.execute('''INSERT INTO workflow_presets
                    (id,item_id,name,tags,workflow,source,source_head,catalog_path,sha256,enabled,imported_by,imported_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s)
                    ON DUPLICATE KEY UPDATE id=id''',
                    (ident,item['id'],item['name'],encode(item['tags']),encode(item['workflow']),encode(resolved),
                     resolved['head_sha'],path,file['sha256'],actor.username,now()))
                c.execute('SELECT id,sha256 FROM workflow_presets WHERE source_head=%s AND catalog_path=%s AND item_id=%s',
                          (resolved['head_sha'],path,item['id']))
                saved = c.fetchone()
                if saved['sha256'] != file['sha256']:
                    raise DomainError('同一固定提交的清单内容发生冲突，保留原预置和批准记录', 409)
                ids.append(saved['id'])
            self.db.audit(c,actor,'presets.import',file['sha256'],{'path':path,'source':resolved,'items':ids})
        return [row for row in self.list() if row['id'] in ids]

    def get(self, preset_id, require_enabled=False, actor=None):
        archived = self.archive.get(preset_id) if self.archive else None
        if archived:
            return archived
        row = self.db.one('SELECT * FROM workflow_presets WHERE id=%s', (preset_id,))
        if not row:
            variant = self.db.one('SELECT * FROM workflow_preset_variants WHERE id=%s', (preset_id,))
            if not variant:
                raise DomainError('预置不存在', 404)
            if actor is None or not (actor.admin or variant['owner_user_id'] == actor.id):
                raise DomainError('只能访问自己的参数变体', 403)
            if require_enabled:
                self.get(variant['root_id'], require_enabled=True, actor=actor)
            return self._variant(variant)
        decoded = self._decode(row)
        if require_enabled and not decoded['enabled']:
            raise DomainError('此预置尚未获得管理员的单项启用批准', 409)
        return decoded

    def enable(self, actor, preset_id):
        Identity.require_admin(actor)
        with self.db.transaction() as c:
            # Serialize approval across the small catalog; never enable a batch.
            c.execute('SELECT * FROM workflow_presets ORDER BY id FOR UPDATE')
            rows = list(c.fetchall())
            selected = next((row for row in rows if row['id'] == preset_id), None)
            if not selected:
                raise DomainError('预置不存在', 404)
            if selected['catalog_path'].startswith('@workflow/'):
                source_id = selected['catalog_path'].split('/', 1)[1]
                c.execute('SELECT status FROM workflows WHERE id=%s FOR UPDATE', (source_id,))
                run = c.fetchone()
                if not run or run['status'] != 'SUCCEEDED':
                    raise DomainError('来源任务尚未执行成功，预置草案不能启用', 409)
            if not self.sample_item_id or selected['item_id'] != self.sample_item_id:
                raise DomainError('当前仅允许配置的样例单项启用；其他预置继续保持未启用', 409)
            if any(row['enabled'] and row['id'] != preset_id for row in rows):
                raise DomainError('已有一个预置获得批准，不能同时开启其他预置', 409)
            if not selected['enabled']:
                c.execute('UPDATE workflow_presets SET enabled=TRUE,approved_by=%s,approved_at=%s WHERE id=%s',
                          (actor.username,now(),preset_id))
                self.db.audit(c,actor,'preset.enable',preset_id,{'item_id':selected['item_id'],'sha256':selected['sha256']})
        return self.get(preset_id)
