import { useState } from 'react';
import { errorText, patch, post } from '../api';
import { useQuery } from '../hooks';
import type { WorkflowPreset, WorkflowSource, WorkflowSpec } from '../workflowTypes';
import { Empty, ErrorNotice, Field, Loading, Modal } from './ui';
import './preset-table.css';

const columns = ['modelscope_model', 'quantization', 'test_type', 'dataset', 'device'] as const;
const textValue = (value: string | string[] | undefined) => Array.isArray(value) ? value.join(' / ') : value || '';
const columnValue = (preset: WorkflowPreset, key: string) => textValue(preset.tags[key] || (key === 'device' ? preset.tags.generation : undefined));
const remarksText = (preset: WorkflowPreset) => preset.remarks ?? Object.entries(preset.tags || {}).filter(([key]) => !columns.includes(key as typeof columns[number]) && key !== 'generation').map(([key, value]) => `${key}: ${textValue(value)}`).join('\n');
const blankTags = () => Object.fromEntries(columns.map(key => [key, '']));

export function WorkflowPresets({ onApply, admin = false }: { onApply: (preset: WorkflowPreset) => void; admin?: boolean; source?: WorkflowSource }) {
  const query = useQuery<WorkflowPreset[]>('/presets');
  const [search, setSearch] = useState(''), [filters, setFilters] = useState<Record<string, string>>({});
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<WorkflowPreset>(), [remarks, setRemarks] = useState('');
  const [adding, setAdding] = useState(false), [baseId, setBaseId] = useState(''), [name, setName] = useState('');
  const [tags, setTags] = useState<Record<string, string>>(blankTags), [yamlPath, setYamlPath] = useState('');
  const [commit, setCommit] = useState(''), [workflowText, setWorkflowText] = useState(''), [modalError, setModalError] = useState('');
  const values = query.data || [];
  const rows = values.filter(preset => columns.every(key => !filters[key] || columnValue(preset, key) === filters[key]) &&
    `${preset.name} ${Object.values(preset.tags || {}).map(textValue).join(' ')} ${remarksText(preset)}`.toLowerCase().includes(search.trim().toLowerCase()));
  const beginAdd = () => { setBaseId(''); setName(''); setTags(blankTags()); setRemarks(''); setYamlPath(''); setCommit(''); setWorkflowText(''); setModalError(''); setAdding(true); };
  const copyPreset = (id: string) => {
    setBaseId(id); setModalError('');
    const preset = values.find(value => value.id === id);
    if (!preset?.workflow) return;
    setName(`${preset.name} 副本`.slice(0, 128)); setTags(Object.fromEntries(columns.map(key => [key, columnValue(preset, key)])));
    setRemarks(remarksText(preset)); setYamlPath(preset.yaml_path || ''); setCommit(preset.source?.head_sha || preset.source?.commit || '');
    setWorkflowText(JSON.stringify(preset.workflow, null, 2));
  };
  const saveRemarks = async () => {
    if (!admin || !editing || busy) return;
    setBusy(true); setModalError('');
    try { await patch(`/presets/${editing.id}/remarks`, { remarks }); setEditing(undefined); setNotice('备注已更新。'); await query.reload(); }
    catch (err) { setModalError(errorText(err)); } finally { setBusy(false); }
  };
  const addPreset = async () => {
    if (!admin || busy) return;
    setModalError('');
    try {
      if (!name.trim()) throw new Error('请输入预置名称。');
      if (!workflowText.trim()) throw new Error('请选择一个预置复制配置，或上传 workflow JSON。');
      const workflow = JSON.parse(workflowText) as Partial<WorkflowSpec>;
      if (!workflow || typeof workflow !== 'object' || Array.isArray(workflow)) throw new Error('workflow JSON 必须是配置对象。');
      const sha = commit.trim().toLowerCase();
      if (sha && !/^[0-9a-f]{40}$/.test(sha)) throw new Error('来源 commit 须为完整的 40 位 SHA；留空使用当前 main。');
      const base = values.find(value => value.id === baseId);
      if (sha && sha === base?.source?.head_sha) workflow.source = base.source;
      else workflow.source = sha ? { revision: 'commit', commit: sha, head_sha: sha, vllm_sha: '' } : { revision: 'branch', branch: 'main', head_sha: '', vllm_sha: '' };
      setBusy(true);
      await post<WorkflowPreset>('/presets/public', { name: name.trim(), tags: Object.fromEntries(Object.entries(tags).filter(([, value]) => value.trim()).map(([key, value]) => [key, value.trim()])), remarks, yaml_path: yamlPath.trim(), workflow, ...(baseId ? { base_preset_id: baseId } : {}) });
      setAdding(false); setNotice('公共预置已添加，所有用户均可载入配置。'); await query.reload();
    } catch (err) { setModalError(errorText(err)); } finally { setBusy(false); }
  };
  const upload = async (file?: File) => {
    if (!file) return;
    setModalError('');
    try {
      if (file.size > 2 * 1024 * 1024) throw new Error('workflow JSON 最多 2 MiB。');
      const content = await file.text(), workflow = JSON.parse(content) as Partial<WorkflowSpec>;
      if (!workflow || typeof workflow !== 'object' || Array.isArray(workflow)) throw new Error('请上传 workflow 配置对象。');
      setWorkflowText(JSON.stringify(workflow, null, 2)); setBaseId('');
      if (!name && workflow.name) setName(workflow.name.slice(0, 128));
      setCommit(workflow.source?.head_sha || workflow.source?.commit || '');
    } catch (err) { setModalError(errorText(err)); }
  };
  const enable = async (preset: WorkflowPreset) => {
    if (!admin || busy) return;
    setBusy(true); setError('');
    try { await post(`/presets/${preset.id}/enable`); await query.reload(); }
    catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return <details className="workflow-presets preset-catalog"><summary>选择预置任务</summary>
    <p className="muted small">载入后可修改配置；载入不会申请机器或执行任务。</p>
    <div className="preset-catalog-toolbar"><input aria-label="搜索预置标签" placeholder="搜索名称、模型、数据集或备注" value={search} onChange={event => setSearch(event.target.value)} /><span className="muted small">{rows.length} / {values.length} 项</span><button type="button" className="text-button" onClick={() => { setFilters({}); setSearch(''); }}>清除筛选</button><button type="button" className="text-button" onClick={() => void query.reload()}>刷新</button>{admin && <button type="button" className="button secondary small-button" onClick={beginAdd}>添加预置</button>}</div>
    <ErrorNotice text={query.error || error} />{notice && <div className="notice success" role="status">{notice}</div>}
    {!query.data && query.loading ? <Loading /> : !values.length ? <Empty title="尚未配置预置任务" detail="管理员可添加公共预置，或维护仓库中的预置目录。" /> : <div className="preset-table-scroll"><table className="preset-table"><thead><tr><th scope="col">预置名称</th>{columns.map(key => <th scope="col" key={key}><span>{key}</span><select aria-label={`筛选 ${key}`} value={filters[key] || ''} onChange={event => setFilters(previous => ({ ...previous, [key]: event.target.value }))}><option value="">全部</option>{Array.from(new Set(values.map(preset => columnValue(preset, key)).filter(Boolean))).sort().map(value => <option key={value} value={value}>{value}</option>)}</select></th>)}<th scope="col">备注</th><th scope="col">操作</th></tr></thead><tbody>{rows.map(preset => <tr key={preset.id}><td><strong>{preset.name}</strong><details className="preset-source"><summary>来源</summary><dl><dt>vLLM-Ascend commit</dt><dd><code>{preset.source?.head_sha || preset.source?.commit || '未记录'}</code></dd><dt>YAML</dt><dd>{preset.yaml_path || '未记录'}</dd><dt>维护人</dt><dd>{preset.created_by || preset.imported_by || '未记录'}</dd></dl>{preset.reason && <p>{preset.reason}</p>}</details></td>{columns.map(key => <td key={key}>{columnValue(preset, key) || '—'}</td>)}<td className="preset-remarks"><span>{remarksText(preset) || '—'}</span>{admin && <button type="button" className="text-button" aria-label={`编辑备注 ${preset.name}`} onClick={() => { setEditing(preset); setRemarks(remarksText(preset)); setModalError(''); }}>编辑备注</button>}</td><td><div className="preset-table-actions"><button type="button" className="button secondary small-button" aria-label={`使用预置 ${preset.name}`} disabled={preset.loadable === false || !preset.workflow || busy} onClick={() => onApply(preset)}>载入配置</button>{admin && !preset.enabled && <button type="button" className="text-button" disabled={busy || preset.validation_status === 'pending_execution'} onClick={() => void enable(preset)}>单项启用</button>}</div></td></tr>)}{!rows.length && <tr><td colSpan={8} className="preset-no-matches">没有匹配的预置，请调整筛选条件。</td></tr>}</tbody></table></div>}
    {editing && <Modal title={`编辑备注 · ${editing.name}`} onClose={() => { if (!busy) setEditing(undefined); }}><div className="preset-dialog-body"><Field label="备注" hint="纯文本，最多 4000 字符；清空后显示为空备注。"><textarea rows={8} maxLength={4000} value={remarks} onChange={event => setRemarks(event.target.value)} /></Field><ErrorNotice text={modalError} /><div className="preset-dialog-actions"><button type="button" className="button secondary" disabled={busy} onClick={() => setEditing(undefined)}>取消</button><button type="button" className="button" disabled={busy} onClick={() => void saveRemarks()}>{busy ? '保存中…' : '保存备注'}</button></div></div></Modal>}
    {adding && <Modal title="添加公共预置" wide onClose={() => { if (!busy) setAdding(false); }}><div className="preset-dialog-body"><p className="muted small">复制已有配置或上传 workflow JSON。来源固定保存，添加不会启动任务。</p><div className="preset-form-grid"><Field label="复制已有预置"><select value={baseId} onChange={event => copyPreset(event.target.value)}><option value="">选择配置，或直接上传 JSON</option>{values.filter(preset => preset.workflow).map(preset => <option key={preset.id} value={preset.id}>{preset.name}</option>)}</select></Field><Field label="上传 workflow JSON"><input type="file" accept=".json,application/json" onChange={event => void upload(event.target.files?.[0])} /></Field><Field label="预置名称"><input value={name} maxLength={128} onChange={event => setName(event.target.value)} /></Field>{columns.map(key => <Field key={key} label={key}><input value={tags[key] || ''} maxLength={256} onChange={event => setTags(previous => ({ ...previous, [key]: event.target.value }))} /></Field>)}<Field label="来源 commit" hint="完整 40 位 SHA；留空解析当前 main。"><input value={commit} maxLength={40} onChange={event => setCommit(event.target.value)} /></Field><Field label="YAML 路径"><input value={yamlPath} maxLength={512} placeholder="tests/e2e/…/config.yaml" onChange={event => setYamlPath(event.target.value)} /></Field></div><Field label="备注"><textarea rows={3} maxLength={4000} value={remarks} onChange={event => setRemarks(event.target.value)} /></Field><details><summary>查看或编辑 workflow JSON</summary><textarea className="preset-json-editor" aria-label="workflow JSON" spellCheck={false} rows={12} value={workflowText} onChange={event => setWorkflowText(event.target.value)} /></details><ErrorNotice text={modalError} /><div className="preset-dialog-actions"><button type="button" className="button secondary" disabled={busy} onClick={() => setAdding(false)}>取消</button><button type="button" className="button" disabled={busy || !workflowText.trim() || !name.trim()} onClick={() => void addPreset()}>{busy ? '保存中…' : '添加公共预置'}</button></div></div></Modal>}
  </details>;
}
