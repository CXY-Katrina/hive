"""Compile logical multi-node definitions into isolated existing execution units."""
import copy
import hashlib
import re
from .domain import DomainError


def environment_nodes(env):
    return env.get('node_aliases') or [env['node_alias']]


def _instance_id(prefix, logical, node, count):
    if count == 1:
        return logical
    return prefix + hashlib.sha256((logical + '\0' + node).encode()).hexdigest()[:24]


def expand_workflow(spec):
    env_nodes = {env['alias']: environment_nodes(env) for env in spec['environments']}
    if sum(map(len, env_nodes.values())) > 128 or sum(len(job.get('node_aliases') or env_nodes[job['environment']]) for job in spec['jobs']) > 256:
        raise DomainError('展开后最多支持 128 个环境实例和 256 个 job 实例', 422)
    environments, env_ids = [], {}
    for env in spec['environments']:
        nodes = environment_nodes(env)
        env_nodes[env['alias']] = nodes
        for node in nodes:
            ident = _instance_id('e_', env['alias'], node, len(nodes))
            env_ids[env['alias'], node] = ident
            environments.append({**copy.deepcopy(env), 'alias': ident, 'logical_alias': env['alias'],
                                 'node_alias': node, 'node_aliases': [node]})
    jobs, job_ids = [], {}
    for job in spec['jobs']:
        nodes = job.get('node_aliases') or env_nodes[job['environment']]
        job_ids[job['id']] = {node: _instance_id('j_', job['id'], node, len(nodes)) for node in nodes}
    for job in spec['jobs']:
        for node, ident in job_ids[job['id']].items():
            endpoint_sources = {}
            for key in {job['id']} | {dep['job_id'] for dep in job['depends_on']}:
                instances = job_ids[key]
                endpoint_sources.update({key + '.' + source_node: source_id for source_node, source_id in instances.items()})
                if node in instances or len(instances) == 1:
                    endpoint_sources[key] = instances.get(node, next(iter(instances.values())))
                else:
                    for step in job['pre'] + job['steps'] + job['ready'] + job['post']:
                        values = [step.get('launch', '')] + step.get('args', []) + (step.get('runner') or {}).get('args', [])
                        if any(re.search(r'\$\{' + re.escape(key) + r'\.(?:endpoint|host|port)\}', value) for value in values):
                            raise DomainError('多节点服务参数不明确，请指定 ' + key + '.node0.endpoint 等节点参数', 422)
            dependencies = [dict(dep, job_id=instance) for dep in job['depends_on']
                            for instance in job_ids[dep['job_id']].values()]
            artifacts = []
            for item in job['artifacts']:
                artifact_environment = item.get('environment') or job['environment']
                targets = item.get('targets') or [{'environment': artifact_environment, 'node_alias': target_node}
                    for target_node in env_nodes[artifact_environment]]
                for target in targets:
                    # A selected target is archived once by its local job instance when possible.
                    owner = target['node_alias'] if target['node_alias'] in job_ids[job['id']] else next(iter(job_ids[job['id']]))
                    if node == owner:
                        artifacts.append({**{key: copy.deepcopy(value) for key, value in item.items() if key != 'targets'},
                            'environment': env_ids[target['environment'], target['node_alias']]})
            if len(artifacts) > 2048:
                raise DomainError('每个 job 实例最多归档 2048 个展开后的节点产物', 422)
            if len({(a.get('environment') or env_ids[job['environment'], node], a['path']) for a in artifacts}) != len(artifacts):
                raise DomainError('产物目标路径重复', 422)
            jobs.append({**copy.deepcopy(job), 'id': ident, 'logical_job_id': job['id'],
                         'logical_environment': job['environment'], 'node_alias': node, 'node_aliases': [node],
                         'environment': env_ids[job['environment'], node], 'depends_on': dependencies,
                         'endpoint_sources': endpoint_sources, 'artifacts': artifacts})
    if len({e['alias'] for e in environments}) != len(environments) or len({j['id'] for j in jobs}) != len(jobs):
        raise DomainError('环境或 job 实例标识冲突，请调整逻辑名称', 422)
    return {'environments': environments, 'jobs': jobs}


def aggregate_instances(rows, logical_key, output_key):
    grouped = {}
    for row in rows:
        logical = row.get(logical_key) or row[output_key]
        grouped.setdefault(logical, []).append(row)
    result = []
    for logical, instances in grouped.items():
        states = {row['status'] for row in instances}
        if len(states) == 1:
            status = next(iter(states))
        elif states <= {'SUCCEEDED', 'FAILED', 'CANCELLED', 'SKIPPED'}:
            status = next(value for value in ('FAILED', 'CANCELLED', 'SKIPPED', 'SUCCEEDED') if value in states)
        elif 'UNKNOWN' in states:
            status = 'UNKNOWN'
        else:
            status = 'PREPARING' if output_key == 'alias' else 'RUNNING'
        result.append({output_key: logical, 'status': status, 'instances': instances})
    return result


def bind_artifact_paths(jobs, task_id):
    """Resolve only compiled instance paths; logical definitions remain reusable."""
    for job in jobs:
        seen = set()
        for artifact in job['artifacts']:
            path = artifact['path'].replace('${task_id}', task_id).replace('${job_id}', job['id'])
            for name, value in (('HIVE_TASK_ID',task_id),('HIVE_JOB_ID',job['id'])):
                path = re.sub(r'\$(?:\{' + name + r'\}|' + name + r'(?![A-Za-z0-9_]))',
                              lambda match: value,path)
            key = (artifact.get('environment') or job['environment'], path)
            if len(path) > 4096 or key in seen:
                raise DomainError('解析后的产物路径超过 4096 字符或与同一目标重复', 422)
            seen.add(key)
            artifact['path'] = path
