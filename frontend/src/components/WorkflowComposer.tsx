import { useRef, useState, type FormEvent } from 'react';
import { errorText, operationKey, post } from '../api';
import type { WorkflowEnvironment, WorkflowJob, WorkflowRun, WorkflowSource, WorkflowStep, WorkflowSpace } from '../workflowTypes';
import { useQuery } from '../hooks';
import type { ResourceSpec, User } from '../types';
import { defaultResource } from './ResourceForm';
import { WorkflowPresets } from './WorkflowPresets';
import { WorkflowGraph } from './WorkflowGraph';
import { WorkflowJobEditor } from './WorkflowJobEditor';
import { stepCommand, stepFiles } from './WorkflowFiles';
import { WorkflowEnvironmentEditor } from './WorkflowEnvironmentEditor';
import { ErrorNotice, Field, Icon } from './ui';

export const emptyStep = (): WorkflowStep => ({ type: 'shell', path: '', args: [], launch: '', files: [] });
const environment = (alias: string, role: 'server' | 'client'): WorkflowEnvironment => ({ alias, role, node_alias: 'node0', image: '', shell: '/bin/bash', python: 'python3', workdir: '/home', environment: {}, packages: [], bootstrap: { type: 'shell', path: '/mnt/share/c00814587/start-docker-A3.sh', args: ['${image}', '${container_name}'], external: true }, install: [], verify: [] });
export const emptyJob = (index: number, target = 'server_env'): WorkflowJob => ({ id: `job${index}`, name: `job${index}`, environment: target, kind: 'batch', npu_count: 1, ports: [], depends_on: [], pre: [], steps: [emptyStep()], post: [], post_policy: 'success', ready: [], timeout_seconds: 600 });

function presetSteps(values: WorkflowStep[] | undefined): WorkflowStep[] {
  if (values === undefined) return [];
  if (!Array.isArray(values)) throw new Error('预置文件步骤必须是数组。');
  return values.map(step => {
    if (!step || (step.launch == null && typeof step.path !== 'string') || step.args != null && (!Array.isArray(step.args) || step.args.some(arg => typeof arg !== 'string'))) throw new Error('预置文件入口或参数格式无效。');
    const value = { ...emptyStep(), ...step, launch: step.launch, args: step.args || [] };
    if (step.runner) value.runner = { ...step.runner, type: step.runner.type || 'python', args: step.runner.args || [] };
    if (value.inputs && (!Array.isArray(value.inputs) || value.inputs.some(input => !input || typeof input.path !== 'string'))) throw new Error('预置输入文件格式无效。');
    return value;
  });
}
function presetJobs(values: WorkflowJob[]) {
  if (!Array.isArray(values) || !values.length) throw new Error('预置需要提供至少一个作业。');
  return values.map((job, index) => {
    if (!job || typeof job.id !== 'string' || typeof job.environment !== 'string' || !Array.isArray(job.steps)) throw new Error('预置作业必须包含 id、目标环境和执行入口。');
    if (job.depends_on != null && (!Array.isArray(job.depends_on) || job.depends_on.some(dep => !dep || typeof dep.job_id !== 'string'))) throw new Error('预置依赖格式无效。');
    if (job.ports != null && !Array.isArray(job.ports) || job.artifacts != null && !Array.isArray(job.artifacts)) throw new Error('预置端口或产物列表格式无效。');
    return { ...emptyJob(index + 1), ...job, name: job.name || job.id, depends_on: (job.depends_on || []).map(dep => ({ ...dep, condition: dep.condition || 'succeeded' })), ports: job.ports || [], pre: presetSteps(job.pre), steps: presetSteps(job.steps), post: presetSteps(job.post), ready: presetSteps(job.ready) };
  });
}

