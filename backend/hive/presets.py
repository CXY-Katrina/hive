"""Administratively imported, immutable JSON workflow catalogs."""
import json
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
    def __init__(self, db, sources, sample_item_id=None):
        self.db, self.sources = db, sources
        self.sample_item_id = sample_item_id or None

    @staticmethod
    def _decode(row):
        row = dict(row)
        for key in ('tags', 'workflow', 'source'):
            row[key] = decode(row[key], {})
        row['enabled'] = bool(row['enabled'])
        row['path'] = row.pop('catalog_path')
        return row

    def list(self):
        return [self._decode(row) for row in self.db.all('SELECT * FROM workflow_presets ORDER BY name,id')]

    def import_catalog(self, actor, source, path):
        Identity.require_admin(actor)
        if (not isinstance(path,str) or len(path)>512 or not path.lower().endswith('.json')
                or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*',path)
                or any(part in {'.','..'} for part in path.split('/'))):
            raise DomainError('预置导入只接受仓库内的 JSON 清单路径，不解释 YAML 或扫描业务目录',422)
        resolved = self.sources.resolve(source['pr'])
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

    def get(self, preset_id, require_enabled=False):
        row = self.db.one('SELECT * FROM workflow_presets WHERE id=%s', (preset_id,))
        if not row:
            raise DomainError('预置不存在', 404)
        if require_enabled and not row['enabled']:
            raise DomainError('此预置尚未获得管理员的单项启用批准', 409)
        return self._decode(row)

    def enable(self, actor, preset_id):
        Identity.require_admin(actor)
        with self.db.transaction() as c:
            # Serialize approval across the small catalog; never enable a batch.
            c.execute('SELECT * FROM workflow_presets ORDER BY id FOR UPDATE')
            rows = list(c.fetchall())
            selected = next((row for row in rows if row['id'] == preset_id), None)
            if not selected:
                raise DomainError('预置不存在', 404)
            if not self.sample_item_id or selected['item_id'] != self.sample_item_id:
                raise DomainError('当前仅允许配置的样例单项启用；其他预置继续保持未启用', 409)
            if any(row['enabled'] and row['id'] != preset_id for row in rows):
                raise DomainError('已有一个预置获得批准，不能同时开启其他预置', 409)
            if not selected['enabled']:
                c.execute('UPDATE workflow_presets SET enabled=TRUE,approved_by=%s,approved_at=%s WHERE id=%s',
                          (actor.username,now(),preset_id))
                self.db.audit(c,actor,'preset.enable',preset_id,{'item_id':selected['item_id'],'sha256':selected['sha256']})
        return self.get(preset_id)
