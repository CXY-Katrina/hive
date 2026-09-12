import type { ResourceSpec } from '../types';
import { Field, Icon } from './ui';

export const defaultResource = (): ResourceSpec => ({ generation: 'A3', model: '', mode: 'partial', machine_count: 1, cards_per_node: 1, min_memory_gib: 0, require_interconnect: false, queue: true, wait_minutes: 60 });

export function ResourceForm({ value, onChange, task = false }: { value: ResourceSpec; onChange: (value: ResourceSpec) => void; task?: boolean }) {
  const update = <K extends keyof ResourceSpec>(key: K, next: ResourceSpec[K]) => onChange({ ...value, [key]: next, ...(key === 'machine_count' && Number(next) <= 1 ? { require_interconnect: false } : {}) });
  return <div className="resource-form"><div className="form-grid">
    <div className="field"><span>NPU 代际</span><div className="choice-buttons" role="group" aria-label="NPU 代际">{['A2', 'A3', 'A5'].map(item => <button type="button" key={item} aria-pressed={value.generation === item} onClick={() => update('generation', item)}>{item}</button>)}</div></div>
    <Field label="机型" hint="留空匹配该代际所有机型"><input value={value.model} onChange={e => update('model', e.target.value)} placeholder="不限机型" maxLength={128} /></Field>
    <div className="field"><span>分配方式</span><div className="choice-buttons" role="group" aria-label="分配方式"><button type="button" aria-pressed={value.mode === 'partial'} onClick={() => update('mode', 'partial')}>部分卡</button><button type="button" aria-pressed={value.mode === 'whole'} onClick={() => update('mode', 'whole')}>整机独占</button></div></div>
    <Field label="机器数量"><input type="number" min={1} max={64} required value={value.machine_count} onChange={e => update('machine_count', Number(e.target.value))} /></Field>
    {value.mode === 'partial' && <Field label="每机申请卡数"><input type="number" min={1} max={128} required value={value.cards_per_node} onChange={e => update('cards_per_node', Number(e.target.value))} /></Field>}
  </div>
  {value.machine_count > 1 && <label className="check-field"><input type="checkbox" checked={value.require_interconnect} onChange={e => update('require_interconnect', e.target.checked)} /><span>需要多机互联<small>分配前复验所选机器与卡的双向连通性</small></span></label>}
  <div className="queue-row"><label className="check-field"><input type="checkbox" checked={value.queue} onChange={e => update('queue', e.target.checked)} /><span>资源不足时排队</span></label>{value.queue && <label className="inline-field">最长等待 <input type="number" min={1} max={10080} required value={value.wait_minutes} onChange={e => update('wait_minutes', Number(e.target.value))} /> 分钟</label>}</div>
  {!task && <div className="resource-note"><Field label="用途说明（可选）"><input maxLength={1024} placeholder="例如推理调试、环境准备" value={value.note || ''} onChange={e => update('note', e.target.value)} /></Field></div>}
  <div className="notice compact"><Icon name="info" size={17} /><span>{task ? '任务使用独立执行时限，准备环境期间不会按 AI Core 空闲回收。' : '交付后保护 30 分钟；保护结束后，申请卡连续 10 分钟 AI Core 全零，可清理相关进程并归还。'}</span></div>
  </div>;
}

export function resourceDescription(spec?: Partial<ResourceSpec>) {
  if (!spec) return '资源规格待确认';
  return `${spec.generation || 'NPU'}${spec.model ? ' · ' + spec.model : ''} · ${spec.machine_count || 1} 台 × ${spec.mode === 'whole' ? '整机' : (spec.cards_per_node || 1) + ' 卡'}`;
}
