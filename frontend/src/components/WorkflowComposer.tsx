import { useEffect, useRef, useState, type FormEvent } from 'react';
import { errorText, operationKey, post } from '../api';
import type { WorkflowEnvironment, WorkflowJob, WorkflowRun, WorkflowSource, WorkflowStep, WorkflowPreset } from '../workflowTypes';
import { artifactTargets, environmentTargets } from '../workflowArtifacts';
import type { ResourceSpec, User } from '../types';
import type { WorkflowDraft } from '../workflowDrafts';
import { defaultResource } from './ResourceForm';
import { WorkflowVariables } from './WorkflowVariables';
import { WorkflowPresets } from './WorkflowPresets';
import { WorkflowGraph } from './WorkflowGraph';
import { WorkflowJobEditor } from './WorkflowJobEditor';
import { stepCommand, stepFiles, WorkflowFileContext, WorkflowTaskFiles } from './WorkflowFiles';
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

function draftEnvironments(draft?: WorkflowDraft): WorkflowEnvironment[] {
  if (!draft) return [environment('env1','server'),environment('env2','server')];
  const values = (draft.environments || []).map(environmentConfig);
  for (const job of draft.jobs || []) if (/^[a-z][a-z0-9_]{0,31}$/.test(job.environment) && !values.some(env => env.alias === job.environment)) values.push(environment(job.environment,'server'));
  return values.length ? values : [environment('env1','server')];
}

