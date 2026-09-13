"""Generic workflow reconciliation; executable business content is always external."""
import base64
from datetime import timedelta
import hashlib
import json
import re
import shlex
from pathlib import PurePosixPath

from .container_runtime import ContainerRuntime
from .workflow_logs import LogArchive
from .workflow_artifacts import WorkflowArtifacts, artifact_key
from .domain import DomainError, SYSTEM, now, encode, decode

FINAL = {'SUCCEEDED', 'FAILED', 'CANCELLED', 'SKIPPED'}


def guard_path(path):
    parts = PurePosixPath(path).parts
    if not path.startswith('/') or '..' in parts or '\x00' in path:
        raise DomainError('运行文件路径无效', 422)
    return ''.join('test ! -L ' + shlex.quote(str(PurePosixPath(*parts[:i]))) + '\n' for i in range(2, len(parts) + 1))


def materialize_files(root, files):
    result = ''
    for path, file in files.items():
        target = root + '/' + path
        result += guard_path(target)
        if file.get('uploaded'):
            result += 'mkdir -p -- ' + shlex.quote(str(PurePosixPath(target).parent)) + '\n'
            result += 'printf %s ' + shlex.quote(base64.b64encode(file['content'].encode()).decode()) + ' | base64 --decode > ' + shlex.quote(target) + '\n'
        result += 'test "$(sha256sum -- ' + shlex.quote(target) + " | cut -d ' ' -f 1)\" = " + shlex.quote(file['sha256']) + '\n'
    return result


def render_steps(steps, env, context):
    def expand(value):
        def replace(match):
            key = match[1]
            if key not in context:
                raise DomainError('未定义的任务参数: ' + key, 422)
            return str(context[key])
        return re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}', replace, value)
    lines = ['set -euo pipefail']
    for step in steps:
        root = context['source_dir']
        if step.get('launch') or step.get('files'):
            command = step.get('launch', '').strip()
            exports = []
            launch_context = dict(context)
            if step.get('files'):
                launch_context['input'] = root + '/' + step['files'][0]['name']
            if command:
                def launch_parameter(match):
                    key = match[1]
                    if key not in launch_context:
                        if '.' in key:
                            raise DomainError('未定义的任务参数: ' + key, 422)
                        return match[0]  # Ordinary shell variables belong to the script.
                    name = 'HIVE_LAUNCH_PARAM_' + str(len(exports))
                    exports.append('export ' + name + '=' + shlex.quote(str(launch_context[key])))
                    return '${' + name + '}'
                command = re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}', launch_parameter, command)
            else:
                commands = []
                for file in step['files']:
                    interpreter = env['python'] if file['name'].lower().endswith('.py') else env['shell']
                    commands.append(shlex.join([interpreter, root + '/' + file['name']]))
                command = '\n'.join(commands)
            lines.append('(\ncd -- ' + shlex.quote(root) + '\n' + '\n'.join(exports) + '\n' + command + '\n)')
            continue
        entry = step
        values = dict(context)
        values['input'] = root + '/' + (step['inputs'][0]['path'] if step.get('inputs') and step['type'] != 'yaml' else step['path'])
        if step['type'] == 'yaml':
            entry = step['runner']
        old_input = context.get('input')
        context['input'] = values['input']
        interpreter = env['shell'] if entry['type'] == 'shell' else env['python']
        path = entry['path'] if entry.get('external') else root + '/' + entry['path']
        args = entry.get('args', []) + (step.get('args', []) if step['type'] == 'yaml' else [])
        if step['type'] == 'yaml' and not any('${input}' in a for a in args):
            raise DomainError('YAML 执行器参数需要 ${input}', 422)
        lines.append(shlex.join([interpreter, path] + [expand(a) for a in args]))
        if old_input is None:
            context.pop('input', None)
        else:
            context['input'] = old_input
    return '\n'.join(lines) + '\n'



def resource_helpers(mappings):
    lines = ['hive_resource() {', 'case "$1:$2" in']
    for kind, items in mappings.items():
        for name, target in items.items():
            lines.append(shlex.quote(kind + ':' + name) + ') printf "%s\\n" ' + shlex.quote(target) + ' ;;')
    lines += ['*) printf "%s\\n" "资源映射不存在: $1/$2" >&2; return 2 ;;', 'esac', '}', 'export -f hive_resource']
    return '\n'.join(lines) + '\n'


