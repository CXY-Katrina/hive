import { useId, useState } from 'react';
import type { WorkflowJob } from '../workflowTypes';
import { ErrorNotice } from './ui';

export function dependencyError(jobs: WorkflowJob[], from: string, to: string) {
  if (from === to) return '作业不能依赖自身。';
  const target = jobs.find(job => job.id === to);
  if (!target || !jobs.some(job => job.id === from)) return '依赖引用了不存在的作业。';
  if (target.depends_on.some(item => item.job_id === from)) return '该依赖已经存在。';
  const visited = new Set<string>();
  const reaches = (id: string): boolean => {
    if (id === to) return true;
    if (visited.has(id)) return false;
    visited.add(id);
    return jobs.find(job => job.id === id)?.depends_on.some(item => reaches(item.job_id)) || false;
  };
  return reaches(from) ? '依赖会形成环，请保持单向执行顺序。' : '';
}

export function WorkflowGraph({ jobs, onChange, readOnly = false }: { jobs: WorkflowJob[]; onChange?: (jobs: WorkflowJob[]) => void; readOnly?: boolean }) {
  const markerId = useId();
  const [selected, setSelected] = useState<string>();
  const [error, setError] = useState('');
  const width = Math.max(600, jobs.length * 232 + 24);
  const positions = new Map(jobs.map((job, index) => [job.id, 24 + index * 232]));
  const connect = (target: string) => {
    if (!selected || !onChange) return;
    const problem = dependencyError(jobs, selected, target);
    setError(problem);
    if (!problem) onChange(jobs.map(job => job.id === target ? { ...job, depends_on: [...job.depends_on, { job_id: selected, condition: 'succeeded' }] } : job));
    setSelected(undefined);
  };
  return <section className="workflow-graph"><div className="workflow-row-heading"><h4>作业依赖图</h4>{!readOnly && <small className="muted">拖动或依次点击输出端 → 输入端；也可用下方依赖表单</small>}</div><div className="workflow-graph-scroll"><div className="workflow-graph-stage" style={{ width, height: 170 }} onKeyDown={event => { if (event.key === 'Escape') setSelected(undefined); }}>
    <svg width={width} height="170" aria-label="作业依赖连线"><defs><marker id={markerId} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs>{jobs.flatMap(job => job.depends_on.map(dep => {
      const from = (positions.get(dep.job_id) || 0) + 188, to = positions.get(job.id) || 0;
      const curve = `M ${from} 81 C ${from + 30} 140 ${to - 30} 140 ${to} 81`;
      return <g key={`${dep.job_id}-${job.id}`}><path d={curve} fill="none" stroke="currentColor" strokeWidth="2" markerEnd={`url(#${markerId})`} /><text x={(from + to) / 2} y="134" textAnchor="middle">{dep.condition === 'ready' ? '就绪' : '成功'}</text>{!readOnly && <path d={curve} fill="none" stroke="transparent" strokeWidth="16" role="button" tabIndex={0} aria-label={`删除依赖 ${dep.job_id} → ${job.id}`} onClick={() => onChange?.(jobs.map(value => value.id === job.id ? { ...value, depends_on: value.depends_on.filter(item => item.job_id !== dep.job_id) } : value))} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onChange?.(jobs.map(value => value.id === job.id ? { ...value, depends_on: value.depends_on.filter(item => item.job_id !== dep.job_id) } : value)); } }} />}</g>;
    }))}</svg>
    {jobs.map(job => <div className={`workflow-graph-node ${selected === job.id ? 'selected' : ''}`} key={job.id} style={{ left: positions.get(job.id), top: 42 }}><strong>{job.name || job.id}</strong><small>{job.environment} · {job.kind === 'service' ? '常驻服务' : '批处理'}</small>{!readOnly && <><button type="button" className="graph-port graph-input" aria-label={`连线到 ${job.id}`} onPointerUp={() => connect(job.id)} onClick={event => { if (event.detail === 0) connect(job.id); }}>○</button><button type="button" className="graph-port graph-output" aria-label={`从 ${job.id} 连线`} onPointerDown={event => { event.preventDefault(); setSelected(job.id); setError(''); }} onClick={event => { if (event.detail === 0) { setSelected(job.id); setError(''); } }}>●</button></>}</div>)}
  </div></div>{selected && <p className="muted small" role="status">已选择 {selected}，点击目标作业输入端；Esc 取消。</p>}<ErrorNotice text={error} /></section>;
}
