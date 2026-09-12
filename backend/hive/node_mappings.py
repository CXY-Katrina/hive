"""Versioned node-local aliases, independent of scheduling and container execution."""
from .domain import DomainError, decode, encode, now
from .node_mapping_schemas import MappingsUpdate
from pydantic import ValidationError


class NodeMappings:
    def __init__(self, db):
        self.db = db

    def get(self, node_id):
        if not self.db.one('SELECT id FROM nodes WHERE id=%s AND deleted_at IS NULL', (node_id,)):
            raise DomainError('节点不存在', 404)
        row = self.db.one('SELECT * FROM node_resource_mappings WHERE node_id=%s', (node_id,))
        if row:
            row['entries'] = decode(row['entries'], [])
            return row
        return dict(node_id=node_id, version=0, entries=[], updated_at=None, updated_by=None)

    def replace(self, node_id, actor, entries, version, cursor=None):
        if not actor.admin:
            raise DomainError('只有管理员可以编辑资源映射', 403)
        try:
            body = MappingsUpdate(version=version, entries=entries)
        except ValidationError:
            raise DomainError('资源映射内容无效，请检查名称、路径及重复项', 422) from None
        entries = [item.model_dump() for item in body.entries]
        def write(c):
            c.execute('SELECT id FROM nodes WHERE id=%s AND deleted_at IS NULL FOR UPDATE', (node_id,))
            if not c.fetchone():
                raise DomainError('节点不存在', 404)
            c.execute('SELECT version FROM node_resource_mappings WHERE node_id=%s', (node_id,))
            old = c.fetchone()
            if (old['version'] if old else 0) != version:
                raise DomainError('资源映射已被更新，请重新读取后编辑', 409)
            next_version = (old['version'] if old else 0) + 1
            stamp = now()
            c.execute('INSERT INTO node_resource_mappings (node_id,version,entries,updated_at,updated_by) '
                      'VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE version=VALUES(version),'
                      'entries=VALUES(entries),updated_at=VALUES(updated_at),updated_by=VALUES(updated_by)',
                      (node_id, next_version, encode(entries), stamp, actor.username))
            self.db.audit(c, actor, 'node.mappings.replace', node_id,
                          {'version': next_version, 'entries': entries})
            return dict(node_id=node_id, version=next_version, entries=entries,
                        updated_at=stamp, updated_by=actor.username)
        if cursor is not None:
            return write(cursor)
        with self.db.transaction() as c:
            return write(c)


def register_node_mapping_routes(app, services, current, respond):
    from fastapi import Depends
    def service():
        return getattr(services, 'node_mappings', None) or NodeMappings(services.db)

    @app.get('/api/nodes/{node_id}/mappings')
    def get_mappings(node_id: str, actor=Depends(current)):
        return respond(service().get(node_id))

    @app.put('/api/nodes/{node_id}/mappings')
    def put_mappings(node_id: str, body: MappingsUpdate, actor=Depends(current)):
        return respond(service().replace(node_id, actor, body.entries, body.version))
