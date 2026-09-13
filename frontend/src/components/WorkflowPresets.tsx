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
    if (!admin || busy || !source?.pr) return;
    setError(''); setNotice('');
    if (!/^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*\.json$/.test(path) || path.split('/').some(part => part === '.' || part === '..')) { setError('请输入 PR 内的 JSON 清单相对路径，例如 ci/catalog.json。'); return; }
    setBusy(true);
    try { const imported = await post<WorkflowPreset[]>('/presets/import', { source: { pr: source.pr, head_sha: source.head_sha, vllm_sha: source.vllm_sha, ...(source.revision ? { revision: source.revision } : {}) }, path }); setNotice(`已导入 ${imported.length} 个预置条目，启用状态由中心单独核验。`); await query.reload(); }
    catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  const enable = async (preset: WorkflowPreset) => {
    if (!admin || busy || preset.validation_status === 'pending_execution') return;
    setBusy(true); setError(''); setNotice('');
    try { await post(`/presets/${preset.id}/enable`); await query.reload(); setNotice(`已启用「${preset.name}」。`); }
    catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  const values = query.data || [];
  const rows = values.filter(preset => {
    const tags = Object.values(preset.tags || {}).flatMap(value => Array.isArray(value) ? value : [value]).map(String);
    return (!cycle || tags.some(value => value.toLowerCase() === cycle)) && `${preset.name} ${tags.join(' ')}`.toLowerCase().includes(search.toLowerCase());
  });
  return <details className="workflow-presets"><summary>选择预置任务</summary><p className="muted small">载入预置后可在下方查看和修改配置；载入不会申请机器或执行任务。实机执行状态与载入权限分别记录。</p>{admin && <details><summary>高级管理：导入旧 PR 清单</summary><div className="workflow-catalog-admin"><Field label="预置清单 PR 路径"><input aria-label="预置清单 PR 路径" value={path} maxLength={512} placeholder="ci/catalog.json" onChange={event => setPath(event.target.value)} /></Field><button type="button" className="button secondary small-button" disabled={busy || !source?.pr || !path.trim()} onClick={() => void importCatalog()}>导入预置清单</button><p className="muted small">{source?.pr ? `来源：已解析 PR #${source.pr} 的固定提交。` : '请先在下方解析 PR，再导入该提交中的 JSON 清单。'}每次只启用后端配置允许的一个样例。</p></div></details>}<div className="toolbar"><input aria-label="搜索预置标签" placeholder="搜索名称、模型、数据集或标签" value={search} onChange={event => setSearch(event.target.value)} /><div className="choice-buttons" role="group" aria-label="预置周期">{[['', '全部周期'], ['nightly', 'nightly'], ['weekly', 'weekly']].map(([value, label]) => <button key={value} type="button" aria-pressed={cycle === value} onClick={() => setCycle(value)}>{label}</button>)}</div><button type="button" className="text-button" onClick={() => void query.reload()}>刷新预置</button></div><ErrorNotice text={query.error || error} />{notice && <div className="notice success" role="status">{notice}</div>}{!query.data && query.loading ? <Loading /> : !rows.length ? <Empty title={values.length ? '没有匹配的预置' : '尚未配置预置任务'} detail="请由管理员在 Hive 的 preset_tasks 目录中维护预置任务，完成后刷新列表。" /> : <div className="workflow-preset-list">{rows.map(preset => <article key={preset.id}><div className="workflow-row-heading"><strong>{preset.name}</strong><div className="toolbar-actions">{admin && !preset.enabled && <button type="button" className="text-button" aria-label={`启用预置 ${preset.name}`} disabled={busy || preset.validation_status === 'pending_execution'} title={preset.validation_status === 'pending_execution' ? preset.reason || '等待来源任务执行成功后启用。' : undefined} onClick={() => void enable(preset)}>单项启用</button>}<button type="button" className="button secondary small-button" aria-label={`使用预置 ${preset.name}`} disabled={preset.loadable === false || !preset.workflow || busy} onClick={() => onApply(preset)}>载入配置</button></div></div>{preset.validation_status === 'pending_execution' && <p className="muted small">待实机验收 · 可载入配置</p>}{preset.scope === 'personal' && <p className="muted small">个人用例 · 尚未验证</p>}<div className="workflow-tags">{Object.entries(preset.tags || {}).map(([key, value]) => <span className="tag" key={key}>{key}: {Array.isArray(value) ? value.join(' / ') : String(value)}</span>)}</div>{preset.reason && <p className="muted small">{preset.reason}</p>}<details><summary>查看预置来源</summary><dl className="detail-list"><dt>vLLM-Ascend commit</dt><dd><code>{preset.source?.head_sha || preset.source?.commit || '未记录'}</code></dd><dt>nightly YAML</dt><dd><code>{preset.yaml_path || '未记录'}</code></dd><dt>预置人员</dt><dd>{preset.created_by || preset.imported_by || '未记录'}</dd></dl></details></article>)}</div>}</details>;
}
