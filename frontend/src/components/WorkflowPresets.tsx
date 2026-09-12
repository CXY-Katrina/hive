import { useState } from 'react';
import { errorText, post } from '../api';
import { useQuery } from '../hooks';
import type { WorkflowPreset, WorkflowSource } from '../workflowTypes';
import { Empty, ErrorNotice, Field, Loading } from './ui';

export function WorkflowPresets({ onApply, admin = false, source }: { onApply: (preset: WorkflowPreset) => void; admin?: boolean; source?: WorkflowSource }) {
  const query = useQuery<WorkflowPreset[]>('/presets');
  const [search, setSearch] = useState(''), [cycle, setCycle] = useState('');
  const [path, setPath] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const importCatalog = async () => {
    if (!admin || busy || !source) return;
    setError(''); setNotice('');
    if (!/^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*\.json$/.test(path) || path.split('/').some(part => part === '.' || part === '..')) { setError('请输入 PR 内的 JSON 清单相对路径，例如 ci/catalog.json。'); return; }
    setBusy(true);
    try { const imported = await post<WorkflowPreset[]>('/presets/import', { source: { pr: source.pr, head_sha: source.head_sha, vllm_sha: source.vllm_sha }, path }); setNotice(`已导入 ${imported.length} 个预置条目，启用状态由中心单独核验。`); await query.reload(); }
    catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  const enable = async (preset: WorkflowPreset) => {
    if (!admin || busy) return;
    setBusy(true); setError(''); setNotice('');
    try { await post(`/presets/${preset.id}/enable`); await query.reload(); setNotice(`已启用「${preset.name}」。`); }
    catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  const values = query.data || [];
  const rows = values.filter(preset => {
    const tags = Object.values(preset.tags || {}).flatMap(value => Array.isArray(value) ? value : [value]).map(String);
    return (!cycle || tags.some(value => value.toLowerCase() === cycle)) && `${preset.name} ${tags.join(' ')}`.toLowerCase().includes(search.toLowerCase());
  });
  return <details className="workflow-presets"><summary>选择外部预置</summary><p className="muted small">名称、标签和文件入口来自外部清单；只有已验收并开放的条目可以载入执行配置。</p>{admin && <div className="workflow-catalog-admin"><Field label="预置清单 PR 路径"><input aria-label="预置清单 PR 路径" value={path} maxLength={512} placeholder="ci/catalog.json" onChange={event => setPath(event.target.value)} /></Field><button type="button" className="button secondary small-button" disabled={busy || !source || !path.trim()} onClick={() => void importCatalog()}>导入预置清单</button><p className="muted small">{source ? `来源：已解析 PR #${source.pr} 的固定提交。` : '请先在下方解析 PR，再导入该提交中的 JSON 清单。'}每次只启用后端配置允许的一个样例。</p></div>}<div className="toolbar"><input aria-label="搜索预置标签" placeholder="搜索名称、模型、数据集或标签" value={search} onChange={event => setSearch(event.target.value)} /><div className="choice-buttons" role="group" aria-label="预置周期">{[['', '全部周期'], ['nightly', 'nightly'], ['weekly', 'weekly']].map(([value, label]) => <button key={value} type="button" aria-pressed={cycle === value} onClick={() => setCycle(value)}>{label}</button>)}</div><button type="button" className="text-button" onClick={() => void query.reload()}>刷新预置</button></div><ErrorNotice text={query.error || error} />{notice && <div className="notice success" role="status">{notice}</div>}{!query.data && query.loading ? <Loading /> : !rows.length ? <Empty title={values.length ? '没有匹配的预置' : '尚未提供外部预置清单'} detail="预置由 PR 清单提供，平台不会生成模型业务脚本。" /> : <div className="workflow-preset-list">{rows.map(preset => <article key={preset.id}><div className="workflow-row-heading"><strong>{preset.name}</strong><div className="toolbar-actions">{admin && !preset.enabled && <button type="button" className="text-button" aria-label={`启用预置 ${preset.name}`} disabled={busy} onClick={() => void enable(preset)}>单项启用</button>}<button type="button" className="button secondary small-button" aria-label={`使用预置 ${preset.name}`} disabled={!preset.enabled || !preset.workflow || busy} onClick={() => onApply(preset)}>载入配置</button></div></div><div className="workflow-tags">{Object.entries(preset.tags || {}).map(([key, value]) => <span className="tag" key={key}>{key}: {Array.isArray(value) ? value.join(' / ') : String(value)}</span>)}</div>{preset.reason && <p className="muted small">{preset.reason}</p>}{preset.source && <details><summary>查看预置来源</summary><pre>{JSON.stringify(preset.source, null, 2)}</pre></details>}</article>)}</div>}</details>;
}
