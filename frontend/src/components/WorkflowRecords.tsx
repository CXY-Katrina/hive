import { useEffect, useState } from 'react';
import { errorText, post } from '../api';
import { useQuery } from '../hooks';
import type { User } from '../types';
import type { WorkflowRun, WorkflowSpace } from '../workflowTypes';
import { Badge, dateTime, Empty, ErrorNotice, Loading, Modal, shortId } from './ui';
import { WorkflowEvidence } from './WorkflowEvidence';
import { WorkflowGraph } from './WorkflowGraph';

const terminal = ['SUCCEEDED', 'FAILED', 'CANCELLED', 'CLOSED', 'EXPIRED'];
const knownJobStates = ['PENDING', 'QUEUED', 'READY', 'RUNNING', 'PREPARING', 'COLLECTING', 'CANCELLING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'SKIPPED'];
function PendingStatus({ status, job = false }: { status: string; job?: boolean }) {
  if (status === 'CANCELLING') return <p className="notice compact">取消处理中，等待所属进程退出与收尾复核，尚未确认资源归还。</p>;
  if (job && !knownJobStates.includes(status)) return <p className="notice compact">作业状态待核验，尚未确认执行结束；请查看原因与日志。</p>;
  return null;
}
function runStatus(run: WorkflowRun) {
  const preparing = run.status === 'PREPARING' || ['QUEUED', 'PENDING'].includes(run.status) && run.space_status === 'PREPARING';
  if (preparing) {
    const phases: Record<string, string> = { CHECKOUT: '获取源码', BOOTSTRAPPING: '创建容器', INSTALL: '安装依赖', VERIFY: '核验环境' };
    const phase = phases[run.preparation_phase || ''];
    return { value: 'PREPARING', label: phase ? `环境准备中 · ${phase}` : '环境准备中' };
  }
  return { value: run.status, label: run.status === 'SUCCEEDED' ? '执行成功' : run.status === 'QUEUED' ? '排队中' : undefined };
}
export function WorkflowRecords({ refresh, user }: { refresh: number; user: User }) {
  const query = useQuery<WorkflowRun[]>('/workflows', 10000);
  const spaces = useQuery<WorkflowSpace[]>('/spaces', 15000);
  const [selected, setSelected] = useState<WorkflowRun>();
  const [action, setAction] = useState<{ kind: 'cancel' | 'close'; id: string | number; name: string }>();
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  useEffect(() => { if (refresh) { void query.reload(); void spaces.reload(); } }, [refresh, query.reload, spaces.reload]);
  const act = async () => {
    if (!action || busy) return; setBusy(true); setError('');
    try { await post(action.kind === 'cancel' ? `/workflows/${action.id}/cancel` : `/spaces/${action.id}/close`); setNotice(action.kind === 'cancel' ? '取消已提交，等待作业退出与状态复核。' : '关闭已提交，等待所属进程与容器退出后统一归还资源。'); setAction(undefined); void query.reload(); void spaces.reload(); }
    catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return <><section className="panel section-gap" aria-label="任务记录"><div className="panel-toolbar"><h2>任务记录 <span className="count-chip">{query.data?.length ?? 0}</span></h2><button className="text-button" onClick={() => void query.reload()}>刷新任务记录</button></div><p className="muted small" style={{ padding: '0 20px' }}>{user.admin ? '管理员可查看全部任务' : '当前显示本人任务'}</p><ErrorNotice text={query.error || error} />{notice && <div className="notice success" role="status">{notice}</div>}{!query.data && query.loading ? <Loading /> : !query.data && query.error ? <Empty title="任务记录暂时无法读取" detail="请刷新重试；当前状态无法确认任务数量。" /> : !query.data?.length ? <Empty title={user.admin ? '当前暂无任务记录' : '当前账号暂无任务'} detail="排队任务提交后即在此显示。" /> : <div className="record-list">{query.data.map(run => {
    return <article className="task-record" key={run.id}><div className="record-heading"><h3>{run.name}</h3><Badge {...runStatus(run)} /></div><p className="muted small">{run.owner_name} · {dateTime(run.created_at)} · {run.spec?.source?.pr != null ? `PR #${run.spec.source.pr}` : run.spec?.source?.commit || run.spec?.source?.head_sha ? `commit ${(run.spec.source.commit || run.spec.source.head_sha).slice(0, 8)}` : '代码版本未记录'} · 空间 #{shortId(run.space_id)}</p>{run.reason && <p className="record-reason">{run.reason}</p>}<div className="toolbar-actions"><button className="button secondary small-button" aria-label={`查看任务 ${run.name}`} onClick={() => setSelected(run)}>查看任务与日志</button>{!terminal.includes(run.status) && (user.admin || run.owner_name === user.username) && <button className="text-button danger-text" aria-label={`取消任务 ${run.name}`} onClick={() => { setError(''); setAction({ kind: 'cancel', id: run.id, name: run.name }); }}>取消任务</button>}</div></article>;
  })}</div>}</section>
    <section className="panel section-gap"><div className="panel-toolbar"><h2>运行空间 <span className="count-chip">{spaces.data?.length ?? 0}</span></h2><button className="text-button" onClick={() => void spaces.reload()}>刷新运行空间</button></div><ErrorNotice text={spaces.error} />{!spaces.data && spaces.loading ? <Loading /> : !spaces.data?.length ? <Empty title="暂无运行空间" detail="任务资源、服务端环境与客户端环境在这里统一展示。" /> : <div className="record-list">{spaces.data.map(space => <article className="request-record" key={space.id}><div className="record-heading"><strong>空间 #{shortId(space.id)}</strong><Badge value={space.status} /></div><p className="muted small">归属 {space.owner_name} · 保留截止 {dateTime(space.retain_until)}</p><div className="workflow-space-environments">{space.environments?.map(env => <div key={env.alias}><strong>{env.alias}</strong><span>{env.node_alias}</span>{env.host && <code>{env.host}</code>}{env.logical_ids && <span>卡 {env.logical_ids.join(', ') || '无 NPU'}</span>}<Badge value={env.status} /><code>{env.container_name || '等待容器身份'}</code>{env.reason && <small>{env.reason}</small>}<details className="workflow-environment-evidence"><summary>环境来源与核验输出</summary><dl className="detail-list"><dt>镜像</dt><dd>{env.image || '未提供'}</dd><dt>解释器</dt><dd>{env.shell || '未提供'} / {env.python || '未提供'}</dd><dt>工作目录</dt><dd>{env.workdir || '未提供'}</dd><dt>请求的依赖</dt><dd>{(env.requested_packages || env.packages)?.map(pkg => `${pkg.name} ${pkg.version} · ${pkg.source}`).join('；') || '未配置'}</dd></dl><p className="muted small">以上为安装输入，实际版本以外部核验输出为准。</p>{env.identity && <pre>{JSON.stringify(env.identity, null, 2)}</pre>}{env.logs?.map((log, index) => <div className="log-panel" key={index}><div>{log.phase}</div><pre>{log.text}</pre></div>)}</details></div>)}</div>{!terminal.includes(space.status) && (user.admin || space.owner_name === user.username) && <button className="text-button danger-text" aria-label={`关闭运行空间 ${space.id}`} onClick={() => { setError(''); setAction({ kind: 'close', id: space.id, name: `空间 #${shortId(space.id)}` }); }}>关闭空间并归还资源</button>}</article>)}</div>}</section>
    {selected && <WorkflowDetail initial={selected} onClose={() => setSelected(undefined)} />}
    {action && <Modal title={action.kind === 'cancel' ? `取消任务「${action.name}」` : `关闭${action.name}`} onClose={() => setAction(undefined)}><div className="modal-body"><p>{action.kind === 'cancel' ? '取消当前任务的作业；共享安装环境中其他任务的进程由各自任务管理。' : '阻止空间接受新任务，结束其所属工作并核验容器退出，完成后统一归还节点与卡。'}</p><ErrorNotice text={error} /></div><div className="modal-actions"><button className="button secondary" onClick={() => setAction(undefined)}>返回</button><button className="button danger" disabled={busy} onClick={() => void act()}>{action.kind === 'cancel' ? '确认取消任务' : '确认关闭运行空间'}</button></div></Modal>}
  </>;
}

function WorkflowDetail({ initial, onClose }: { initial: WorkflowRun; onClose: () => void }) {
  const query = useQuery<WorkflowRun>(`/workflows/${initial.id}`, 5000);
  const run = query.data || initial;
  return <Modal title={`${run.name} · 任务详情`} onClose={onClose} wide><div className="modal-body"><ErrorNotice text={query.error} /><div className="workflow-row-heading"><Badge {...runStatus(run)} /><small className="muted">每 5 秒刷新 · #{shortId(run.id)}</small></div><PendingStatus status={run.status} />{run.reason && <p className="record-reason">{run.reason}</p>}<dl className="detail-list source-snapshot"><dt>执行代码 SHA</dt><dd><code>{run.spec?.source?.head_sha || '待固定'}</code></dd><dt>匹配 vLLM SHA</dt><dd><code>{run.spec?.source?.vllm_sha || '待固定'}</code></dd><dt>开始 / 结束</dt><dd>{dateTime(run.started_at)} / {dateTime(run.finished_at)}</dd></dl>{run.spec?.jobs?.length > 0 && <WorkflowGraph jobs={run.spec.jobs} readOnly />}
    {run.jobs?.length ? run.jobs.map(job => <section className="workflow-job-run" key={job.id}><div className="record-heading"><h3>{job.name || job.id}</h3><Badge value={job.status} label={job.status === 'SUCCEEDED' ? '执行成功' : undefined} /></div><p className="muted small">作业 {job.logical_job_id || job.id}{job.node_alias && ` · ${job.node_alias}`} · 阶段 {job.phase || '待执行'}{job.exit_code != null && ` · 退出码 ${job.exit_code}`}</p><PendingStatus status={job.status} job />{job.reason && <p className="record-reason">{job.reason}</p>}{job.endpoint && <p>服务地址：<code>{typeof job.endpoint === 'string' ? job.endpoint : JSON.stringify(job.endpoint)}</code></p>}<WorkflowEvidence workflowId={run.id} job={job} /></section>) : <Empty title="作业等待调度" detail="环境准备完成后，在这里查看各阶段状态及独立日志。" />}
  </div></Modal>;
}
