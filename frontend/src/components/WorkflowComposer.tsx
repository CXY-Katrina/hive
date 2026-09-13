import { useEffect, useRef, useState, type FormEvent } from 'react';
import { errorText, operationKey, post } from '../api';
import type { WorkflowEnvironment, WorkflowJob, WorkflowRun, WorkflowSource, WorkflowStep, WorkflowSpace, WorkflowPreset } from '../workflowTypes';
import { useQuery } from '../hooks';
import type { ResourceSpec, User } from '../types';
import type { WorkflowDraft } from '../workflowDrafts';
import { defaultResource } from './ResourceForm';
import { WorkflowPresets } from './WorkflowPresets';
import { WorkflowGraph } from './WorkflowGraph';
import { WorkflowJobEditor } from './WorkflowJobEditor';
import { stepCommand, stepFiles } from './WorkflowFiles';
import { WorkflowEnvironmentEditor } from './WorkflowEnvironmentEditor';
import { ErrorNotice, Field, Icon, Modal } from './ui';

export const emptyStep = (): WorkflowStep => ({ type: 'shell', path: '', args: [], launch: '', files: [] });
const environment = (alias: string, role: 'server' | 'client'): WorkflowEnvironment => ({ alias, role, node_alias: 'node0', image: '', shell: '/bin/bash', python: 'python3', workdir: '/home', environment: {}, packages: [], bootstrap: { type: 'shell', path: '/mnt/share/c00814587/start-docker-A3.sh', args: ['${image}', '${container_name}'], external: true }, install: [], verify: [] });
function environmentConfig(value: Partial<WorkflowEnvironment> & { alias: string }): WorkflowEnvironment {
  const defaults = environment(value.alias, value.role || 'server');
  const known = Object.fromEntries(Object.keys(defaults).filter(key => value[key as keyof WorkflowEnvironment] !== undefined).map(key => [key, value[key as keyof WorkflowEnvironment]]));
  return { ...defaults, ...known, ...(value.node_aliases ? { node_aliases: value.node_aliases, node_alias: value.node_aliases[0] || '' } : {}) };
}
export const emptyJob = (index: number, target = 'env1'): WorkflowJob => ({ id: `job${index}`, name: `job${index}`, environment: target, kind: 'batch', npu_count: 1, ports: [], depends_on: [], pre: [], steps: [emptyStep()], post: [], post_policy: 'success', ready: [], timeout_seconds: 600 });

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

