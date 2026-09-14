"""Business-neutral workflow input contracts."""
from typing import Literal
import re
from pathlib import PurePosixPath
from pydantic import Field, model_validator
from .schemas import Input, ResourceSpec


class FileInput(Input):
    path: str = Field(min_length=1, max_length=512)
    type: Literal['yaml'] = 'yaml'
    uploaded_content: str | None = Field(default=None, max_length=262144)


class Runner(Input):
    type: Literal['shell', 'python'] = 'python'
    path: str = Field(min_length=1, max_length=512)
    args: list[str] = Field(default_factory=list, max_length=128)


class UploadedFile(Input):
    name: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=262144)

    @model_validator(mode='after')
    def valid_file(self):
        if (not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', self.name)
                or any(part in {'.', '..', '.git'} for part in self.name.split('/'))
                or not self.name.lower().endswith(('.sh', '.py', '.yaml', '.yml')) or '\x00' in self.content):
            raise ValueError('上传文件须为 Shell/Python/YAML，使用有效相对文件名')
        return self


class Step(Input):
    launch: str = Field(default='', max_length=65536)
    files: list[UploadedFile] = Field(default_factory=list, max_length=32)
    type: Literal['shell', 'python', 'yaml'] = 'shell'
    path: str = Field(default='', max_length=512)
    args: list[str] = Field(default_factory=list, max_length=128)
    inputs: list[FileInput] = Field(default_factory=list, max_length=32)
    uploaded_content: str | None = Field(default=None, max_length=262144)
    runner: Runner | None = None
    external: bool = False

    @model_validator(mode='after')
    def valid_entry(self):
        if self.launch or self.files:
            if not self.launch.strip() and not self.files:
                raise ValueError('请上传文件或填写启动命令')
            if '${input}' in self.launch and not self.files:
                raise ValueError('${input} 需要至少一个上传文件')
            if self.path or self.runner or self.inputs or self.external or self.uploaded_content is not None or self.args:
                raise ValueError('上传/启动命令与旧文件入口不可混用')
            if '\x00' in self.launch or len({f.name for f in self.files}) != len(self.files):
                raise ValueError('启动命令无效或上传文件重复')
            if not self.launch.strip() and any(f.name.lower().endswith(('.yaml', '.yml')) for f in self.files):
                raise ValueError('YAML 需要填写启动命令来指定执行器')
            return self
        if not self.path:
            raise ValueError('请上传文件或填写启动命令')
        paths = [self.path] + [i.path for i in self.inputs] + ([self.runner.path] if self.runner else [])
        for path in paths:
            if '\\' in path or '\x00' in path or '..' in PurePosixPath(path).parts or any(ord(c) < 32 for c in path):
                raise ValueError('文件路径无效')
            if path.startswith('/') and not (self.external and path == self.path and path == '/mnt/share/c00814587/start-docker-A3.sh'):
                raise ValueError('入口必须引用 PR 内相对路径')
        if self.external and (self.type != 'shell' or self.path != '/mnt/share/c00814587/start-docker-A3.sh'):
            raise ValueError('未登记的外部引导入口')
        if self.type == 'yaml' and not self.runner:
            raise ValueError('YAML 需要指定外部执行器')
        if self.type == 'yaml' and not any('${input}' in a for a in self.args + self.runner.args):
            raise ValueError('YAML 执行器参数需包含 ${input}')
        if any('\x00' in a or len(a) > 16384 for a in self.args + (self.runner.args if self.runner else [])):
            raise ValueError('参数无效')
        return self


class Package(Input):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(default='', max_length=256)
    source: str = Field(default='', max_length=2048)


class Environment(Input):
    alias: str = Field(pattern=r'^[a-z][a-z0-9_]{0,31}$')
    role: Literal['server', 'client'] = 'server'
    node_alias: str | None = Field(default=None, pattern=r'^node(?:0|[1-9][0-9]*)$')
    node_aliases: list[str] | None = Field(default=None, min_length=1, max_length=64)
    image: str = Field(min_length=1, max_length=255)
    shell: str = '/bin/bash'
    python: str = 'python3'
    workdir: str = '/home'
    environment: dict[str, str] = Field(default_factory=dict)
    packages: list[Package] = Field(default_factory=list, max_length=32)
    bootstrap: Step
    install: list[Step] = Field(default_factory=list, max_length=32)
    verify: list[Step] = Field(default_factory=list, max_length=32)

    @model_validator(mode='after')
    def valid_environment(self):
        nodes = self.node_aliases if self.node_aliases is not None else ([self.node_alias] if self.node_alias else [])
        if (not nodes or len(nodes) != len(set(nodes))
                or any(not re.fullmatch(r'node(?:0|[1-9][0-9]*)', node) for node in nodes)
                or self.node_alias is not None and self.node_aliases is not None and nodes != [self.node_alias]):
            raise ValueError('环境需选择不重复的节点，不能同时提交矛盾的单节点和多节点绑定')
        self.node_aliases = sorted(nodes, key=lambda node: int(node[4:]))
        self.node_alias = self.node_aliases[0] if len(nodes) == 1 else None
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/:@+-]{0,254}', self.image):
            raise ValueError('镜像引用无效')
        for executable in (self.shell, self.python):
            if not re.fullmatch(r'[A-Za-z0-9_/.+-]+', executable) or '..' in PurePosixPath(executable).parts:
                raise ValueError('解释器应为单个可执行文件路径')
        if not self.workdir.startswith('/') or '\x00' in self.workdir or '..' in PurePosixPath(self.workdir).parts:
            raise ValueError('工作目录需要绝对路径')
        if len(self.environment) > 128:
            raise ValueError('环境变量过多')
        for key, value in self.environment.items():
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or key.startswith('HIVE_') or key == 'ASCEND_RT_VISIBLE_DEVICES' or '\x00' in value or len(value) > 16384:
                raise ValueError('环境变量无效或由平台管理')
        if any(step.external for step in self.install + self.verify):
            raise ValueError('业务步骤必须引用 PR 文件')
        return self


