import { useState, type FormEvent } from 'react';
import { api, errorText } from '../api';
import { useQuery } from '../hooks';
import type { ID, User } from '../types';
import { dateTime, ErrorNotice, Icon, Loading } from './ui';
import './NodeMappings.css';

export interface NodeMappingEntry { kind: 'model' | 'dataset' | 'image' | 'package'; name: string; target: string }
interface MappingDocument { node_id: ID; version: number; entries: NodeMappingEntry[]; updated_at?: string; updated_by?: string }
const kinds = [
  { value: 'model', button: '权重', label: 'ModelScope 权重', name: '组织/模型名称', target: '/mnt/weight/模型目录' },
  { value: 'dataset', button: '数据集', label: 'ModelScope 数据集', name: '组织/数据集名称', target: '/mnt/datasets/数据集目录' },
  { value: 'image', button: '镜像', label: '容器镜像', name: '镜像逻辑名称', target: 'registry/image:tag 或 sha256:…' },
  { value: 'package', button: '依赖包', label: '底层依赖包', name: '如 torch-npu、CANN', target: '/mnt/packages/安装包' },
] as const;

export function NodeMappingFields({ entries, onChange, disabled = false }: {
  entries: NodeMappingEntry[]; onChange: (entries: NodeMappingEntry[]) => void; disabled?: boolean;
}) {
  const update = (index: number, patch: Partial<NodeMappingEntry>) => onChange(entries.map((entry, i) => i === index ? { ...entry, ...patch } : entry));
  return <div className="node-mapping-fields">
    {!entries.length && <p className="node-mapping-empty">尚未添加映射，可按需登记本机已有资源。</p>}
    {entries.map((entry, index) => {
      const kind = kinds.find(item => item.value === entry.kind)!;
      return <div className="node-mapping-row" key={index}>
        <div className="node-mapping-type"><span>资源类型</span><strong className={`node-mapping-kind ${entry.kind}`}>{kind.label}</strong></div>
        <label><span>逻辑名称</span><input aria-label={`逻辑名称 ${index + 1}`} required disabled={disabled} value={entry.name} maxLength={255} placeholder={kind.name} onChange={e => update(index, { name: e.target.value })} /></label>
        <label><span>{entry.kind === 'image' ? '本机镜像' : '本机绝对路径'}</span><input aria-label={`本机位置 ${index + 1}`} required disabled={disabled} value={entry.target} maxLength={entry.kind === 'image' ? 255 : 4096} placeholder={kind.target} onChange={e => update(index, { target: e.target.value })} /></label>
        <button type="button" className="node-mapping-remove text-button danger-text" aria-label={`删除映射 ${index + 1}`} disabled={disabled} onClick={() => onChange(entries.filter((_, i) => i !== index))}>删除</button>
      </div>;
    })}
    <div className="node-mapping-add">{kinds.map(kind => <button key={kind.value} type="button" className="button secondary small-button" aria-label={`添加${kind.button}映射`} disabled={disabled || entries.length >= 128} onClick={() => onChange([...entries, { kind: kind.value, name: '', target: '' }])}><Icon name="plus" size={14} />{kind.button}</button>)}<span>{entries.length} / 128 项 · 总量最多 16 KiB</span></div>
  </div>;
}

export function NodeMappings({ nodeId, admin }: { nodeId: ID; admin?: boolean }) {
  const query = useQuery<MappingDocument>(`/nodes/${nodeId}/mappings`);
  const session = useQuery<User>(admin == null ? '/session' : null);
  const editable = admin ?? session.data?.admin ?? false;
  const [draft, setDraft] = useState<NodeMappingEntry[] | null>(null);
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (draft == null || busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      await api(`/nodes/${nodeId}/mappings`, { method: 'PUT', body: JSON.stringify({ version, entries: draft }) });
      setDraft(null); setNotice('资源映射已保存。'); await query.reload();
    } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  };
  return <section className="node-mappings" aria-label="节点资源映射">
    <div className="node-mappings-heading"><div><span className="node-mappings-eyebrow">LOCAL RESOURCE MAP</span><h3>资源映射</h3></div><span className="node-mapping-count">{query.data?.entries.length ?? '—'} 项</span></div>
    <p className="node-mappings-description">用统一名称关联本机权重、数据集、镜像和依赖包。修改后供新分配的运行空间使用，已有空间继续使用原映射快照。</p>
    <ErrorNotice text={query.error || session.error || error} retry={query.error ? () => void query.reload() : undefined} />
    {notice && <div className="notice success" role="status">{notice}</div>}
    {!query.data && query.loading ? <Loading /> : draft != null ? <form onSubmit={event => void save(event)}>
      <NodeMappingFields entries={draft} onChange={setDraft} disabled={busy} />
      <div className="node-mapping-actions"><button className="button primary small-button" disabled={busy}>{busy ? '保存中…' : '保存资源映射'}</button><button type="button" className="button secondary small-button" disabled={busy} onClick={() => { setDraft(null); setError(''); void query.reload(); }}>放弃修改并重新读取</button></div>
    </form> : query.data && <>
      {query.data.entries.length ? <div className="node-mapping-table"><table><thead><tr><th>类型</th><th>逻辑名称</th><th>本机位置</th></tr></thead><tbody>{query.data.entries.map(entry => <tr key={`${entry.kind}:${entry.name}`}><td><span className={`node-mapping-kind ${entry.kind}`}>{kinds.find(kind => kind.value === entry.kind)?.label}</span></td><td><code>{entry.name}</code></td><td><code>{entry.target}</code></td></tr>)}</tbody></table></div> : <p className="node-mapping-empty">暂未登记资源映射。</p>}
      <div className="node-mapping-actions">{editable && <button type="button" className="button secondary small-button" onClick={() => { setDraft(query.data!.entries.map(item => ({ ...item }))); setVersion(query.data!.version); setError(''); setNotice(''); }}>编辑资源映射</button>}<button type="button" className="text-button" disabled={query.loading} onClick={() => void query.reload()}>刷新映射</button><span className="node-mapping-version">版本 {query.data.version}{query.data.updated_by && ` · ${query.data.updated_by}`}{query.data.updated_at && ` · ${dateTime(query.data.updated_at)}`}</span></div>
    </>}
  </section>;
}
