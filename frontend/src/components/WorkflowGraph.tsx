import { useId, useState } from 'react';
import type { WorkflowJob, WorkflowStep } from '../workflowTypes';
import { ErrorNotice } from './ui';
import './WorkflowGraph.css';

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

const CARD_WIDTH = 248, CARD_HEIGHT = 238, COLUMN_GAP = 144, ROW_GAP = 64, PAD = 32;
type Position = { x: number; y: number; rank: number };

function layout(jobs: WorkflowJob[]) {
  const ranks = new Map<string, number>(), visiting = new Set<string>();
  const rank = (id: string): number => {
    if (ranks.has(id)) return ranks.get(id)!;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const deps = jobs.find(job => job.id === id)?.depends_on.filter(dep => jobs.some(job => job.id === dep.job_id)) || [];
    const value = deps.length ? Math.max(...deps.map(dep => rank(dep.job_id))) + 1 : 0;
    visiting.delete(id); ranks.set(id, value); return value;
  };
  jobs.forEach(job => rank(job.id));
  const columns: WorkflowJob[][] = [];
  jobs.forEach(job => (columns[ranks.get(job.id)!] ||= []).push(job));
  const edges = jobs.flatMap(job => job.depends_on.filter(dep => ranks.has(dep.job_id)).map(dep => ({ from: dep.job_id, to: job.id, condition: dep.condition })));
  const skips = edges.filter(edge => ranks.get(edge.to)! - ranks.get(edge.from)! !== 1);
  const top = 70 + skips.length * 28;
  const rows = Math.max(1, ...columns.map(column => column?.length || 0));
  const positions = new Map<string, Position>();
  columns.forEach((column, level) => column?.forEach((job, row) => positions.set(job.id, {
    x: PAD + level * (CARD_WIDTH + COLUMN_GAP), y: top + (row + (rows - column.length) / 2) * (CARD_HEIGHT + ROW_GAP), rank: level,
  })));
  const routed = edges.map((edge, index) => {
    const from = positions.get(edge.from)!, to = positions.get(edge.to)!;
    const startX = from.x + CARD_WIDTH + 12, endX = to.x - 12;
    const startY = from.y + CARD_HEIGHT / 2, endY = to.y + CARD_HEIGHT / 2;
    // Vertical bends live in column gutters; long edges use the clear top rail.
    const track = edges.filter((other, i) => i < index && ranks.get(other.from) === from.rank).length;
    const outX = startX + 20 + (track % 6) * 12;
    let path: string;
    if (to.rank - from.rank === 1) path = `M ${startX} ${startY} H ${outX} V ${endY} H ${endX}`;
    else {
      const channelY = 24 + skips.indexOf(edge) * 28, inX = endX - 20 - (index % 4) * 12;
      path = `M ${startX} ${startY} H ${outX} V ${channelY} H ${inX} V ${endY} H ${endX}`;
    }
    return { ...edge, key: edge.from + '→' + edge.to, path };
  });
  return { positions, edges: routed, columns, top, width: Math.max(600, PAD * 2 + columns.length * CARD_WIDTH + Math.max(0, columns.length - 1) * COLUMN_GAP), height: top + rows * CARD_HEIGHT + (rows - 1) * ROW_GAP + PAD };
}

function Stage({ title, steps, number, extra }: { title: string; steps: WorkflowStep[]; number: string; extra?: string }) {
  const names = steps.map(step => step.files?.length ? step.files.map(file => file.name).join(' · ') : step.path?.split('/').pop() || step.launch?.split('\n')[0] || '待选择文件');
  return <div className={`job-graph-phase ${steps.length ? '' : 'phase-empty'}`}><span className="job-graph-phase-number">{number}</span><div><span className="job-graph-phase-label"><span>{title}</span><small>{steps.length ? `${steps.length} 步${extra ? ' · ' + extra : ''}` : '未配置'}</small></span><span className="job-graph-phase-files" title={steps.map(step => step.launch || step.path).join('\n')}>{names.length ? names.join(' · ') : '—'}</span></div></div>;
}