export function WorkflowComposer({ onSubmitted, user, resource, onResourceChange: setResource, validateResource }: { onSubmitted: () => void; user: User; resource: ResourceSpec; onResourceChange: (value: ResourceSpec) => void; validateResource: () => boolean }) {
  const canRequest = user.admin || Boolean(user.can_request);
  const spaces = useQuery<WorkflowSpace[]>('/spaces', 15000);
  const [reuse, setReuse] = useState(false), [spaceId, setSpaceId] = useState('');
  const [retainMinutes, setRetainMinutes] = useState(0);
  const selectedSpace = spaces.data?.find(space => String(space.id) === spaceId);
  const chooseSpace = (id: string) => { setSpaceId(id); const space = spaces.data?.find(value => String(value.id) === id); if (space) setEnvironments(space.environments.map(env => ({ ...environment(env.alias, env.role === 'client' ? 'client' : 'server'), ...env, role: env.role === 'client' ? 'client' : 'server' }))); };
  const [name, setName] = useState('');
  const [tab, setTab] = useState<'environment' | 'jobs'>('environment');
  const [selectedJobId, setSelectedJobId] = useState<string>();
  const [presetId, setPresetId] = useState<string | number>();
  const [pr, setPr] = useState('');
  const latestPr = useRef('');
  const [source, setSource] = useState<WorkflowSource>();
  const [environments, setEnvironments] = useState(() => [environment('server_env', 'server'), environment('client_env', 'client')]);
  const [jobs, setJobs] = useState(() => [emptyJob(1)]);
  const [pendingUploads, setPendingUploads] = useState(0);
  const onReading = (delta: number) => setPendingUploads(value => value + delta);
  const [busy, setBusy] = useState(false), [resolving, setResolving] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  const submission = useRef({ signature: '', key: '' });
  const resolveSource = async () => {
    if (resolving || !pr.trim()) return;
    setResolving(true); setError('');
    try { const requested = pr.trim(); const resolved = await post<WorkflowSource>('/sources/resolve', { pr: requested }); if (latestPr.current.trim() === requested) setSource(resolved); }
    catch (err) { setError(errorText(err)); } finally { setResolving(false); }
  };
  const updateEnvironment = (index: number, next: Partial<WorkflowEnvironment>) => { const old = environments[index].alias; if (next.alias !== undefined && next.alias !== old) setJobs(values => values.map(job => ({ ...job, environment: job.environment === old ? next.alias! : job.environment, artifacts: job.artifacts?.map(item => ({ ...item, environment: item.environment === old ? next.alias : item.environment })) }))); setEnvironments(values => values.map((value, i) => i === index ? { ...value, ...next } : value)); };
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (busy || !canRequest || !reuse && !validateResource()) return;
    setError(''); setNotice('');
    const invalid = event.currentTarget.querySelector<HTMLInputElement | HTMLTextAreaElement>(':invalid');
    if (invalid) {
      const panel = invalid.closest('[role="tabpanel"]');
      if (panel) setTab(panel.id === 'panel-jobs' ? 'jobs' : 'environment');
      setError('请补全或修正标记的配置项。');
      requestAnimationFrame(() => { invalid.focus(); invalid.reportValidity(); });
      return;
    }
    if (reuse && !selectedSpace) { setError('请选择仍有效的运行空间。'); return; }
    if (!source) { setError('请先解析 PR，固定执行代码版本。'); return; }
    const aliases = new Set(environments.map(env => env.alias));
    if (!jobs.length || !environments.length) { setError('至少配置一个环境和一个作业。'); return; }
    if (aliases.size !== environments.length || environments.some(env => !/^node\d+$/.test(env.node_alias) || Number(env.node_alias.slice(4)) >= (reuse ? selectedSpace?.spec?.resource?.machine_count || resource.machine_count : resource.machine_count))) { setError('环境代称不能重复，且节点代称必须在当前申请数量内。'); return; }
    if (jobs.some(job => !aliases.has(job.environment) || !job.steps.length || job.kind === 'service' && !job.ready.length)) { setError('请检查作业目标环境、主执行入口和服务就绪检查。'); return; }
    const paths = jobs.flatMap(job => [...job.pre, ...job.steps, ...job.post, ...job.ready]).concat(environments.flatMap(env => [...env.install, ...env.verify]));
    if (paths.some(step => !stepCommand(step).trim() && !step.files?.length)) { setError('请上传执行文件或填写启动命令。'); return; }
    const serialize = (step: WorkflowStep) => !step.path || step.launch?.trim() || step.files?.length ? { launch: stepCommand(step), files: stepFiles(step) } : step;
    const payload = { name: name.trim(), source, ...(presetId != null ? { preset_id: presetId } : {}), ...(reuse ? { space_id: spaceId } : { resource: { ...resource, min_memory_gib: 0 } }), retain_minutes: retainMinutes, environments: reuse ? [] : environments.map(env => ({ ...env, install: env.install.map(serialize), verify: env.verify.map(serialize) })), jobs: jobs.map(job => ({ ...job, pre: job.pre.map(serialize), steps: job.steps.map(serialize), post: job.post.map(serialize), ready: job.ready.map(serialize) })) };
    const signature = JSON.stringify(payload);
    setBusy(true);
    try { if (signature !== submission.current.signature) submission.current = { signature, key: operationKey() }; const result = await post<WorkflowRun>('/workflows', { ...payload, idempotency_key: submission.current.key }); setNotice(`任务「${result.name}」已提交，资源和环境状态将在下方更新。`); submission.current = { signature: '', key: '' }; onSubmitted(); }
    catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return <section className="panel workflow-composer section-gap"><div className="panel-title"><span className="title-icon"><Icon name="task" /></span><div><h2>新建容器任务</h2><p>固定 PR、独立环境与作业依赖</p></div></div><form noValidate onSubmit={event => void submit(event)}><div className="panel-body">{!canRequest && <p className="notice">尚未获得服务器申请权限，请联系管理员开启任务提交。</p>}<WorkflowPresets admin={user.admin} source={source} onApply={preset => { if (!preset.enabled || !preset.workflow) return; setError(''); try { const snapshot = structuredClone(preset.workflow); const nextJobs = presetJobs(snapshot.jobs || []); let nextEnvironments: WorkflowEnvironment[] | undefined; if (snapshot.environments && !reuse) { if (!Array.isArray(snapshot.environments)) throw new Error('预置环境列表格式无效。'); nextEnvironments = snapshot.environments.map(env => { if (!env || typeof env.alias !== 'string' || !['server', 'client'].includes(env.role)) throw new Error('预置环境代称或角色无效。'); return { ...environment(env.alias, env.role), ...env, install: presetSteps(env.install), verify: presetSteps(env.verify), packages: env.packages || [], environment: env.environment || {} }; }); } setPresetId(preset.id); setName(snapshot.name || preset.name); setJobs(nextJobs); if (nextEnvironments) setEnvironments(nextEnvironments); if (snapshot.resource && !reuse) setResource({ ...defaultResource(), ...snapshot.resource, min_memory_gib: 0 }); setNotice(`已载入「${preset.name}」的外部执行配置，请确认 PR 与环境输入后提交。`); } catch (err) { setError(errorText(err)); } }} />
    <div className="form-grid"><Field label="任务名称"><input required maxLength={128} value={name} onChange={e => setName(e.target.value)} /></Field><Field label="vLLM-Ascend PR" hint="填写 PR 编号或该仓库的 PR 链接。"><div className="workflow-inline"><input aria-label="vLLM-Ascend PR" required value={pr} onChange={e => { setPr(e.target.value); latestPr.current = e.target.value; setSource(undefined); }} /><button type="button" className="button secondary small-button" disabled={resolving || !pr.trim()} onClick={() => void resolveSource()}>{resolving ? '解析中…' : '解析 PR'}</button></div></Field></div>
    {source && <dl className="detail-list source-snapshot"><dt>执行代码 SHA</dt><dd><code>{source.head_sha}</code></dd><dt>匹配 vLLM SHA</dt><dd><code>{source.vllm_sha}</code></dd></dl>}
    <h3 className="section-title">资源与环境生命周期</h3><label className="check-field"><input type="checkbox" checked={reuse} onChange={event => { setReuse(event.target.checked); if (!event.target.checked) { setSpaceId(''); setEnvironments([environment('server_env', 'server'), environment('client_env', 'client')]); } }} /><span>使用保留环境</span></label>{reuse ? <><div className="field"><span>运行空间</span><div className="choice-buttons" role="group" aria-label="运行空间">{spaces.data?.filter(space => (space.owner_name === user.username || user.admin) && !['CLOSED', 'CLOSING', 'FAILED', 'EXPIRED'].includes(space.status)).map(space => <button type="button" key={space.id} aria-pressed={spaceId === String(space.id)} onClick={() => chooseSpace(String(space.id))}>#{space.id} · {space.status}</button>)}</div></div><ErrorNotice text={spaces.error} /></> : null}<label className="check-field"><input type="checkbox" checked={retainMinutes > 0} onChange={event => setRetainMinutes(event.target.checked ? 60 : 0)} /><span>保留环境用于后续任务</span></label>{retainMinutes > 0 && <Field label="保留时长（分钟）"><input type="number" min={1} max={10080} required value={retainMinutes} onChange={event => setRetainMinutes(Number(event.target.value))} /></Field>}
    <div className="workflow-tabs" role="tablist" aria-label="任务配置"><button type="button" role="tab" id="tab-environment" aria-controls="panel-environment" aria-selected={tab === 'environment'} onClick={() => setTab('environment')}>任务环境</button><button type="button" role="tab" id="tab-jobs" aria-controls="panel-jobs" aria-selected={tab === 'jobs'} onClick={() => setTab('jobs')}>Jobs</button></div><div role="tabpanel" id="panel-environment" aria-labelledby="tab-environment" hidden={tab !== 'environment'}><h3 className="section-title">独立容器环境</h3><p className="muted small">节点代称代表申请的位置；同机 server / client 使用两个独立容器，只申请一次机器。</p><div className="workflow-environments">{reuse ? environments.map(env => <div className="workflow-environment" key={env.alias}><h4>{env.alias}</h4><p>{env.role} · {env.node_alias}</p><p className="muted small">使用空间已有环境，安装输入与容器身份由中心重新核验。</p></div>) : environments.map((env, index) => <WorkflowEnvironmentEditor key={index} value={env} nodes={Array.from({ length: resource.machine_count }, (_, i) => `node${i}`)} onReading={onReading} onChange={next => updateEnvironment(index, next)} onDelete={() => { if (jobs.some(job => job.environment === env.alias || job.artifacts?.some(item => item.environment === env.alias))) { setError(`环境 ${env.alias} 仍被作业引用，请先修改作业目标。`); return; } setEnvironments(values => values.filter((_, i) => i !== index)); }} />)}</div>{!reuse && <button type="button" className="text-button" onClick={() => setEnvironments(values => { let index = values.length + 1; while (values.some(value => value.alias === `env${index}`)) index += 1; return [...values, { ...environment(`env${index}`, 'client'), node_alias: Array.from({ length: resource.machine_count }, (_, i) => `node${i}`).find(node => !values.some(value => value.node_alias === node)) || 'node0' }]; })}>添加独立环境</button>}
    </div><div role="tabpanel" id="panel-jobs" aria-labelledby="tab-jobs" hidden={tab !== 'jobs'}><h3 className="section-title">作业依赖</h3><p className="muted small">启动命令可使用 <code>{'${node0.ip}'}</code>、<code>{'${host}'}</code> 和 <code>{'${container_name}'}</code>；脚本可用 <code>hive_resource model 名称</code> 查询服务器资源映射。</p><WorkflowGraph jobs={jobs} onChange={setJobs} selectedJobId={selectedJobId} onSelect={id => { setSelectedJobId(id); const editor = document.getElementById(`editor-${id}`); editor?.scrollIntoView({ behavior: 'smooth', block: 'center' }); editor?.querySelector<HTMLInputElement>('input')?.focus({ preventScroll: true }); }} />{jobs.map((job, index) => <WorkflowJobEditor key={job.id} job={job} jobs={jobs} environments={environments} onReading={onReading} onChange={next => setJobs(values => values.map((value, i) => ({ ...(i === index ? { ...value, ...next } : value), depends_on: next.kind ? value.depends_on.map(dep => dep.job_id === job.id ? { ...dep, condition: next.kind === 'service' ? 'ready' as const : 'succeeded' as const } : dep) : value.depends_on })))} onDelete={() => { if (jobs.some(value => value.depends_on.some(dep => dep.job_id === job.id))) { setError(`作业 ${job.id} 仍被其他作业依赖，请先删除连线。`); return; } setJobs(values => values.filter(value => value.id !== job.id)); }} />)}<button type="button" className="button secondary small-button" onClick={() => { const next = Math.max(0, ...jobs.map(job => Number(job.id.replace('job', '')) || 0)) + 1; setJobs(values => [...values, emptyJob(next, environments[0]?.alias || '')]); }}>添加作业</button>
    </div><ErrorNotice text={error} />{notice && <div className="notice success" role="status">{notice}</div>}
  </div><div className="form-footer"><span className="muted small">上传文件关联固定 PR，启动命令在目标容器内执行。</span><button className="button primary" disabled={busy || resolving || pendingUploads > 0 || !canRequest}>{busy ? '正在提交…' : '提交任务申请'}<Icon name="arrow" size={16} /></button></div></form></section>;
}