class Dependency(Input):
    job_id: str
    condition: Literal['succeeded', 'ready'] = 'succeeded'


class ArtifactTarget(Input):
    environment: str = Field(min_length=1, max_length=32)
    node_alias: str = Field(pattern=r'^node(?:0|[1-9][0-9]*)$')


class Artifact(Input):
    environment: str = Field(default='', max_length=32)
    targets: list[ArtifactTarget] = Field(default_factory=list, max_length=128)
    path: str = Field(min_length=1, max_length=1024)
    label: str = Field(default='', max_length=128)
    kind: Literal['file', 'metrics', 'auto'] = 'auto'

    @model_validator(mode='after')
    def valid_path(self):
        if '${' in re.sub(r'\$\{(?:task_id|job_id|HIVE_TASK_ID|HIVE_JOB_ID|HIVE_OUTPUT_DIR)\}', '', self.path):
            raise ValueError('产物路径支持 HIVE_OUTPUT_DIR、HIVE_TASK_ID 和 HIVE_JOB_ID')
        if self.environment and self.targets:
            raise ValueError('产物请使用环境或精确节点目标中的一种')
        if len({(target.environment, target.node_alias) for target in self.targets}) != len(self.targets):
            raise ValueError('产物节点目标不能重复')
        absolute = re.sub(r'^\$(?:\{HIVE_OUTPUT_DIR\}|HIVE_OUTPUT_DIR)(?=/|$)', '/output', self.path)
        if not absolute.startswith('/') or '..' in PurePosixPath(absolute).parts or '\x00' in absolute:
            raise ValueError('产物需要 HIVE_OUTPUT_DIR 下的路径或容器内绝对路径')
        if not self.label:
            self.label = PurePosixPath(self.path).name
        return self


class Job(Input):
    id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,47}$')
    name: str = Field(default='', max_length=128)
    environment: str
    node_aliases: list[str] | None = Field(default=None, min_length=1, max_length=64)
    kind: Literal['batch', 'service'] = 'batch'
    npu_count: int = Field(default=1, ge=0, le=128)
    ports: list[int] = Field(default_factory=list, max_length=16)
    depends_on: list[Dependency] = Field(default_factory=list, max_length=64)
    pre: list[Step] = Field(default_factory=list, max_length=32)
    steps: list[Step] = Field(min_length=1, max_length=32)
    post: list[Step] = Field(default_factory=list, max_length=32)
    ready: list[Step] = Field(default_factory=list, max_length=32)
    post_policy: Literal['success', 'always'] = 'success'
    timeout_seconds: int = Field(default=600, ge=1, le=604800)
    artifacts: list[Artifact] = Field(default_factory=list, max_length=16)