export function WorkflowComposer({ onSubmitted, user, resource, onResourceChange: setResource, validateResource, initialDraft, onDraftChange, draftSaved }: { onSubmitted: () => void; user: User; resource: ResourceSpec; onResourceChange: (value: ResourceSpec) => void; validateResource: () => boolean; initialDraft?: WorkflowDraft; onDraftChange: (value: WorkflowDraft) => void; draftSaved: boolean }) {
  const canRequest = user.admin || Boolean(user.can_request);
  const spaces = useQuery<WorkflowSpace[]>('/spaces', 15000);
  const [reuse, setReuse] = useState(initialDraft?.reuse || false), [spaceId, setSpaceId] = useState(initialDraft?.spaceId || '');
  const [retainMinutes, setRetainMinutes] = useState(initialDraft?.retainMinutes || 0);
  const selectedSpace = spaces.data?.find(space => String(space.id) === spaceId);
  const chooseSpace = (id: string) => { setSpaceId(id); const space = spaces.data?.find(value => String(value.id) === id); if (space) setEnvironments((space.spec.environments || space.environments).map(env => environmentConfig({ ...env, role: env.role === 'client' ? 'client' : 'server' }))); };
  const [name, setName] = useState(() => { if (initialDraft) return initialDraft.name; const now = new Date(); const pad = (n: number) => String(n).padStart(2, '0'); return `任务-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`; });
  const [selectedEnvironment, setSelectedEnvironment] = useState(initialDraft?.selectedEnvironment || 0);
  const [selectedJobId, setSelectedJobId] = useState<string | undefined>(initialDraft?.selectedJobId);
  const [presetId, setPresetId] = useState<string | number | undefined>(initialDraft?.presetId);
  const [pr, setPr] = useState(initialDraft?.pr || '');
  const latestPr = useRef(initialDraft?.pr || '');
  const [source, setSource] = useState<WorkflowSource | undefined>(initialDraft?.source);
  const [environments, setEnvironments] = useState(() => initialDraft?.environments || [environment('env1', 'server'), environment('env2', 'server')]);
  const [jobs, setJobs] = useState(() => initialDraft?.jobs || [emptyJob(1)]);

  const [pendingUploads, setPendingUploads] = useState(0);
  const onReading = (delta: number) => setPendingUploads(value => value + delta);
  const [busy, setBusy] = useState(false), [resolving, setResolving] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  const formRef = useRef<HTMLFormElement>(null), deriveCheck = useRef<HTMLButtonElement>(null), deriveSubmit = useRef<HTMLButtonElement>(null);
  const [deriveOpen, setDeriveOpen] = useState(false), [deriveName, setDeriveName] = useState('');
  const [presetName, setPresetName] = useState(initialDraft?.presetName || ''), [presetTags, setPresetTags] = useState<Record<string,string>>(initialDraft?.presetTags || {});
  const submission = useRef({ signature: '', key: '' });
  useEffect(() => { onDraftChange({ name, pr, source, environments, jobs, reuse, spaceId, retainMinutes, presetId, presetName, presetTags, selectedEnvironment, selectedJobId }); }, [name, pr, source, environments, jobs, reuse, spaceId, retainMinutes, presetId, presetName, presetTags, selectedEnvironment, selectedJobId, onDraftChange]);
  const resolveSource = async () => {
    if (resolving || !pr.trim()) return;
    setResolving(true); setError('');
    try { const requested = pr.trim(); const resolved = await post<WorkflowSource>('/sources/resolve', (/^[a-fA-F0-9]{40}$/.test(requested) ? { commit: requested.toLowerCase() } : { pr: requested })); if (latestPr.current.trim() === requested) setSource(resolved); }
    catch (err) { setError(errorText(err)); } finally { setResolving(false); }
  };
  const updateEnvironment = (index: number, next: Partial<WorkflowEnvironment>) => { const old = environments[index].alias; if (next.alias !== undefined && next.alias !== old) setJobs(values => values.map(job => ({ ...job, environment: job.environment === old ? next.alias! : job.environment, artifacts: job.artifacts?.map(item => ({ ...item, environment: item.environment === old ? next.alias : item.environment, targets: item.targets?.map(target => ({ ...target, environment: target.environment === old ? next.alias! : target.environment })) })) }))); setEnvironments(values => values.map((value, i) => i === index ? { ...value, ...next } : value)); };
  const submit = async (event: FormEvent) => {
    event.preventDefault(); const action = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('data-action'); const deriving = action === 'derive-check' || action === 'derive'; if (busy || !canRequest && !deriving || !reuse && !validateResource()) return;
    setError(''); setNotice('');
    const invalid = event.currentTarget.querySelector<HTMLInputElement | HTMLTextAreaElement>(':invalid');
    if (invalid) {
      const panel = invalid.closest('[role="tabpanel"]');
      if (panel?.hasAttribute('data-environment-index')) setSelectedEnvironment(Number(panel.getAttribute('data-environment-index')));
      if (panel?.hasAttribute('data-job-id')) setSelectedJobId(panel.getAttribute('data-job-id')!);
      setError('请补全或修正标记的配置项。');
      requestAnimationFrame(() => { invalid.focus(); invalid.reportValidity(); });
      return;
    }
    if (reuse && !selectedSpace) { setError('请选择仍有效的运行空间。'); return; }
    if (!source) { setError('请先解析 PR 或 commit，固定执行代码版本。'); return; }
    const aliases = new Set(environments.map(env => env.alias));
    if (!jobs.length || !environments.length) { setError('至少配置一个环境和一个作业。'); return; }
    if (aliases.size !== environments.length || environments.some(env => { const nodes = env.node_aliases || [env.node_alias]; return !nodes.length || nodes.some(node => !/^node\d+$/.test(node) || Number(node.slice(4)) >= (reuse ? selectedSpace?.spec?.resource?.machine_count || resource.machine_count : resource.machine_count)); })) { setError('环境代称不能重复，且节点代称必须在当前申请数量内。'); return; }
    if (jobs.some(job => !aliases.has(job.environment) || !job.steps.length || job.kind === 'service' && !job.ready.length)) { setError('请检查作业目标环境、主执行入口和服务就绪检查。'); return; }
    if (jobs.some(job => job.artifacts?.some(item => item.targets && !item.targets.length))) { setError('每个产物至少选择一个目标容器与节点。'); return; }
    const paths = jobs.flatMap(job => [...job.pre, ...job.steps, ...job.post, ...job.ready]).concat(environments.flatMap(env => [...env.install, ...env.verify]));
    if (paths.some(step => !stepCommand(step).trim() && !step.files?.length)) { setError('请上传执行文件或填写启动命令。'); return; }
    const serialize = (step: WorkflowStep) => !step.path || step.launch?.trim() || step.files?.length ? { launch: stepCommand(step), files: stepFiles(step) } : step;
    const payload = { name: name.trim(), source, ...(presetId != null ? { preset_id: presetId } : {}), ...(reuse ? { space_id: spaceId } : { resource: { ...resource, min_memory_gib: 0 } }), retain_minutes: retainMinutes, environments: reuse ? [] : environments.map(env => ({ ...env, node_alias: undefined, node_aliases: env.node_aliases || [env.node_alias], install: env.install.map(serialize), verify: env.verify.map(serialize) })), jobs: jobs.map(job => ({ ...job, pre: job.pre.map(serialize), steps: job.steps.map(serialize), post: job.post.map(serialize), ready: job.ready.map(serialize) })) };
    if (deriving) {
      if (presetId == null) { setError('请先载入一个预置用例。'); return; }
      if (action === 'derive-check') { setDeriveName(`${name || presetName} · 副本`); setDeriveOpen(true); return; }
      if (!deriveName.trim()) { setError('请输入新用例名称。'); return; }
      if (reuse && (!selectedSpace?.spec.resource || !selectedSpace.spec.environments?.length)) { setError('该空间缺少可重建的环境快照，请先选择新申请环境再另存。'); return; }
      const { preset_id: _parent, ...currentWorkflow } = payload;
      const workflow = reuse ? { ...currentWorkflow, space_id: undefined, resource: { ...defaultResource(), ...selectedSpace!.spec.resource, min_memory_gib: 0 }, environments: environments.map(env => ({ ...env, node_alias: undefined, node_aliases: env.node_aliases || [env.node_alias], install: env.install.map(serialize), verify: env.verify.map(serialize) })) } : currentWorkflow;
      setBusy(true);
      try { const created = await post<WorkflowPreset>(`/presets/${presetId}/derive`, { name: deriveName.trim(), tags: presetTags, workflow }); setPresetId(created.id); setPresetName(created.name); setDeriveOpen(false); setNotice(`已另存为新用例「${created.name}」。这是尚未验证的个人用例，原用例保持不变。`); }
      catch (err) { setError(errorText(err)); } finally { setBusy(false); }
      return;
    }
    const signature = JSON.stringify(payload);
    setBusy(true);
    try { if (signature !== submission.current.signature) submission.current = { signature, key: operationKey() }; const result = await post<WorkflowRun>('/workflows', { ...payload, idempotency_key: submission.current.key }); setNotice(`任务「${result.name}」已提交，资源和环境状态将在下方更新。`); submission.current = { signature: '', key: '' }; onSubmitted(); }
    catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return <section className="panel workflow-composer section-gap"><div className="panel-title"><span className="title-icon"><Icon name="task" /></span><div><h2>新建容器任务</h2><p>固定代码版本、独立环境与作业依赖</p></div></div><form ref={formRef} noValidate onSubmit={event => void submit(event)}><div className="panel-body">{!canRequest && <p className="notice">尚未获得服务器申请权限，请联系管理员开启任务提交。</p>}<WorkflowPresets admin={user.admin} source={source} onApply={preset => { if (preset.loadable === false || !preset.workflow) return; setError(''); try { const snapshot = structuredClone(preset.workflow); const nextJobs = presetJobs(snapshot.jobs || []); let nextEnvironments: WorkflowEnvironment[] | undefined; if (snapshot.environments && !reuse) { if (!Array.isArray(snapshot.environments)) throw new Error('预置环境列表格式无效。'); nextEnvironments = snapshot.environments.map(env => { if (!env || typeof env.alias !== 'string' || env.role && !['server', 'client'].includes(env.role)) throw new Error('预置环境代称或角色无效。'); return { ...environment(env.alias, env.role || 'server'), ...env, install: presetSteps(env.install), verify: presetSteps(env.verify), packages: env.packages || [], environment: env.environment || {} }; }); } setPresetId(preset.id); setPresetName(preset.name); setPresetTags(Object.fromEntries(Object.entries(preset.tags || {}).map(([key,value]) => [key, Array.isArray(value) ? value.join(' / ') : value]))); const pinned = snapshot.source || preset.source; if (pinned) { setSource(pinned); const reference = pinned.revision === 'commit' || pinned.pr == null ? pinned.commit || pinned.head_sha : String(pinned.pr); setPr(reference); latestPr.current = reference; } setSelectedEnvironment(0); setSelectedJobId(nextJobs[0]?.id); setName(snapshot.name || preset.name); setJobs(nextJobs); if (nextEnvironments) setEnvironments(nextEnvironments); if (snapshot.resource && !reuse) setResource({ ...defaultResource(), ...snapshot.resource, min_memory_gib: 0 }); setNotice(`已载入「${preset.name}」的外部执行配置，请确认代码版本与环境输入后提交。`); } catch (err) { setError(errorText(err)); } }} />
    <div className="form-grid"><Field label="任务名称"><input required maxLength={128} value={name} onChange={e => setName(e.target.value)} /></Field><Field label="vLLM-Ascend PR / commit" hint="填写 PR 编号、该仓库 PR 链接，或完整的 40 位 commit SHA。"><div className="workflow-inline"><input aria-label="vLLM-Ascend PR / commit" required value={pr} onChange={e => { setPr(e.target.value); latestPr.current = e.target.value; setSource(undefined); }} /><button type="button" className="button secondary small-button" disabled={resolving || !pr.trim()} onClick={() => void resolveSource()}>{resolving ? '解析中…' : '解析 PR / commit'}</button></div></Field></div>
    {source && <dl className="detail-list source-snapshot"><dt>执行代码 SHA</dt><dd><code>{source.head_sha}</code></dd><dt>匹配 vLLM SHA</dt><dd><code>{source.vllm_sha}</code></dd></dl>}
    <div className="workflow-lifecycle"><div><strong>环境从哪里来</strong><div className="choice-buttons" role="group" aria-label="环境来源"><button type="button" aria-pressed={!reuse} onClick={() => { if (!reuse) return; setReuse(false); setSpaceId(''); setSelectedEnvironment(0); setEnvironments([environment('env1', 'server'), environment('env2', 'server')]); }}>新申请环境</button><button type="button" aria-pressed={reuse} onClick={() => setReuse(true)}>复用已有环境</button></div><p className="muted small">{reuse ? '选择本人保留的环境，继续使用已有容器，不重复申请机器。' : '按上方资源规格申请机器，再为每个环境创建独立容器。'}</p>{reuse && <><div className="choice-buttons" role="group" aria-label="运行空间">{spaces.data?.filter(space => (space.owner_name === user.username || user.admin) && !['CLOSED', 'CLOSING', 'FAILED', 'EXPIRED'].includes(space.status)).map(space => <button type="button" key={space.id} aria-pressed={spaceId === String(space.id)} onClick={() => { chooseSpace(String(space.id)); setSelectedEnvironment(0); }}>#{space.id} · {space.status}</button>)}</div><ErrorNotice text={spaces.error} /></>}</div><div><strong>任务结束后</strong><div className="choice-buttons" role="group" aria-label="任务结束后"><button type="button" aria-pressed={retainMinutes === 0} onClick={() => setRetainMinutes(0)}>结束即释放</button><button type="button" aria-pressed={retainMinutes > 0} onClick={() => setRetainMinutes(value => value || 60)}>保留环境</button></div><p className="muted small">{retainMinutes > 0 ? '保留容器与资源，方便继续调试或再次提交任务；到期后统一释放。' : '任务结束并完成清理核验后，关闭容器、归还资源。'}</p>{retainMinutes > 0 && <Field label="保留时长（分钟）"><input type="number" min={1} max={10080} required value={retainMinutes} onChange={event => setRetainMinutes(Number(event.target.value))} /></Field>}</div></div>
    <section className="workflow-block" aria-label="环境配置"><div className="workflow-block-heading"><div><span className="eyebrow">ENVIRONMENTS</span><h3>环境配置</h3></div>{!reuse && <button type="button" className="button secondary small-button" onClick={() => { let index = environments.length + 1; while (environments.some(value => value.alias === `env${index}`)) index += 1; setEnvironments(values => [...values, { ...environment(`env${index}`, 'server'), node_alias: Array.from({ length: resource.machine_count }, (_, i) => `node${i}`).find(node => !values.some(value => value.node_alias === node)) || 'node0' }]); setSelectedEnvironment(environments.length); }}>添加独立环境</button>}</div><p className="muted small">每个环境独立配置镜像和安装步骤；同一台服务器可以创建多个容器。</p><div className="workflow-tabs" role="tablist" aria-label="环境列表">{environments.map((env, index) => <button type="button" role="tab" key={index} id={`env-tab-${index}`} aria-controls={`env-panel-${index}`} aria-selected={selectedEnvironment === index} onClick={() => setSelectedEnvironment(index)}>{env.alias || `环境 ${index + 1}`}</button>)}</div><div className="workflow-environments">{environments.map((env, index) => <div key={index} role="tabpanel" id={`env-panel-${index}`} aria-labelledby={`env-tab-${index}`} data-environment-index={index} hidden={selectedEnvironment !== index}>{reuse ? <div className="workflow-environment"><h4>{env.alias}</h4><p>{(env.node_aliases || [env.node_alias]).join(' / ')}</p><p className="muted small">使用空间已有环境，安装输入与容器身份由中心重新核验。</p></div> : <WorkflowEnvironmentEditor value={env} nodes={Array.from({ length: resource.machine_count }, (_, i) => `node${i}`)} onReading={onReading} onChange={next => updateEnvironment(index, next)} onDelete={() => { if (jobs.some(job => job.environment === env.alias || job.artifacts?.some(item => item.environment === env.alias || item.targets?.some(target => target.environment === env.alias)))) { setError(`环境 ${env.alias} 仍被作业引用，请先修改作业目标。`); return; } setEnvironments(values => values.filter((_, i) => i !== index)); setSelectedEnvironment(Math.max(0, index - 1)); }} />}</div>)}</div></section>
    <section className="workflow-block" aria-label="Jobs 配置"><div className="workflow-block-heading"><div><span className="eyebrow">JOBS</span><h3>作业配置</h3></div><button type="button" className="button secondary small-button" onClick={() => { const next = Math.max(0, ...jobs.map(job => Number(job.id.replace('job', '')) || 0)) + 1; setJobs(values => [...values, emptyJob(next, environments[0]?.alias || '')]); setSelectedJobId(`job${next}`); }}>添加作业</button></div><p className="muted small">用 <code>{'${node0.ip}'}</code>、<code>{'${node1.ip}'}</code> 等引用申请到的各节点 IP；<code>{'${host}'}</code> 是当前执行节点 IP，<code>{'${container_name}'}</code> 是当前真实容器名。同一环境选择多个节点时，会在各节点创建独立容器，并分别执行同一启动命令。</p><details className="workflow-env-details"><summary>查看节点变量与脚本示例</summary><dl className="detail-list source-snapshot"><dt>本次节点变量</dt><dd>{Array.from({ length: reuse ? selectedSpace?.spec.resource?.machine_count || resource.machine_count : resource.machine_count }, (_, index) => <code key={index} style={{ display: 'inline-block', marginRight: 12 }}>{'${node' + index + '.ip}'}</code>)}</dd><dt>当前执行位置</dt><dd><code>{'${host}'}</code> / <code>{'${container_name}'}</code> 随正在执行的节点和容器变化。</dd><dt>当前节点资源映射</dt><dd><code>hive_resource model 模型名称</code> 查询当前执行节点的模型路径；同一逻辑名称在不同节点可对应不同路径。可将 <code>model</code> 换成 <code>dataset</code>、<code>image</code> 或 <code>package</code>。</dd></dl><p className="muted small">Bash：各节点使用 HIVE_NODE0_IP、HIVE_NODE1_IP 等变量，编号从 0 开始，仅包含本次申请的节点。</p><pre className="source-snapshot" style={{ whiteSpace: 'pre-wrap' }}>{'echo "$HIVE_NODE0_IP"\n# 申请至少两台机器时：echo "$HIVE_NODE1_IP"\necho "当前节点：$HIVE_HOST_IP，当前容器：$HIVE_CONTAINER_NAME"\nmodel_path=$(hive_resource model 模型名称)'}</pre><p className="muted small">Python：HIVE_NODES_JSON 包含所有已申请节点；当前执行位置通过独立变量读取。</p><pre className="source-snapshot" style={{ whiteSpace: 'pre-wrap' }}>{'import json, os\nfor node in json.loads(os.environ["HIVE_NODES_JSON"]):\n    print(node["node_alias"], node["ip"])\nprint(os.environ["HIVE_HOST_IP"], os.environ["HIVE_CONTAINER_NAME"])'}</pre></details><WorkflowGraph jobs={jobs} onChange={setJobs} selectedJobId={selectedJobId || jobs[0]?.id} onSelect={id => { setSelectedJobId(id); requestAnimationFrame(() => { const editor = document.getElementById(`editor-${id}`); editor?.scrollIntoView({ behavior: 'smooth', block: 'center' }); editor?.querySelector<HTMLInputElement>('input')?.focus({ preventScroll: true }); }); }} /><div className="workflow-tabs" role="tablist" aria-label="作业列表">{jobs.map(job => <button type="button" role="tab" id={`job-tab-${job.id}`} aria-controls={`job-panel-${job.id}`} key={job.id} aria-selected={(selectedJobId || jobs[0]?.id) === job.id} onClick={() => setSelectedJobId(job.id)}>{job.name || job.id}</button>)}</div>{jobs.map((job, index) => <div key={job.id} role="tabpanel" id={`job-panel-${job.id}`} data-job-id={job.id} aria-labelledby={`job-tab-${job.id}`} hidden={(selectedJobId || jobs[0]?.id) !== job.id}><WorkflowJobEditor job={job} jobs={jobs} environments={environments} onReading={onReading} onChange={next => setJobs(values => values.map((value, i) => ({ ...(i === index ? { ...value, ...next } : value), depends_on: next.kind ? value.depends_on.map(dep => dep.job_id === job.id ? { ...dep, condition: next.kind === 'service' ? 'ready' as const : 'succeeded' as const } : dep) : value.depends_on })))} onDelete={() => { if (jobs.some(value => value.depends_on.some(dep => dep.job_id === job.id))) { setError(`作业 ${job.id} 仍被其他作业依赖，请先删除连线。`); return; } setJobs(values => values.filter(value => value.id !== job.id)); setSelectedJobId(jobs.find(value => value.id !== job.id)?.id); }} /></div>)}</section>
    <ErrorNotice text={error} />{notice && <div className="notice success" role="status">{notice}</div>}
  </div><div className="form-footer"><span className="muted small" role="status">{draftSaved ? '草稿已保存到此浏览器' : '正在保存草稿…'}</span><button ref={deriveCheck} hidden type="submit" data-action="derive-check" /><button ref={deriveSubmit} hidden type="submit" data-action="derive" />{presetId != null && <button type="button" className="button secondary" disabled={busy || resolving || pendingUploads > 0} onClick={() => formRef.current?.requestSubmit(deriveCheck.current!)}>另存为新用例</button>}<button className="button primary" disabled={busy || resolving || pendingUploads > 0 || !canRequest}>{busy ? '正在提交…' : '提交任务申请'}<Icon name="arrow" size={16} /></button></div></form>{deriveOpen && <Modal title="另存为新用例" onClose={() => setDeriveOpen(false)}><div className="modal-body"><p className="muted small">保存当前参数为个人用例，原用例保持不变；保存不会申请机器或运行任务。</p><Field label="新用例名称"><input autoFocus required maxLength={128} value={deriveName} onChange={event => setDeriveName(event.target.value)} /></Field><ErrorNotice text={error} /></div><div className="modal-actions"><button className="button secondary" onClick={() => setDeriveOpen(false)}>返回</button><button className="button primary" disabled={busy || !deriveName.trim()} onClick={() => formRef.current?.requestSubmit(deriveSubmit.current!)}>确认另存</button></div></Modal>}</section>;
}
