"""Persistent orchestration. Business operations live in referenced PR files."""
import hashlib
import copy
from .domain import DomainError, uid, now, encode, decode


FINAL = {'SUCCEEDED', 'FAILED', 'CANCELLED', 'SKIPPED'}


class Workflows:
    def __init__(self, db, resources, inventory, transport, settings, sources):
        self.db, self.resources, self.inventory = db, resources, inventory
        self.transport, self.settings = transport, settings
        self.sources = sources

    def tick_space(self, space_id):
        from .workflow_engine import WorkflowEngine
        # One durable space lease controls card/port assignments across its tasks.
        with self.db.transaction() as c:
            lock = 'hive.space.' + space_id
            c.execute('SELECT GET_LOCK(%s,0) AS acquired', (lock,))
            if c.fetchone()['acquired'] != 1:
                return
            try:
                WorkflowEngine(self).tick(space_id)
            except (DomainError, OSError, TimeoutError) as exc:
                reason = str(exc) if isinstance(exc, DomainError) else '远端通信未确认，保留占用并继续核验'
                with self.db.transaction() as report:
                    report.execute('UPDATE workflow_spaces SET reason=%s WHERE id=%s', (reason, space_id))
                    report.execute("UPDATE workflows SET reason=%s WHERE space_id=%s AND status NOT IN ('SUCCEEDED','FAILED','CANCELLED')", (reason, space_id))
            finally:
                c.execute('SELECT RELEASE_LOCK(%s)', (lock,))

    @staticmethod
    def authorize(row, actor):
        if not row:
            raise DomainError('任务或运行空间不存在', 404)
        if row['owner_user_id'] != actor.id and not actor.admin:
            raise DomainError('只能操作自己的任务和环境', 403)

    def submit(self, actor, spec):
        if hasattr(actor, 'can_request') and not (actor.admin or actor.can_request):
            raise DomainError('尚未获得资源申请权限', 403)
        body_hash = hashlib.sha256(encode(spec).encode()).hexdigest()
        existing = self.db.one('SELECT id,body_hash FROM workflows WHERE owner_user_id=%s AND idempotency_key=%s', (actor.id, spec['idempotency_key']))
        if existing:
            if existing['body_hash'] != body_hash:
                raise DomainError('幂等键已用于其他任务内容')
            return self.get(existing['id'], actor)
        spec = copy.deepcopy(spec)
        if spec.get('preset_id'):
            from .presets import Presets
            Presets(self.db, self.sources, self.settings.workflow_sample_preset_id).get(spec['preset_id'], require_enabled=True)
        if spec.get('space_id'):
            space = self.db.one('SELECT * FROM workflow_spaces WHERE id=%s', (spec['space_id'],))
            self.authorize(space, actor)
            base_spec = decode(space['spec'])
            if space['status'] in {'CLOSING', 'CLOSED', 'FAILED'}:
                raise DomainError('运行空间已经关闭或正在关闭')
            if spec['environments'] and spec['environments'] != base_spec['environments']:
                raise DomainError('复用环境不能修改安装配置；请新建运行空间')
            spec['environments'] = base_spec['environments']
            if any(j['environment'] not in {e['alias'] for e in spec['environments']} for j in spec['jobs']):
                raise DomainError('任务引用了不存在的环境', 422)
            original = base_spec['resource']
            if original['mode'] == 'partial' and any(j['npu_count'] > original['cards_per_node'] for j in spec['jobs']):
                raise DomainError('job 用卡数超过运行空间已申请卡数', 422)
            from .workflow_schemas import WorkflowCreate
            from pydantic import ValidationError
            try:
                WorkflowCreate.model_validate({**spec, 'space_id': None, 'resource': original})
            except ValidationError as exc:
                raise DomainError(exc.errors()[0]['msg'], 422) from None
        resolved = self.sources.resolve(spec['source'].get('pr'))
        if any(spec['source'].get(key) and spec['source'][key] != resolved[key] for key in ('head_sha', 'vllm_sha')):
            raise DomainError('PR 已更新或提交 SHA 不匹配，请重新解析后提交')
        spec['source'] = resolved
        if spec.get('space_id') and any(resolved[k] != base_spec['source'][k] for k in ('head_sha', 'vllm_sha')):
            raise DomainError('安装代码版本不同，请新建环境；相同版本可替换 YAML 复用')
        spec['files'] = {}
        def capture(path, uploaded=None, config=False):
            previous = spec['files'].get(path)
            if previous and (uploaded is None or uploaded == previous['content']):
                return
            file = self.sources.file(resolved, path)
            if uploaded is not None:
                if not config and uploaded != file['content']:
                    raise DomainError('上传执行代码与 PR 不一致，请先提交 PR', 422)
                if config:
                    file = {**file, 'content': uploaded, 'sha256': hashlib.sha256(uploaded.encode()).hexdigest(),
                            'size': len(uploaded.encode()), 'uploaded': True}
            previous = spec['files'].get(path)
            if previous and previous['sha256'] != file['sha256']:
                raise DomainError('同一任务中的文件路径对应不同内容', 422)
            spec['files'][path] = file
            if sum(f['size'] for f in spec['files'].values()) > 2 * 1024 * 1024:
                raise DomainError('任务引用文件总量超过 2 MiB', 422)
        def step_files(step):
            if not step.get('external'):
                capture(step['path'], step.get('uploaded_content'), step['type'] == 'yaml')
            if step.get('runner'):
                capture(step['runner']['path'])
            for item in step.get('inputs', []):
                capture(item['path'], item.get('uploaded_content'), True)
        for env in spec['environments']:
            for step in [env['bootstrap']] + env['install'] + env['verify']:
                step_files(step)
        for job in spec['jobs']:
            for step in job['pre'] + job['steps'] + job['post'] + job['ready']:
                step_files(step)
        with self.db.transaction() as c:
            # Serialize submissions by owner, including reuse and idempotent retries.
            c.execute('SELECT id FROM users WHERE id=%s FOR UPDATE', (actor.id,))
            c.fetchone()
            c.execute('SELECT * FROM workflows WHERE owner_user_id=%s AND idempotency_key=%s',
                      (actor.id, spec['idempotency_key']))
            existing = c.fetchone()
            if existing:
                if existing['body_hash'] != body_hash:
                    raise DomainError('幂等键已用于其他任务内容')
                task_id = existing['id']
            else:
                space_id, task_id = spec.get('space_id') or uid(), uid()
                if spec.get('space_id'):
                    c.execute('SELECT * FROM workflow_spaces WHERE id=%s FOR UPDATE', (space_id,))
                    space = c.fetchone()
                    self.authorize(space, actor)
                    if space['status'] in {'CLOSING', 'CLOSED', 'FAILED'}:
                        raise DomainError('运行空间正在关闭')
                    c.execute('UPDATE workflow_spaces SET retain_until=NULL WHERE id=%s', (space_id,))
                else:
                    request = self.resources.create(actor, spec['resource'], 'workflow:' + space_id, purpose='task', cursor=c)
                    c.execute('INSERT INTO workflow_spaces (id,request_id,owner_user_id,owner_name,status,spec,runtime,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                              (space_id, request['id'], actor.id, actor.username, 'QUEUED', encode(spec), '{}', now()))
                    for env in spec['environments']:
                        c.execute('INSERT INTO workflow_environments (space_id,alias,status,spec,runtime) VALUES (%s,%s,%s,%s,%s)',
                                  (space_id, env['alias'], 'PENDING', encode(env), '{}'))
                c.execute('INSERT INTO workflows (id,space_id,owner_user_id,owner_name,idempotency_key,body_hash,name,spec,status,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                          (task_id, space_id, actor.id, actor.username, spec['idempotency_key'], body_hash, spec['name'], encode(spec), 'QUEUED', now()))
                for job in spec['jobs']:
                    c.execute('INSERT INTO workflow_jobs (workflow_id,id,status,phase,spec,runtime) VALUES (%s,%s,%s,%s,%s,%s)',
                              (task_id, job['id'], 'PENDING', 'pending', encode(job), '{}'))
                self.db.audit(c, actor, 'workflow.submit', task_id, {'space_id': space_id})
        return self.get(task_id, actor)

    def get(self, task_id, actor):
        row = self.db.one('SELECT * FROM workflows WHERE id=%s', (task_id,))
        self.authorize(row, actor)
        row['spec'] = decode(row['spec'])
        jobs = self.db.all('SELECT * FROM workflow_jobs WHERE workflow_id=%s ORDER BY id', (task_id,))
        for job in jobs:
            job['spec'], runtime = decode(job['spec']), decode(job.pop('runtime'))
            job.update(name=job['spec']['name'] or job['id'], logs=runtime.get('logs', []))
            job.update(cards=runtime.get('cards', []), endpoint=runtime.get('endpoint'),
                       display_truncated=runtime.get('display_truncated', False),
                       artifacts=runtime.get('artifacts', []), metrics=runtime.get('metrics', {'metrics': [], 'verdict': 'unknown'}),
                       attempts=[{'phase': phase, 'status': a['status'], 'exit_code': a.get('exit_code'),
                                  'log_truncated': a.get('log_truncated', False)} for phase, a in runtime.get('attempts', {}).items()])
        row['jobs'] = jobs
        return row

    def list(self, actor):
        rows = self.db.all('SELECT id FROM workflows WHERE owner_user_id=%s OR %s ORDER BY created_at DESC LIMIT 500', (actor.id, actor.admin))
        return [self.get(row['id'], actor) for row in rows]

    def spaces(self, actor):
        rows = self.db.all('SELECT * FROM workflow_spaces WHERE owner_user_id=%s OR %s ORDER BY created_at DESC LIMIT 500', (actor.id, actor.admin))
        for row in rows:
            row['spec'] = decode(row['spec'])
            row.pop('runtime', None)
            environments = self.db.all('SELECT * FROM workflow_environments WHERE space_id=%s ORDER BY alias', (row['id'],))
            row['environments'] = []
            for env in environments:
                spec, runtime = decode(env['spec']), decode(env['runtime'])
                node = self.inventory.get(runtime['node_id']) if runtime.get('node_id') else {}
                row['environments'].append({**spec, 'status': env['status'], 'reason': env['reason'],
                                           'container_name': runtime.get('container_name'), 'node_id': runtime.get('node_id'),
                                           'host': node.get('host'), 'logical_ids': runtime.get('logical_ids', []),
                                           'boot_id': runtime.get('boot_id'), 'logs': runtime.get('logs', []),
                                           'requested_packages': spec['packages']})
        return rows

    def cancel(self, task_id, actor):
        with self.db.transaction() as c:
            c.execute('SELECT * FROM workflows WHERE id=%s FOR UPDATE', (task_id,))
            row = c.fetchone()
            self.authorize(row, actor)
            if row['status'] not in FINAL:
                c.execute("UPDATE workflows SET cancel_requested=TRUE,status='CANCELLING' WHERE id=%s", (task_id,))
                self.db.audit(c, actor, 'workflow.cancel', task_id)
        return self.get(task_id, actor)

    def close_space(self, space_id, actor):
        with self.db.transaction() as c:
            c.execute('SELECT * FROM workflow_spaces WHERE id=%s FOR UPDATE', (space_id,))
            row = c.fetchone()
            self.authorize(row, actor)
            if row['status'] != 'CLOSED':
                c.execute("UPDATE workflow_spaces SET status='CLOSING',reason='用户关闭运行空间' WHERE id=%s", (space_id,))
                c.execute('UPDATE workflows SET cancel_requested=TRUE WHERE space_id=%s', (space_id,))
                self.db.audit(c, actor, 'space.close', space_id)
        return {'id': space_id, 'status': 'CLOSING'}

    def log_key(self, task_id, job_id, phase, actor):
        self.get(task_id, actor)
        row = self.db.one('SELECT runtime FROM workflow_jobs WHERE workflow_id=%s AND id=%s', (task_id, job_id))
        item = decode(row['runtime']).get('attempts', {}).get(phase) if row else None
        if not item:
            raise DomainError('执行阶段日志不存在', 404)
        return item['id']


def register_workflow_routes(app, services, current, respond):
    from fastapi import Depends
    from .workflow_schemas import WorkflowCreate

    @app.post('/api/workflows')
    def submit(body: WorkflowCreate, actor=Depends(current)):
        return respond(services.workflows.submit(actor, body.model_dump()), 201)

    @app.get('/api/workflows')
    def listing(actor=Depends(current)):
        return respond(services.workflows.list(actor))

    @app.get('/api/workflows/{task_id}')
    def detail(task_id: str, actor=Depends(current)):
        return respond(services.workflows.get(task_id, actor))

    @app.get('/api/spaces')
    def spaces(actor=Depends(current)):
        return respond(services.workflows.spaces(actor))

    @app.post('/api/workflows/{task_id}/cancel')
    def cancel(task_id: str, actor=Depends(current)):
        return respond(services.workflows.cancel(task_id, actor))

    @app.post('/api/spaces/{space_id}/close')
    def close(space_id: str, actor=Depends(current)):
        return respond(services.workflows.close_space(space_id, actor))

    @app.get('/api/workflows/{task_id}/jobs/{job_id}/logs/{phase}')
    def log(task_id: str, job_id: str, phase: str, actor=Depends(current)):
        from fastapi.responses import StreamingResponse
        from .workflow_logs import LogArchive
        key = services.workflows.log_key(task_id, job_id, phase, actor)
        archive = LogArchive(services.settings.data_dir)
        first = archive.read(key)
        if first['missing']:
            raise DomainError('日志尚未归档', 404)
        def chunks():
            item = first
            while True:
                yield item['data']
                if item['eof']:
                    break
                item = archive.read(key, item['next_offset'])
        return StreamingResponse(chunks(), media_type='text/plain; charset=utf-8',
                                 headers={'Content-Disposition': 'attachment; filename="workflow.log"'})

    @app.get('/api/workflows/{task_id}/jobs/{job_id}/artifacts/{artifact_id}')
    def artifact(task_id: str, job_id: str, artifact_id: str, actor=Depends(current)):
        from fastapi.responses import FileResponse
        from .workflow_artifacts import WorkflowArtifacts
        services.workflows.get(task_id, actor)
        saved = WorkflowArtifacts(None, services.settings.data_dir).read(task_id, job_id, artifact_id)
        return FileResponse(saved['path'], media_type='application/octet-stream', filename='artifact-' + artifact_id[:12])