class WorkflowCreate(Input):
    idempotency_key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    source: dict
    preset_id: str | None = Field(default=None, max_length=64)
    resource: ResourceSpec | None = None
    space_id: str | None = None
    retain_minutes: int = Field(default=0, ge=0, le=10080)
    runtime_variables: Literal['minimal'] = 'minimal'
    output_layout: Literal['per-job'] = 'per-job'
    environments: list[Environment] = Field(default_factory=list, max_length=32)
    jobs: list[Job] = Field(min_length=1, max_length=64)

    @model_validator(mode='after')
    def valid_graph(self):
        if bool(self.resource) == bool(self.space_id):
            raise ValueError('新申请和复用运行空间需二选一')
        envs = {e.alias: e for e in self.environments}
        if len(envs) != len(self.environments):
            raise ValueError('环境代称不能重复')
        if self.resource:
            nodes = {node for env in self.environments for node in env.node_aliases}
            if nodes != {'node' + str(i) for i in range(self.resource.machine_count)}:
                raise ValueError('环境节点代称需与申请机器数一致')
        jobs = {j.id: j for j in self.jobs}
        if len(jobs) != len(self.jobs):
            raise ValueError('job ID 不能重复')
        variable_owners = {}
        for job in self.jobs:
            nodes = job.node_aliases or (envs[job.environment].node_aliases if job.environment in envs else
                                        ['node' + str(i) for i in range(64)])
            for alias in [job.id] + [job.id + '.' + node for node in nodes]:
                variable = re.sub(r'[^A-Za-z0-9]', '_', alias).upper()
                if variable in variable_owners and variable_owners[variable] != alias:
                    raise ValueError('job 名称转换为 environment variable 后冲突，请修改 job ID')
                variable_owners[variable] = alias
        for job in self.jobs:
            if self.environments and job.environment not in envs:
                raise ValueError('job 引用了不存在的环境')
            if job.node_aliases is not None and (len(job.node_aliases) != len(set(job.node_aliases))
                    or any(not re.fullmatch(r'node(?:0|[1-9][0-9]*)', node) for node in job.node_aliases)
                    or self.environments and not set(job.node_aliases) <= set(envs[job.environment].node_aliases)):
                raise ValueError('job 节点需为所选环境中不重复的节点')
            if self.environments and any(a.environment and a.environment not in envs for a in job.artifacts):
                raise ValueError('产物引用了不存在的服务器环境')
            if self.environments and any(target.environment not in envs or target.node_alias not in envs[target.environment].node_aliases
                                         for artifact in job.artifacts for target in artifact.targets):
                raise ValueError('产物节点不属于所选环境')
            if job.kind == 'service' and not job.ready:
                raise ValueError('服务 job 需要就绪检查入口')
            if len(set(job.ports)) != len(job.ports) or any(not 1024 <= p <= 65535 for p in job.ports):
                raise ValueError('端口应为不重复的 1024–65535 整数')
            if self.resource and self.resource.mode == 'partial' and job.npu_count > self.resource.cards_per_node:
                raise ValueError('job 用卡数超过每台申请卡数')
            if any(s.external for s in job.pre + job.steps + job.post + job.ready):
                raise ValueError('job 只能在容器内执行 PR 文件')
            seen = set()
            for dep in job.depends_on:
                if dep.job_id not in jobs or dep.job_id == job.id or dep.job_id in seen:
                    raise ValueError('依赖不存在、自依赖或重复')
                if dep.condition == 'ready' and jobs[dep.job_id].kind != 'service':
                    raise ValueError('ready 依赖只能引用服务 job')
                if dep.condition == 'succeeded' and jobs[dep.job_id].kind == 'service':
                    raise ValueError('依赖服务 job 时请选择 ready，服务会在使用方结束后关闭')
                seen.add(dep.job_id)
        visited, visiting = set(), set()
        def visit(key):
            if key in visiting:
                raise ValueError('job 依赖存在环')
            if key in visited:
                return
            visiting.add(key)
            for dep in jobs[key].depends_on:
                visit(dep.job_id)
            visiting.remove(key)
            visited.add(key)
        for key in jobs:
            visit(key)
        allowed = {'space_id','node_alias','host','container_name','image','source_dir','vllm_sha','ascend_sha','input'}
        for node in {node for env in self.environments for node in env.node_aliases}:
            allowed.update({node + '.ip', node + '.host'})
        def check(entries, tokens):
            for entry in entries:
                for token in re.findall(r'\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}', entry.launch):
                    if ('.' in token or token in allowed | {'task_id','job_id','port','endpoint'}) and token not in tokens:
                        if not (self.space_id and not self.environments and re.fullmatch(r'node[0-9]+\.(ip|host)', token)):
                            raise ValueError('当前阶段没有此参数来源: ' + token)
                for arg in entry.args + (entry.runner.args if entry.runner else []):
                    for token in re.findall(r'\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}', arg):
                        if token not in tokens and not (self.space_id and not self.environments and re.fullmatch(r'node[0-9]+\.(ip|host)', token)):
                            raise ValueError('当前阶段没有此参数来源: ' + token)
        check([step for env in self.environments for step in [env.bootstrap] + env.install + env.verify], allowed)
        for job in self.jobs:
            tokens = allowed | {'task_id', 'job_id', 'port', 'endpoint'}
            for key in {job.id} | {d.job_id for d in job.depends_on}:
                if jobs[key].ports:
                    tokens.update({key + '.endpoint', key + '.host', key + '.port'})
                    nodes = jobs[key].node_aliases or (envs[jobs[key].environment].node_aliases if self.environments else
                                                     ['node' + str(i) for i in range(64)])
                    tokens.update(key + '.' + node + '.' + field for node in nodes for field in ('endpoint', 'host', 'port'))
            check(job.pre + job.steps + job.ready + job.post, tokens)
        return self