export function WorkflowComposer({ onSubmitted, user, resource, onResourceChange: setResource, validateResource, initialDraft, onDraftChange, draftSaved }: { onSubmitted: () => void; user: User; resource: ResourceSpec; onResourceChange: (value: ResourceSpec) => void; validateResource: () => boolean; initialDraft?: WorkflowDraft; onDraftChange: (value: WorkflowDraft) => void; draftSaved: boolean }) {
  const canRequest = user.admin || Boolean(user.can_request);
  const [name, setName] = useState(() => { if (initialDraft) return initialDraft.name; const now = new Date(); const pad = (n: number) => String(n).padStart(2, '0'); return `任务-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`; });
  const [selectedEnvironment, setSelectedEnvironment] = useState(Math.min(initialDraft?.selectedEnvironment || 0,draftEnvironments(initialDraft).length-1));
  const [selectedJobId, setSelectedJobId] = useState<string | undefined>(initialDraft?.selectedJobId);
  const [presetId, setPresetId] = useState<string | number | undefined>(initialDraft?.presetId);
  const [presetYamlPath, setPresetYamlPath] = useState(initialDraft?.presetYamlPath);
  const [pr, setPr] = useState(initialDraft?.pr?.trim() ? initialDraft.pr : 'main');
  const latestPr = useRef(initialDraft?.pr?.trim() ? initialDraft.pr : 'main');
  const [source, setSource] = useState<WorkflowSource | undefined>(initialDraft?.pr?.trim() ? initialDraft.source : undefined);
  const [environments, setEnvironments] = useState(() => draftEnvironments(initialDraft));
  const [jobs, setJobs] = useState(() => initialDraft?.jobs?.length ? presetJobs(initialDraft.jobs).map(job => ({...job,environment: draftEnvironments(initialDraft).some(env => env.alias === job.environment) ? job.environment : draftEnvironments(initialDraft)[0].alias})) : [emptyJob(1,draftEnvironments(initialDraft)[0].alias)]);

  const sourceRequest = useRef(0);
  const allSteps = [...environments.flatMap(env => [env.bootstrap, ...env.install, ...env.verify]), ...jobs.flatMap(job => [...job.pre, ...job.steps, ...job.ready, ...job.post])];
  const sharedFiles = [...new Map(allSteps.flatMap(stepFiles).map(file => [file.name, file])).values()];
  const [legacyMissingScripts,setLegacyMissingScripts] = useState(initialDraft?.legacyMissingScripts ?? Boolean(initialDraft && initialDraft.formatVersion !== 2 && !sharedFiles.length));
  const saveSharedFile = (name: string, content: string) => {
    const update = (step: WorkflowStep): WorkflowStep => stepFiles(step).some(file => file.name === name) ? { ...step, path: '', launch: stepCommand(step), files: stepFiles(step).map(file => file.name === name ? { ...file, content } : file), uploaded_content: undefined, inputs: undefined } : step;
    setEnvironments(values => values.map(env => ({ ...env, bootstrap: update(env.bootstrap), install: env.install.map(update), verify: env.verify.map(update) })));
    setJobs(values => values.map(job => ({ ...job, pre: job.pre.map(update), steps: job.steps.map(update), ready: job.ready.map(update), post: job.post.map(update) })));
  };
  const [pendingUploads, setPendingUploads] = useState(0);
  const onReading = (delta: number) => setPendingUploads(value => value + delta);
  const [busy, setBusy] = useState(false), [resolving, setResolving] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  const formRef = useRef<HTMLFormElement>(null), deriveCheck = useRef<HTMLButtonElement>(null), deriveSubmit = useRef<HTMLButtonElement>(null);
  const [deriveOpen, setDeriveOpen] = useState(false), [deriveName, setDeriveName] = useState('');
  const [presetName, setPresetName] = useState(initialDraft?.presetName || ''), [presetTags, setPresetTags] = useState<Record<string,string>>(initialDraft?.presetTags || {});
  const submission = useRef({ signature: '', key: '' });
  useEffect(() => { onDraftChange({ name, pr, source, environments, jobs, reuse:false, spaceId:'', retainMinutes:0, formatVersion:2, legacyMissingScripts, presetId, presetName, presetTags, presetYamlPath, selectedEnvironment, selectedJobId }); }, [name, pr, source, environments, jobs, legacyMissingScripts, presetId, presetName, presetTags, presetYamlPath, selectedEnvironment, selectedJobId, onDraftChange]);
  const resolveReference = async (requested: string, preset?: Pick<WorkflowPreset, 'origin' | 'scope' | 'yaml_path'>) => {
    const token = ++sourceRequest.current; setSource(undefined); setResolving(true); setError('');
    try { const resolved = await post<WorkflowSource>('/sources/resolve', requested === 'main' ? { branch: 'main' } : /^[a-fA-F0-9]{40}$/.test(requested) ? { commit: requested.toLowerCase() } : { pr: requested }); if (token !== sourceRequest.current || latestPr.current.trim() !== requested) return; if (preset?.origin === 'archive' && preset.scope !== 'personal' && preset.yaml_path) { const file = await post<{content:string}>('/sources/file', {source:resolved,path:preset.yaml_path}); if (token !== sourceRequest.current || latestPr.current.trim() !== requested) return; if (typeof file.content !== 'string') throw new Error('最新 nightly YAML 获取失败，请重新载入预置。'); saveSharedFile(preset.yaml_path,file.content); } setSource(resolved); }
    catch (err) { if (token === sourceRequest.current && latestPr.current.trim() === requested) setError(errorText(err)); } finally { if (token === sourceRequest.current) setResolving(false); }
  };
  const resolveSource = () => { if (!resolving && pr.trim()) void resolveReference(pr.trim(), presetYamlPath ? {origin:'archive',yaml_path:presetYamlPath} : undefined); };
  const updateEnvironment = (index: number, next: Partial<WorkflowEnvironment>) => { const old = environments[index].alias; if (next.alias !== undefined && next.alias !== old) setJobs(values => values.map(job => ({ ...job, environment: job.environment === old ? next.alias! : job.environment, artifacts: job.artifacts?.map(item => ({ ...item, environment: item.environment === old ? next.alias : item.environment, targets: item.targets?.map(target => ({ ...target, environment: target.environment === old ? next.alias! : target.environment })) })) }))); setEnvironments(values => values.map((value, i) => i === index ? { ...value, ...next } : value)); };
  const submit = async (event: FormEvent) => {
    event.preventDefault(); const action = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('data-action'); const deriving = action === 'derive-check' || action === 'derive'; if (busy || !canRequest && !deriving || !validateResource()) return;
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
    if (!source) { setError('请先解析 PR 或 commit，固定执行代码版本。'); return; }
    const aliases = new Set(environments.map(env => env.alias));
    if (!jobs.length || !environments.length) { setError('至少配置一个环境和一个作业。'); return; }
    if (aliases.size !== environments.length || environments.some(env => { const nodes = env.node_aliases || [env.node_alias]; return !nodes.length || nodes.some(node => !/^node\d+$/.test(node) || Number(node.slice(4)) >= resource.machine_count); })) { setError('环境代称不能重复，且节点代称必须在当前申请数量内。'); return; }
    if (jobs.some(job => !aliases.has(job.environment) || !job.steps.length || job.kind === 'service' && !job.ready.length)) { setError('请检查作业目标环境、主执行入口和服务就绪检查。'); return; }
    if (jobs.some(job => job.artifacts?.some(item => !artifactTargets(item, job, environments).length))) { setError('每个产物至少选择一个目标容器与节点。'); return; }
    const paths = jobs.flatMap(job => [...job.pre, ...job.steps, ...job.post, ...job.ready]).concat(environments.flatMap(env => [env.bootstrap, ...env.install, ...env.verify]));
    if (paths.some(step => !stepCommand(step).trim() && !step.files?.length)) { setError('请上传执行文件或填写启动命令。'); return; }
    const serialize = (step: WorkflowStep) => !step.path || step.launch?.trim() || step.files?.length ? { launch: stepCommand(step), files: stepFiles(step) } : step;
    const payload = { name: name.trim(), source, ...(presetId != null ? { preset_id: presetId } : {}), resource: { ...resource, min_memory_gib: 0 }, retain_minutes: 0, environments: environments.map(env => ({ ...env, node_alias: undefined, node_aliases: env.node_aliases || [env.node_alias], bootstrap: serialize(env.bootstrap), install: env.install.map(serialize), verify: env.verify.map(serialize) })), jobs: jobs.map(job => ({ ...job, artifacts: job.artifacts?.map(item => ({ ...item, environment: undefined, targets: artifactTargets(item, job, environments) })), pre: job.pre.map(serialize), steps: job.steps.map(serialize), post: job.post.map(serialize), ready: job.ready.map(serialize) })) };
    if (deriving) {
      if (presetId == null) { setError('请先载入一个预置用例。'); return; }
      if (action === 'derive-check') { setDeriveName(`${name || presetName} · 副本`); setDeriveOpen(true); return; }
      if (!deriveName.trim()) { setError('请输入新用例名称。'); return; }
      const { preset_id: _parent, ...workflow } = payload;
      setBusy(true);
      try { const created = await post<WorkflowPreset>(`/presets/${presetId}/derive`, { name: deriveName.trim(), tags: presetTags, workflow }); setPresetId(created.id); setPresetYamlPath(undefined); setPresetName(created.name); setDeriveOpen(false); setNotice(`已另存为新用例「${created.name}」。这是尚未验证的个人用例，原用例保持不变。`); }
      catch (err) { setError(errorText(err)); } finally { setBusy(false); }
      return;
    }
    const signature = JSON.stringify(payload);
    setBusy(true);
    try { if (signature !== submission.current.signature) submission.current = { signature, key: operationKey() }; const result = await post<WorkflowRun>('/workflows', { ...payload, idempotency_key: submission.current.key }); setNotice(`任务「${result.name}」已提交，资源和环境状态将在下方更新。`); submission.current = { signature: '', key: '' }; onSubmitted(); }
    catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return <WorkflowFileContext.Provider value={{files: sharedFiles, save: saveSharedFile}}><section className="panel workflow-composer section-gap"><div className="panel-title"><span className="title-icon"><Icon name="task" /></span><div><h2>新建容器任务</h2><p>固定代码版本、独立环境与作业依赖</p></div></div><form ref={formRef} noValidate onSubmit={event => void submit(event)}><div className="panel-body">{!canRequest && <p className="notice">尚未获得服务器申请权限，请联系管理员开启任务提交。</p>}<WorkflowPresets admin={user.admin} source={source} onApply={preset => { if (preset.loadable === false || !preset.workflow) return; setError(''); try { const snapshot = structuredClone(preset.workflow); let nextJobs = presetJobs(snapshot.jobs || []); let nextEnvironments: WorkflowEnvironment[] | undefined; if (snapshot.environments) { if (!Array.isArray(snapshot.environments)) throw new Error('预置环境列表格式无效。'); nextEnvironments = snapshot.environments.map(env => { if (!env || typeof env.alias !== 'string' || env.role && !['server', 'client'].includes(env.role)) throw new Error('预置环境代称或角色无效。'); return { ...environment(env.alias, env.role || 'server'), ...env, install: presetSteps(env.install), verify: presetSteps(env.verify), packages: env.packages || [], environment: env.environment || {} }; }); } { const baseline = nextEnvironments || environments; nextJobs = nextJobs.map(job => ({...job, artifacts: job.artifacts?.map(item => { const expected = environmentTargets(baseline, job.environment); const matchesDefault = item.targets?.length === expected.length && expected.every(target => item.targets?.some(value => value.environment === target.environment && value.node_alias === target.node_alias)); return matchesDefault ? {...item,targets:undefined,environment:undefined} : item; })})); } setLegacyMissingScripts(false); setPresetYamlPath(preset.origin === 'archive' && preset.scope !== 'personal' ? preset.yaml_path : undefined); setPresetId(preset.id); setPresetName(preset.name); setPresetTags(Object.fromEntries(Object.entries(preset.tags || {}).map(([key,value]) => [key, Array.isArray(value) ? value.join(' / ') : value]))); setSource(undefined); setPr('main'); latestPr.current = 'main'; void resolveReference('main',preset); setSelectedEnvironment(0); setSelectedJobId(nextJobs[0]?.id); setName(snapshot.name || preset.name); setJobs(nextJobs); if (nextEnvironments) setEnvironments(nextEnvironments); if (snapshot.resource) setResource({ ...defaultResource(), ...snapshot.resource, min_memory_gib: 0 }); setNotice(`已载入「${preset.name}」的外部执行配置，请确认代码版本与环境输入后提交。`); } catch (err) { setError(errorText(err)); } }} />
    <div className="form-grid"><Field label="任务名称"><input required maxLength={128} value={name} onChange={e => setName(e.target.value)} /></Field><Field label="vLLM-Ascend PR / commit" hint="默认 main；也可填写 PR 编号、PR 链接或完整的 40 位 commit SHA。"><div className="workflow-inline"><input aria-label="vLLM-Ascend PR / commit" required value={pr} onChange={e => { setPr(e.target.value); latestPr.current = e.target.value; setSource(undefined); }} /><button type="button" className="button secondary small-button" disabled={resolving || !pr.trim()} onClick={() => void resolveSource()}>{resolving ? '解析中…' : '解析 PR / commit'}</button></div></Field></div>
    {source && <dl className="detail-list source-snapshot"><dt>执行代码 SHA</dt><dd><code>{source.head_sha}</code></dd><dt>匹配 vLLM SHA</dt><dd><code>{source.vllm_sha}</code></dd></dl>}
    <p className="notice compact">每次任务按上方规格申请新资源，执行结束并完成清理后释放。</p>
    {legacyMissingScripts && !sharedFiles.length && <div className="notice"><p>旧版草稿未包含脚本，请重新载入预置</p><p className="muted small">已保留当前作业改动，载入预置会替换当前配置。</p><button type="button" className="text-button" onClick={() => {const panel=formRef.current?.querySelector<HTMLDetailsElement>('.workflow-presets');if(panel){panel.open=true;panel.scrollIntoView({block:'center'});}}}>查看可载入预置</button></div>}
    <section className="workflow-block" aria-label="环境配置"><div className="workflow-block-heading"><div><span className="eyebrow">ENVIRONMENTS</span><h3>环境配置</h3></div><button type="button" className="button secondary small-button" onClick={() => { let index = environments.length + 1; while (environments.some(value => value.alias === `env${index}`)) index += 1; setEnvironments(values => [...values, { ...environment(`env${index}`, 'server'), node_alias: Array.from({ length: resource.machine_count }, (_, i) => `node${i}`).find(node => !values.some(value => value.node_alias === node)) || 'node0' }]); setSelectedEnvironment(environments.length); }}>添加独立环境</button></div><p className="muted small">每个环境独立配置镜像和安装步骤；同一台服务器可以创建多个容器。</p><div className="workflow-tabs" role="tablist" aria-label="环境列表">{environments.map((env, index) => <button type="button" role="tab" key={index} id={`env-tab-${index}`} aria-controls={`env-panel-${index}`} aria-selected={selectedEnvironment === index} onClick={() => setSelectedEnvironment(index)}>{env.alias || `环境 ${index + 1}`}</button>)}</div><div className="workflow-environments">{environments.map((env, index) => <div key={index} role="tabpanel" id={`env-panel-${index}`} aria-labelledby={`env-tab-${index}`} data-environment-index={index} hidden={selectedEnvironment !== index}>{<WorkflowEnvironmentEditor value={env} nodes={Array.from({ length: resource.machine_count }, (_, i) => `node${i}`)} onReading={onReading} onChange={next => updateEnvironment(index, next)} onDelete={() => { if (jobs.some(job => job.environment === env.alias || job.artifacts?.some(item => item.environment === env.alias || item.targets?.some(target => target.environment === env.alias)))) { setError(`环境 ${env.alias} 仍被作业引用，请先修改作业目标。`); return; } setEnvironments(values => values.filter((_, i) => i !== index)); setSelectedEnvironment(Math.max(0, index - 1)); }} />}</div>)}</div></section>
    <WorkflowTaskFiles files={sharedFiles} />
    <section className="workflow-block" aria-label="Jobs 配置"><div className="workflow-block-heading"><div><span className="eyebrow">JOBS</span><h3>作业配置</h3></div><button type="button" className="button secondary small-button" onClick={() => { const next = Math.max(0, ...jobs.map(job => Number(job.id.replace('job', '')) || 0)) + 1; setJobs(values => [...values, emptyJob(next, environments[0]?.alias || '')]); setSelectedJobId(`job${next}`); }}>添加作业</button></div><WorkflowVariables nodeCount={resource.machine_count} environments={environments} /><WorkflowGraph jobs={jobs} onChange={setJobs} selectedJobId={selectedJobId || jobs[0]?.id} onSelect={id => { setSelectedJobId(id); requestAnimationFrame(() => { const editor = document.getElementById(`editor-${id}`); editor?.scrollIntoView({ behavior: 'smooth', block: 'center' }); editor?.querySelector<HTMLInputElement>('input')?.focus({ preventScroll: true }); }); }} /><div className="workflow-tabs" role="tablist" aria-label="作业列表">{jobs.map((job,index) => <button type="button" role="tab" id={`job-tab-${job.id}`} aria-controls={`job-panel-${job.id}`} key={job.id} aria-selected={(selectedJobId || jobs[0]?.id) === job.id} onClick={() => setSelectedJobId(job.id)}>{`job${index + 1}`}</button>)}</div>{jobs.map((job, index) => <div key={job.id} role="tabpanel" id={`job-panel-${job.id}`} data-job-id={job.id} aria-labelledby={`job-tab-${job.id}`} hidden={(selectedJobId || jobs[0]?.id) !== job.id}><WorkflowJobEditor job={job} jobs={jobs} environments={environments} onReading={onReading} onChange={next => setJobs(values => values.map((value, i) => ({ ...(i === index ? { ...value, ...next } : value), depends_on: next.kind ? value.depends_on.map(dep => dep.job_id === job.id ? { ...dep, condition: next.kind === 'service' ? 'ready' as const : 'succeeded' as const } : dep) : value.depends_on })))} onDelete={() => { if (jobs.some(value => value.depends_on.some(dep => dep.job_id === job.id))) { setError(`作业 ${job.id} 仍被其他作业依赖，请先删除连线。`); return; } setJobs(values => values.filter(value => value.id !== job.id)); setSelectedJobId(jobs.find(value => value.id !== job.id)?.id); }} /></div>)}</section>
    <ErrorNotice text={error} />{notice && <div className="notice success" role="status">{notice}</div>}
  </div><div className="form-footer"><span className="muted small" role="status">{draftSaved ? '草稿已保存到此浏览器' : '正在保存草稿…'}</span><button ref={deriveCheck} hidden type="submit" data-action="derive-check" /><button ref={deriveSubmit} hidden type="submit" data-action="derive" />{presetId != null && <button type="button" className="button secondary" disabled={busy || resolving || pendingUploads > 0} onClick={() => formRef.current?.requestSubmit(deriveCheck.current!)}>另存为新用例</button>}<button className="button primary" disabled={busy || resolving || pendingUploads > 0 || !canRequest || !source}>{busy ? '正在提交…' : '提交任务申请'}<Icon name="arrow" size={16} /></button></div></form>{deriveOpen && <Modal title="另存为新用例" onClose={() => setDeriveOpen(false)}><div className="modal-body"><p className="muted small">保存当前参数为个人用例，原用例保持不变；保存不会申请机器或运行任务。</p><Field label="新用例名称"><input autoFocus required maxLength={128} value={deriveName} onChange={event => setDeriveName(event.target.value)} /></Field><ErrorNotice text={error} /></div><div className="modal-actions"><button className="button secondary" onClick={() => setDeriveOpen(false)}>返回</button><button className="button primary" disabled={busy || !deriveName.trim()} onClick={() => formRef.current?.requestSubmit(deriveSubmit.current!)}>确认另存</button></div></Modal>}</section></WorkflowFileContext.Provider>;
}