class WorkflowEngine:
    def __init__(self, service):
        self.s = service
        self.db = service.db
        self.runtime = ContainerRuntime(service.transport)
        self.logs = LogArchive(service.settings.data_dir)

    def save_env(self, row):
        with self.db.transaction() as c:
            c.execute('UPDATE workflow_environments SET status=%s,runtime=%s,reason=%s WHERE space_id=%s AND alias=%s',
                      (row['status'], encode(row['runtime']), row.get('reason'), row['space_id'], row['alias']))

    def save_job(self, row):
        with self.db.transaction() as c:
            c.execute('UPDATE workflow_jobs SET status=%s,phase=%s,runtime=%s,reason=%s,started_at=%s,ended_at=%s WHERE workflow_id=%s AND id=%s',
                      (row['status'], row['phase'], encode(row['runtime']), row.get('reason'), row.get('started_at'), row.get('ended_at'), row['workflow_id'], row['id']))

    def space_status(self, space, status, reason=None):
        space['status'] = status
        with self.db.transaction() as c:
            if status not in {'CLOSING', 'CLOSED'}:
                c.execute("UPDATE workflow_spaces SET status=%s,runtime=%s,reason=%s WHERE id=%s AND status!='CLOSING'", (status, encode(space['runtime']), reason, space['id']))
            else:
                c.execute('UPDATE workflow_spaces SET status=%s,runtime=%s,reason=%s WHERE id=%s', (status, encode(space['runtime']), reason, space['id']))

    def attempt(self, row, phase, script, env, variables, save, timeout=3600):
        state = row['runtime']
        phases = state.setdefault('attempts', {})
        item = phases.get(phase)
        node = self.s.inventory.connection(env['runtime']['node_id'])
        identity = env['runtime']['identity']
        if item is None:
            identifier = hashlib.sha256((row.get('workflow_id', row['space_id'] if 'space_id' in row else '') + ':' + row.get('id', row.get('alias', '')) + ':' + phase).encode()).hexdigest()
            item = {'id': identifier, 'status': 'PREPARING', 'offset': 0}
            phases[phase] = item
            save(row)
        if item['status'] == 'CLOSED':
            return item.get('exit_code', 1)
        if item['status'] == 'PREPARING':
            workdir = '/tmp' if phase == 'checkout' else env['spec']['workdir']
            item['attempt'] = self.runtime.prepare(node, identity, item['id'], '# HIVE_PHASE ' + phase + '\n' + resource_helpers(env['runtime'].get('resource_mappings', {})) + script,
                                                   variables, timeout, workdir)
            item['status'] = 'PREPARED'
            save(row)
        if item['status'] == 'PREPARED':
            item['status'] = 'STARTING'
            save(row)  # Persist before launch, never replay an uncertain launch.
            self.runtime.launch(node, item['attempt'])
            return None
        result = self.runtime.status(node, item['attempt'])
        chunk = self.runtime.log(node, item['attempt'], item['offset'])
        if chunk.get('missing'):
            if result['status'] == 'EXITED':
                raise DomainError('退出后日志文件缺失，结果未完整归档', 503)
            return None
        archived = self.logs.append(item['id'], item['offset'], chunk['data'])
        if chunk['data']:
            logs = state.setdefault('logs', [])
            logs.append({'phase': phase, 'text': chunk['data'].decode('utf-8', 'replace'), 'offset': item['offset']})
            item['offset'] = archived['next_offset']
            if len(logs) > 64:
                state['display_truncated'] = True
            state['logs'] = logs[-64:]
        item['log_truncated'] = chunk['truncated']
        if result['status'] == 'EXITED':
            item['exit_code'] = result['exit_code']
            if not chunk['eof']:
                save(row)
                return None
            if self.runtime.close(node, item['attempt'])['status'] != 'CLOSED':
                raise DomainError('容器进程关闭尚未确认，保留占用', 503)
            item['status'] = 'CLOSED'
            save(row)
            return item['exit_code']
        if result['status'] in {'UNKNOWN', 'PREPARED', 'CLOSED'}:
            raise DomainError('启动或退出结果未知，保留占用并等待核验', 503)
        item['status'] = result['status']
        save(row)
        return None

    def context(self, space, env, source_dir):
        state = env['runtime']
        node = self.s.inventory.get(state['node_id'])
        result = {'space_id': space['id'], 'node_alias': env['spec']['node_alias'], 'host': node['host'],
                'container_name': state['container_name'], 'image': state.get('image_id', state.get('resolved_image', env['spec']['image'])),
                'source_dir': source_dir, 'vllm_sha': space['spec']['source']['vllm_sha'],
                'ascend_sha': space['spec']['source']['head_sha']}
        for alias, binding in space['runtime'].get('bindings', {}).items():
            result[alias + '.ip'] = binding['host']
            result[alias + '.host'] = binding['host']
        result['nodes'] = [{'node_alias': alias, 'host': binding['host'], 'ip': binding['host']}
                           for alias, binding in sorted(space['runtime'].get('bindings', {}).items(),
                                                        key=lambda item: int(item[0][4:]))]
        result['resource_mappings'] = state.get('resource_mappings', {})
        return result

    def variables(self, env, context, cards=()):
        values = {**env['spec']['environment'], 'HIVE_SOURCE_DIR': context['source_dir'],
                  'HIVE_CONTEXT_JSON': encode(context), 'HIVE_PACKAGES_JSON': encode(env['spec']['packages']),
                  'ASCEND_RT_VISIBLE_DEVICES': ','.join(cards),
                  'HIVE_RESOURCE_MAP_JSON': encode(env['runtime'].get('resource_mappings', {})),
                  'HIVE_HOST_IP': context['host'], 'HIVE_CONTAINER_NAME': context['container_name'],
                  'HIVE_NODES_JSON': encode(context['nodes'])}
        for key, value in context.items():
            if re.fullmatch(r'node[0-9]+\.ip', key):
                values['HIVE_' + key.replace('.', '_').upper()] = value
        return values

    def prepare_environment(self, space, env):
        state, config = env['runtime'], env['spec']
        node = self.s.inventory.connection(state['node_id'])
        image = state.get('resolved_image', config['image'])
        proof = '/var/tmp/hive/bootstrap/' + space['id'] + '/' + env['alias'] + '/created-container-id'
        context = self.context(space, env, '/var/tmp/hive/sources/' + space['id'] + '/' + env['alias'])
        if not state.get('bootstrap_intent'):
            bootstrap = config['bootstrap']
            if bootstrap['external']:
                inspect = '# HIVE_BOOTSTRAP_INSPECT\nset -eu\ndocker image inspect --format ' + shlex.quote('{{.Id}}') + ' -- ' + shlex.quote(image) + '\nsha256sum -- ' + shlex.quote(bootstrap['path']) + " | cut -d ' ' -f 1\n"
            else:
                inspect = '# HIVE_BOOTSTRAP_INSPECT\nset -eu\ndocker image inspect --format ' + shlex.quote('{{.Id}}') + ' -- ' + shlex.quote(image) + '\nprintf "%s\\n" ' + shlex.quote(hashlib.sha256(encode(bootstrap).encode()).hexdigest() if bootstrap.get('launch') or bootstrap.get('files') else space['spec']['files'][bootstrap['path']]['sha256'])
            result = self.s.transport.run(node, inspect)
            fields = result.stdout.split()
            if result.code or len(fields) != 2 or not re.fullmatch('sha256:[0-9a-f]{64}', fields[0]) or not re.fullmatch('[0-9a-f]{64}', fields[1]):
                raise DomainError('启动脚本或本地镜像无法核验', 503)
            state.update(image_id=fields[0], bootstrap_sha=fields[1], bootstrap_intent=True,
                         bootstrap_deadline=(now() + timedelta(seconds=75)).isoformat())
            env['status'] = 'BOOTSTRAPPING'
            self.save_env(env)
            context['image'] = state['image_id']
            script = '# HIVE_BOOTSTRAP_RUN\n# container_name ' + state['container_name'] + '\nset -euo pipefail\n'
            script += 'umask 077\nmkdir -p -- ' + shlex.quote(proof.rsplit('/', 1)[0]) + '\ntest ! -e ' + shlex.quote(proof) + '\n'
            script += '! docker inspect --type container -- ' + shlex.quote(state['container_name']) + ' >/dev/null 2>&1\n'
            if bootstrap['external']:
                script += 'test "$(sha256sum -- ' + shlex.quote(bootstrap['path']) + " | cut -d ' ' -f 1)\" = " + shlex.quote(state['bootstrap_sha']) + '\n'
            else:
                directory = '/var/tmp/hive/bootstrap/' + space['id'] + '/' + env['alias']
                script += 'mkdir -p -- ' + shlex.quote(directory) + '\n'
                for path, file in space['spec']['files'].items():
                    destination = directory + '/' + path
                    parent = destination.rsplit('/', 1)[0]
                    script += guard_path(destination) + 'mkdir -p -- ' + shlex.quote(parent) + '\nprintf %s ' + shlex.quote(base64.b64encode(file['content'].encode()).decode()) + ' | base64 --decode > ' + shlex.quote(destination) + '\n'
                context = {**context, 'source_dir': directory}
            command = ''.join('export ' + key + '=' + shlex.quote(value) + '\n' for key, value in self.variables(env, context).items()) + resource_helpers(state.get('resource_mappings', {})) + render_steps([bootstrap], {**config, 'shell': '/bin/bash', 'python': 'python3'}, context)
            script += 'timeout --signal=TERM --kill-after=5 45 bash -c ' + shlex.quote(command) + '\n'
            script += 'docker inspect --type container --format ' + shlex.quote('{{.Id}}') + ' -- ' + shlex.quote(state['container_name']) + ' > ' + shlex.quote(proof + '.tmp') + '\nmv -- ' + shlex.quote(proof + '.tmp') + ' ' + shlex.quote(proof) + '\n'
            result = self.s.transport.run(node, script, timeout=60)
            if result.code:
                env['reason'] = '容器创建命令失败；核对持久容器身份后恢复'
                self.save_env(env)
        if not state.get('identity'):
            evidence = self.s.transport.run(node, '# HIVE_BOOTSTRAP_PROOF\n# container_name ' + state['container_name'] + '\nset -eu\ntest ! -L ' + shlex.quote(proof) + '\ncat -- ' + shlex.quote(proof))
            if evidence.code or not re.fullmatch(r'[0-9a-f]{64}', evidence.stdout.strip()):
                if state.get('bootstrap_deadline', '9999') <= now().isoformat() and self.bootstrap_absent(node, state):
                    state['bootstrap_absent'] = True
                    env.update(status='FAILED', reason='启动时限已过，已确认没有创建容器')
                    self.save_env(env)
                    return
                raise DomainError('容器创建证据缺失，禁止接管同名容器；保留资源待核验', 503)
            identity = self.runtime.inspect(node, state['container_name'])
            if identity['container_id'] != evidence.stdout.strip() or identity['image_id'] != state['image_id'] or identity['host_boot_id'] != state['boot_id']:
                raise DomainError('创建的容器镜像或节点身份不匹配', 503)
            state['identity'] = identity
            self.save_env(env)
        context = self.context(space, env, '/var/tmp/hive/sources/' + space['id'] + '/' + env['alias'])
        variables = self.variables(env, context)
        source = space['spec']['source']
        directory = context['source_dir']
        checkout = ('set -euo pipefail\n' + guard_path(directory) + guard_path(config['workdir']) + 'mkdir -p -- ' + shlex.quote(config['workdir']) + '\nmkdir -p -- ' + shlex.quote(directory) + '\n'
                    'git -C ' + shlex.quote(directory) + ' init\n'
                    'git -C ' + shlex.quote(directory) + ' fetch --depth 1 https://github.com/vllm-project/vllm-ascend.git ' + source['head_sha'] + '\n'
                    'git -C ' + shlex.quote(directory) + ' checkout --detach FETCH_HEAD\n'
                    'test "$(git -C ' + shlex.quote(directory) + ' rev-parse HEAD)" = ' + source['head_sha'] + '\n')
        checkout += materialize_files(directory, space['spec']['files'])
        for phase, script in [('checkout', checkout), ('install', render_steps(config['install'], config, context)), ('verify', render_steps(config['verify'], config, context))]:
            env['status'] = phase.upper()
            code = self.attempt(env, phase, script, env, variables, self.save_env)
            if code is None:
                return
            if code:
                env.update(status='FAILED', reason=phase + ' 退出码 ' + str(code))
                self.save_env(env)
                return
        env.update(status='READY', reason=None)
        self.save_env(env)

    def bootstrap_absent(self, node, state):
        command = 'set -eu\ntest "$(cat /proc/sys/kernel/random/boot_id)" = ' + shlex.quote(state['boot_id']) + '\n'
        command += 'docker ps -a --no-trunc --filter ' + shlex.quote('name=^/' + state['container_name'] + '$') + ' --format ' + shlex.quote('{{.ID}}') + '\n'
        result = self.s.transport.run(node, command)
        return result.code == 0 and not result.stdout.strip()

    def run_job(self, space, task, job, env, jobs):
        config, state = job['spec'], job['runtime']
        root = '/var/tmp/hive/tasks/' + task['id'] + '/' + job['id'] + '/source'
        context = self.context(space, env, root)
        context.update(task_id=task['id'], job_id=job['id'])
        context.update(port=config['ports'][0] if config['ports'] else '',
                       endpoint='http://' + context['host'] + ':' + str(config['ports'][0]) if config['ports'] else '')
        for other in jobs.values():
            endpoint = other['runtime'].get('endpoint')
            if endpoint:
                context[other['id'] + '.endpoint'] = endpoint
                context[other['id'] + '.host'] = other['runtime']['host']
                context[other['id'] + '.port'] = other['spec']['ports'][0]
        if config['ports']:
            context.update({job['id'] + '.endpoint': context['endpoint'], job['id'] + '.host': context['host'], job['id'] + '.port': context['port']})
        for alias, instance in config.get('endpoint_sources', {}).items():
            for field in ('endpoint', 'host', 'port'):
                if instance + '.' + field in context:
                    context[alias + '.' + field] = context[instance + '.' + field]
        variables = self.variables(env, context, state.get('cards', []))
        variables['HIVE_TASK_ID'] = task['id']
        if job['status'] == 'PENDING':
            latest = self.db.one('SELECT cancel_requested FROM workflows WHERE id=%s', (task['id'],))
            if latest['cancel_requested']:
                return
            if config['npu_count'] > len(env['runtime']['logical_ids']):
                job.update(status='FAILED', reason='job 用卡数超过实际分配卡数', ended_at=now())
                self.save_job(job)
                return
            deps = [(jobs[d['job_id']]['status'], d['condition']) for d in config['depends_on']]
            if any(status in {'FAILED', 'CANCELLED', 'SKIPPED'} for status, _ in deps):
                job.update(status='SKIPPED', reason='前置 job 未成功', ended_at=now())
                self.save_job(job)
                return
            if any(status != ('READY' if condition == 'ready' else 'SUCCEEDED') for status, condition in deps):
                return
            if not self.claim(space, task, job, env):
                return
            job.update(status='RUNNING', started_at=now())
            state['host'] = context['host']
            if config['ports']:
                state['endpoint'] = 'http://' + context['host'] + ':' + str(config['ports'][0])
            variables['ASCEND_RT_VISIBLE_DEVICES'] = ','.join(state['cards'])
            self.save_job(job)
        base = '/var/tmp/hive/sources/' + space['id'] + '/' + env['alias']
        setup = ('set -euo pipefail\n' + guard_path(root) + 'if test ! -d ' + shlex.quote(root) + '; then\n'
                 'mkdir -p -- ' + shlex.quote(root.rsplit('/', 1)[0]) + '\nflock ' + shlex.quote(base + '/.hive-worktree.lock') + ' git -C ' + shlex.quote(base)
                 + ' worktree add --detach ' + shlex.quote(root) + ' ' + task['spec']['source']['head_sha'] + '\nfi\n')
        setup += 'test "$(git -C ' + shlex.quote(root) + ' rev-parse HEAD)" = ' + task['spec']['source']['head_sha'] + '\n'
        setup += 'test "$(git -C ' + shlex.quote(root) + ' rev-parse --show-toplevel)" = ' + shlex.quote(root) + '\n'
        setup += materialize_files(root, task['spec']['files'])
        for phase, script in [('input', setup), ('pre', render_steps(config['pre'], env['spec'], context)),
                              ('steps', ('# HIVE_SERVICE\n' if config['kind'] == 'service' else '') + render_steps(config['steps'], env['spec'], context)),
                              ('post', render_steps(config['post'], env['spec'], context))]:
            if state.get('failure') and (phase != 'post' or config['post_policy'] != 'always'):
                continue
            job['phase'] = phase
            code = self.attempt(job, phase, script, env, variables, self.save_job, config['timeout_seconds'])
            if code is None:
                if phase == 'steps' and config['kind'] == 'service':
                    descendants = set()
                    pending = {job['id']}
                    while pending:
                        children = {j['id'] for j in jobs.values() if any(d['job_id'] in pending for d in j['spec']['depends_on'])} - descendants
                        descendants.update(children)
                        pending = children
                    consumers = [jobs[key] for key in descendants]
                    if state.get('service_ready') and all(j['status'] in FINAL for j in consumers):
                        item = state['attempts']['steps']
                        if self.runtime.close(self.s.inventory.connection(env['runtime']['node_id']), item['attempt'])['status'] != 'CLOSED':
                            raise DomainError('服务停止未确认，保留占用', 503)
                        item.update(status='CLOSED', exit_code=0)
                        state['service_stopped'] = True
                        self.save_job(job)
                        return
                    ready = self.attempt(job, 'ready', render_steps(config['ready'], env['spec'], context), env, variables, self.save_job, config['timeout_seconds'])
                    if ready is not None:
                        if ready:
                            state['failure'] = '服务就绪检查失败，退出码 ' + str(ready)
                            if self.close_attempts(job, env, self.save_job):
                                job.update(status='RUNNING', reason=state['failure'])
                                self.save_job(job)
                        else:
                            state['service_ready'] = True
                            job.update(status='READY', phase='service')
                            self.save_job(job)
                return
            if phase == 'steps' and config['kind'] == 'service' and not state.get('service_stopped'):
                state.setdefault('failure', '服务在任务收尾前退出，退出码 ' + str(code))
                self.save_job(job)
                continue
            if code:
                state.setdefault('failure', phase + ' 退出码 ' + str(code))
                self.save_job(job)
        if config.get('artifacts') and not state.get('artifacts_collected'):
            archive = WorkflowArtifacts(self.runtime, self.s.settings.data_dir)
            try:
                targets = {}
                for target in self.db.all('SELECT alias,runtime FROM workflow_environments WHERE space_id=%s', (space['id'],)):
                    binding = decode(target['runtime'])
                    if binding.get('identity'):
                        targets[target['alias']] = (self.s.inventory.connection(binding['node_id']), binding['identity'])
                collected = archive.collect(self.s.inventory.connection(env['runtime']['node_id']), env['runtime']['identity'], task['id'], job['id'], config['artifacts'], targets=targets)
                state['artifacts'] = collected['artifacts']
                documents = collected['metrics']
                metrics = [metric for document in documents for metric in document['metrics']]
                failed = any(d['verdict'] == 'failed' for d in documents) or any(m.get('verdict') == 'failed' for m in metrics)
                verdict = 'failed' if failed else 'passed' if documents and all(d['verdict'] == 'passed' for d in documents) else 'unknown'
                state['metrics'] = {'metrics': metrics, 'verdict': verdict}
                if failed:
                    state.setdefault('failure', '外部结果判定失败')
                state['artifacts_collected'] = True
            except DomainError as exc:
                if exc.code != 422:
                    raise
                state.setdefault('failure', str(exc))
                state['artifacts'] = []
                for artifact in config['artifacts']:
                    try:
                        saved = archive.read(task['id'], job['id'], artifact_key(artifact))
                        state['artifacts'].append(saved['metadata'])
                    except DomainError:
                        pass
                state['artifacts_collected'] = True
            for artifact in state.get('artifacts', []):
                artifact['download_url'] = '/api/workflows/' + task['id'] + '/jobs/' + job['id'] + '/artifacts/' + artifact['id']
        job.update(status='FAILED' if state.get('failure') else 'SUCCEEDED', ended_at=now(), reason=state.get('failure'))
        self.save_job(job)
        self.unclaim(job)

    def claim(self, space, task, job, env):
        node_id = env['runtime']['node_id']
        with self.db.transaction() as c:
            c.execute('SELECT id FROM nodes WHERE id=%s FOR UPDATE', (node_id,))
            c.fetchone()
            c.execute('SELECT * FROM workflow_claims WHERE node_id=%s', (node_id,))
            claims = list(c.fetchall())
            own = [r for r in claims if r['workflow_id'] == task['id'] and r['job_id'] == job['id']]
            if own:
                job['runtime']['cards'] = [r['resource_key'] for r in own if r['kind'] == 'card']
                return True
            if len({(r['workflow_id'], r['job_id']) for r in claims}) >= 4:
                return False
            busy = {r['resource_key'] for r in claims if r['kind'] == 'card'}
            available = [card for card in env['runtime']['logical_ids'] if card not in busy]
            ports = job['spec']['ports']
            if len(available) < job['spec']['npu_count'] or any(r['kind'] == 'port' and int(r['resource_key']) in ports for r in claims):
                return False
            cards = available[:job['spec']['npu_count']]
            for kind, key in [('card', card) for card in cards] + [('port', str(p)) for p in ports] + [('slot', task['id'] + ':' + job['id'])]:
                # A slot also accounts for zero-card clients.
                if kind == 'slot':
                    key = hashlib.sha256(key.encode()).hexdigest()
                c.execute('INSERT INTO workflow_claims VALUES (%s,%s,%s,%s,%s,%s)', (node_id, kind, key, space['id'], task['id'], job['id']))
            job['runtime']['cards'] = cards
        return True

    def unclaim(self, job):
        with self.db.transaction() as c:
            c.execute('DELETE FROM workflow_claims WHERE workflow_id=%s AND job_id=%s', (job['workflow_id'], job['id']))

    def close_attempts(self, row, env, save, skip_post=False):
        node = self.s.inventory.connection(env['runtime']['node_id'])
        for phase, item in row['runtime'].get('attempts', {}).items():
            if skip_post and phase == 'post':
                continue
            if item['status'] == 'CLOSED' or not item.get('attempt'):
                continue
            if self.runtime.close(node, item['attempt'])['status'] != 'CLOSED':
                row['reason'] = '关闭结果未知，保留资源并继续核验'
                save(row)
                return False
            chunk = self.runtime.log(node, item['attempt'], item.get('offset', 0))
            if not chunk.get('missing'):
                archived = self.logs.append(item['id'], item.get('offset', 0), chunk['data'])
                if chunk['data']:
                    logs = row['runtime'].setdefault('logs', [])
                    logs.append({'phase': phase, 'text': chunk['data'].decode('utf-8', 'replace'), 'offset': item.get('offset', 0)})
                    if len(logs) > 64:
                        row['runtime']['display_truncated'] = True
                    row['runtime']['logs'] = logs[-64:]
                item['offset'] = archived['next_offset']
                item['log_truncated'] = chunk['truncated']
                if not chunk['eof']:
                    save(row)
                    return False
            else:
                item['log_missing_on_cancel'] = True
            item['status'] = 'CLOSED'
            item.setdefault('exit_code', 143)
            save(row)
        return True

    def close_space(self, space, envs):
        with self.db.transaction() as c:
            c.execute('SELECT status FROM workflow_spaces WHERE id=%s FOR UPDATE', (space['id'],))
            current = c.fetchone()
            if current['status'] != 'CLOSING':
                c.execute("SELECT id FROM workflows WHERE space_id=%s AND status NOT IN ('SUCCEEDED','FAILED','CANCELLED') LIMIT 1", (space['id'],))
                if c.fetchone():
                    return  # A new task joined after this reconciliation took its snapshot.
                c.execute("UPDATE workflow_spaces SET status='CLOSING' WHERE id=%s", (space['id'],))
        for env in envs:
            state = env['runtime']
            if env['status'] == 'STOPPED':
                continue
            if state.get('bootstrap_intent') and not state.get('identity') and not state.get('bootstrap_absent'):
                if state.get('bootstrap_deadline', '9999') <= now().isoformat() and self.bootstrap_absent(self.s.inventory.connection(state['node_id']), state):
                    state['bootstrap_absent'] = True
                else:
                    raise DomainError('创建结果未知，核实容器身份后才能归还；时限后确认容器不存在可自动恢复', 503)
            if state.get('identity'):
                if not self.close_attempts(env, env, self.save_env):
                    return
                self.runtime.stop(self.s.inventory.connection(state['node_id']), state['identity'])
            env['status'] = 'STOPPED'
            self.save_env(env)
        self.s.resources.release(space['request_id'], SYSTEM)
        self.space_status(space, 'CLOSED')

    def tick(self, space_id):
        space = self.db.one('SELECT * FROM workflow_spaces WHERE id=%s', (space_id,))
        if not space or space['status'] == 'CLOSED':
            return
        space['spec'], space['runtime'] = decode(space['spec']), decode(space['runtime'])
        request = self.s.resources.get(space['request_id'])
        envs = self.db.all('SELECT * FROM workflow_environments WHERE space_id=%s ORDER BY alias', (space_id,))
        tasks = self.db.all('SELECT * FROM workflows WHERE space_id=%s ORDER BY created_at,id', (space_id,))
        for env in envs:
            env['spec'], env['runtime'] = decode(env['spec']), decode(env['runtime'])
        for task in tasks:
            if task['cancel_requested'] and task['status'] not in FINAL:
                running = self.db.one("SELECT id FROM workflow_jobs WHERE workflow_id=%s AND status NOT IN ('PENDING','CANCELLED') LIMIT 1", (task['id'],))
                if not running:
                    with self.db.transaction() as c:
                        c.execute("UPDATE workflow_jobs SET status='CANCELLED',ended_at=%s WHERE workflow_id=%s", (now(), task['id']))
                        c.execute("UPDATE workflows SET status='CANCELLED',ended_at=%s WHERE id=%s", (now(), task['id']))
                    task['status'] = 'CANCELLED'
        closing = space['status'] == 'CLOSING' or request['status'] in {'RELEASING', 'RELEASED', 'CANCELLED', 'FAILED'}
        if all(t['status'] in FINAL for t in tasks) and any(e['status'] not in {'READY', 'STOPPED'} for e in envs):
            closing = True
            self.space_status(space, 'CLOSING')
        if request['status'] in {'QUEUED', 'RESERVED', 'CANCELLED', 'FAILED'}:
            for task in tasks:
                if task['cancel_requested'] or closing:
                    with self.db.transaction() as c:
                        c.execute("UPDATE workflow_jobs SET status='CANCELLED',ended_at=%s WHERE workflow_id=%s", (now(), task['id']))
                        c.execute("UPDATE workflows SET status='CANCELLED',ended_at=%s WHERE id=%s", (now(), task['id']))
                    task['status'] = 'CANCELLED'
            if all(t['status'] in FINAL for t in tasks):
                self.close_space(space, envs)
            return
        if request['status'] != 'ACTIVE' and not closing:
            return
        devices = self.s.resources.devices(space['request_id'])
        node_ids = sorted({d['node_id'] for d in devices})
        if not space['runtime'].get('bindings') and not closing:
            space['runtime']['bindings'] = {'node' + str(i): {'node_id': node_id, 'host': self.s.inventory.get(node_id)['host']} for i, node_id in enumerate(node_ids)}
            self.space_status(space, space['status'])
        if not closing:
            for binding in space['runtime'].get('bindings', {}).values():
                if 'resource_mappings' not in binding:
                    snapshot = self.s.node_mappings.get(binding['node_id'])
                    mapping = {kind: {} for kind in ('model', 'dataset', 'image', 'package')}
                    for item in snapshot['entries']:
                        mapping[item['kind']][item['name']] = item['target']
                    binding.update(resource_mappings=mapping, mappings_version=snapshot['version'])
                    self.space_status(space, space['status'])
        for env in envs:
            if not env['runtime']:
                if closing:
                    continue
                index = int(env['spec']['node_alias'][4:])
                node_id = node_ids[index]
                selected = [d for d in devices if d['node_id'] == node_id]
                env['runtime'] = {'node_id': node_id, 'logical_ids': [d['logical_id'] for d in selected],
                                  'boot_id': selected[0]['boot_id'], 'epoch': request['version'],
                                  'container_name': 'hive-' + space_id + '-' + env['alias']}
                self.save_env(env)
            if not closing and 'resource_mappings' not in env['runtime']:
                binding = space['runtime']['bindings'][env['spec']['node_alias']]
                mappings = binding['resource_mappings']
                env['runtime'].update(resource_mappings=mappings, mappings_version=binding['mappings_version'],
                                      resolved_image=mappings['image'].get(env['spec']['image'], env['spec']['image']))
                self.save_env(env)
            if not closing:
                selected = [d for d in devices if d['node_id'] == env['runtime']['node_id']]
                if (env['runtime']['epoch'] != request['version'] or not selected
                        or [d['logical_id'] for d in selected] != env['runtime']['logical_ids']
                        or any(d['boot_id'] != env['runtime']['boot_id'] for d in selected)):
                    raise DomainError('任务分配或节点身份改变，停止启动新步骤并保留占用', 503)
            if not closing and env['status'] not in {'READY', 'FAILED', 'STOPPED'}:
                self.prepare_environment(space, env)
        failed_environment = any(env['status'] == 'FAILED' for env in envs)
        if not closing and not failed_environment and any(env['status'] != 'READY' for env in envs):
            self.space_status(space, 'PREPARING')
            return
        if not closing:
            self.space_status(space, 'READY')
        by_alias = {e['alias']: e for e in envs}
        for task in tasks:
            if task['status'] in FINAL:
                continue
            task['spec'] = decode(task['spec'])
            jobs = self.db.all('SELECT * FROM workflow_jobs WHERE workflow_id=%s ORDER BY id', (task['id'],))
            for job in jobs:
                job['spec'], job['runtime'] = decode(job['spec']), decode(job['runtime'])
            by_id = {j['id']: j for j in jobs}
            cancel = task['cancel_requested'] or closing or failed_environment
            for job in jobs:
                if job['status'] not in FINAL:
                    env = by_alias[job['spec']['environment']]
                    dependency_failed = any(by_id[d['job_id']]['status'] in {'FAILED', 'CANCELLED', 'SKIPPED'} for d in job['spec']['depends_on'])
                    timed_out = bool(job['started_at'] and (now() - job['started_at']).total_seconds() >= job['spec']['timeout_seconds'])
                    if cancel or timed_out or (dependency_failed and job['status'] != 'PENDING'):
                        if not job['runtime'].get('attempts') or self.close_attempts(job, env, self.save_job, skip_post=True):
                            if job['spec']['post_policy'] == 'always' and job['spec']['post'] and job['started_at'] and env['runtime'].get('identity'):
                                context = self.context(space, env, '/var/tmp/hive/tasks/' + task['id'] + '/' + job['id'] + '/source')
                                context.update(task_id=task['id'], job_id=job['id'], port=job['spec']['ports'][0] if job['spec']['ports'] else '', endpoint=job['runtime'].get('endpoint', ''))
                                for other in jobs:
                                    if other['runtime'].get('endpoint'):
                                        context[other['id'] + '.endpoint'] = other['runtime']['endpoint']
                                        context[other['id'] + '.host'] = other['runtime']['host']
                                        context[other['id'] + '.port'] = other['spec']['ports'][0]
                                for alias, instance in job['spec'].get('endpoint_sources', {}).items():
                                    for field in ('endpoint', 'host', 'port'):
                                        if instance + '.' + field in context:
                                            context[alias + '.' + field] = context[instance + '.' + field]
                                code = self.attempt(job, 'post', render_steps(job['spec']['post'], env['spec'], context), env, self.variables(env, context, job['runtime'].get('cards', [])), self.save_job, min(300, job['spec']['timeout_seconds']))
                                if code is None:
                                    continue
                                if code:
                                    job['reason'] = '收尾脚本退出码 ' + str(code)
                            job.update(status='FAILED' if failed_environment or dependency_failed or timed_out else 'CANCELLED', ended_at=now())
                            if timed_out:
                                job['reason'] = 'job 超过时限'
                            self.save_job(job)
                            self.unclaim(job)
                    else:
                        self.run_job(space, task, job, env, by_id)
                    if job['status'] in FINAL:
                        self.unclaim(job)
            finished = all(j['status'] in FINAL for j in jobs)
            status = ('FAILED' if any(j['status'] in {'FAILED', 'SKIPPED'} for j in jobs) else 'CANCELLED' if cancel else 'SUCCEEDED') if finished else 'CANCELLING' if cancel else 'RUNNING'
            task['status'] = status
            with self.db.transaction() as c:
                c.execute('UPDATE workflows SET status=%s,started_at=COALESCE(started_at,%s),ended_at=%s WHERE id=%s', (status, now(), now() if finished else None, task['id']))
        if all(t['status'] in FINAL for t in tasks):
            retention = space['spec']['retain_minutes']
            if closing or failed_environment or not retention:
                self.close_space(space, envs)
            elif space['retain_until'] is None:
                with self.db.transaction() as c:
                    c.execute('UPDATE workflow_spaces SET retain_until=%s WHERE id=%s', (now() + timedelta(minutes=retention), space_id))
            elif space['retain_until'] <= now():
                self.close_space(space, envs)