export function WorkflowGraph({ jobs, onChange, readOnly = false, onSelect, selectedJobId }: {
  jobs: WorkflowJob[]; onChange?: (jobs: WorkflowJob[]) => void; readOnly?: boolean; onSelect?: (id: string) => void; selectedJobId?: string;
}) {
  const markerId = useId().replace(/:/g, '');
  const [source, setSource] = useState<string>();
  const [highlight, setHighlight] = useState<string>();
  const [error, setError] = useState('');
  const diagram = layout(jobs), editable = !readOnly && !!onChange;
  const connect = (target: string) => {
    if (!source || !editable) return;
    const problem = dependencyError(jobs, source, target);
    setError(problem);
    if (!problem) {
      const condition = jobs.find(job => job.id === source)?.kind === 'service' ? 'ready' : 'succeeded';
      onChange(jobs.map(job => job.id === target ? { ...job, depends_on: [...job.depends_on, { job_id: source, condition }] } : job));
    }
    setSource(undefined);
  };
  const remove = (from: string, to: string) => onChange?.(jobs.map(job => job.id === to ? { ...job, depends_on: job.depends_on.filter(dep => dep.job_id !== from) } : job));
  return <section className="workflow-graph hive-job-graph" onKeyDown={event => { if (event.key === 'Escape') setSource(undefined); }}>
    <div className="job-graph-heading"><div><span className="job-graph-eyebrow">WORKFLOW / DAG</span><h4>作业依赖图 <span>{jobs.length} JOBS</span></h4></div><span className="job-graph-legend"><i /> 同列作业可并行</span></div>
    <p className="job-graph-hint">{editable ? '点击作业右侧输出端，再点击目标左侧输入端创建依赖；服务就绪或批处理成功后自动放行。' : '按连线顺序执行；服务就绪或批处理成功后放行。'}</p>
    <div className="workflow-graph-scroll" role="region" aria-label="作业画布" tabIndex={0}><div className="workflow-graph-stage" style={{ width: diagram.width, height: diagram.height }}>
      {diagram.columns.map((column, index) => column && <span className="job-graph-column" key={index} style={{ left: PAD + index * (CARD_WIDTH + COLUMN_GAP), top: diagram.top - 34 }}>阶段 {String(index + 1).padStart(2, '0')}</span>)}
      <svg width={diagram.width} height={diagram.height} aria-label="作业依赖连线"><defs><marker id={markerId} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs>
        {diagram.edges.map(edge => <g key={edge.key} className={highlight === edge.key ? 'job-graph-edge focused' : 'job-graph-edge'}><title>{edge.from} → {edge.to}：{edge.condition === 'ready' ? '就绪' : '成功'}</title><path d={edge.path} className="job-graph-edge-underlay" /><path d={edge.path} data-edge-from={edge.from} data-edge-to={edge.to} className="job-graph-edge-line" markerEnd={`url(#${markerId})`} /></g>)}
      </svg>
      {jobs.map((job,index) => { const position = diagram.positions.get(job.id)!; return <article className={`workflow-graph-node ${source === job.id ? 'connecting' : ''} ${selectedJobId === job.id ? 'selected' : ''}`} data-job-id={job.id} key={job.id} style={{ left: position.x, top: position.y, width: CARD_WIDTH, height: CARD_HEIGHT }}>
        <button type="button" className="job-graph-select" disabled={!onSelect} aria-label={`选择作业 ${job.id}`} onClick={() => onSelect?.(job.id)}>
          <div className="job-graph-node-heading"><span className="job-graph-kind">{job.kind === 'service' ? '◈' : '▧'}</span><div><strong title={job.name || job.id}>{`job${index}`}</strong><span className="job-graph-job-id">{job.id}</span></div><span className="job-graph-kind-label">{job.kind === 'service' ? '服务' : '批处理'}</span></div>
          <div className="job-graph-binding"><span title={job.environment}>{job.environment || '待绑定环境'}</span><span>{job.npu_count} NPU</span></div>
          <div className="job-graph-phases"><Stage title="前置校验" number="01" steps={job.pre} /><Stage title="执行" number="02" steps={job.steps} /><Stage title="后续检查" number="03" steps={job.kind === 'service' ? [...job.ready, ...job.post] : job.post} extra={job.kind === 'service' ? '启动后' : job.post_policy === 'always' ? '始终' : '成功后'} /></div>
        </button>
        {editable && <><button type="button" className={`graph-port graph-input ${source && source !== job.id ? 'available' : ''}`} aria-label={`连线到 ${job.id}`} title="输入：接入前置作业" onPointerUp={() => connect(job.id)} onClick={event => { if (event.detail === 0) connect(job.id); }}>+</button><button type="button" className="graph-port graph-output" aria-label={`从 ${job.id} 连线`} title="输出：连接后续作业" onPointerDown={event => { event.preventDefault(); setSource(job.id); setError(''); }} onClick={event => { if (event.detail === 0) { setSource(job.id); setError(''); } }}>→</button></>}
      </article>; })}
    </div></div>
    {source && <p className="job-graph-connecting" role="status">已选择 {source} 的输出端，点击目标输入端。<button type="button" onClick={() => setSource(undefined)}>取消连线</button></p>}
    <div className="job-graph-connections" aria-label="已连接的依赖">{diagram.edges.length ? diagram.edges.map(edge => <div key={edge.key} className="job-graph-connection" onMouseEnter={() => setHighlight(edge.key)} onMouseLeave={() => setHighlight(undefined)} onFocus={() => setHighlight(edge.key)} onBlur={() => setHighlight(undefined)}><span className="job-graph-connection-path">{edge.from}<span aria-hidden="true">→</span>{edge.to}</span><span className="job-graph-condition">{edge.condition === 'ready' ? '就绪后' : '成功后'}</span>{editable && <button type="button" aria-label={`删除依赖 ${edge.from} → ${edge.to}`} onClick={() => remove(edge.from, edge.to)}><span aria-hidden="true">×</span> 删除</button>}</div>) : <span className="job-graph-empty">尚无依赖 · 各作业可独立执行</span>}</div>
    <ErrorNotice text={error} />
  </section>;
}
