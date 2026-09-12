import { useEffect, useState, type FormEvent } from 'react';
import { api, errorText, patch, post } from '../api';
import { useQuery } from '../hooks';
import type { ID, NodeInfo, User } from '../types';
import { HardwareSummary, HardwareDetails, ComputeSpecEditor, ComputeBenchmarkControl, modelLabel, clusterLabel } from '../components/HardwareProfile';
import { HardwareMetricHelp } from '../components/MetricHelp';
import { AddNode } from '../components/AddNode';
import { CredentialsButton } from '../components/CredentialsModal';
import { Badge, bytes, dateTime, Empty, ErrorNotice, Field, Icon, Link, Loading, Modal, PageHeader } from '../components/ui';

export function ClustersPage({ user }: { user: User }) {
  const query = useQuery<NodeInfo[]>('/nodes', 3000);
  const [add, setAdd] = useState(false);
  const [selectedId, setSelectedId] = useState<ID>();
  const selected = query.data?.find(node => node.id === selectedId);
  useEffect(() => {
    if (query.data && selectedId != null && !query.data.some(node => node.id === selectedId)) setSelectedId(undefined);
  }, [query.data, selectedId]);
  const [removeNode, setRemoveNode] = useState<NodeInfo>();
  const [passwordNode, setPasswordNode] = useState<NodeInfo>();
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState<ID>();
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [search, setSearch] = useState('');
  const mutate = async (node: NodeInfo, operation: 'probe' | 'maintenance' | 'password') => {
    if (busy != null) return; setBusy(node.id); setError(''); setNotice('');
    try {
      if (operation === 'probe') await post(`/nodes/${node.id}/probe`);
      else await patch(`/nodes/${node.id}`, operation === 'password' ? { password } : { maintenance: !node.maintenance });
      setNotice(operation === 'probe' ? `${node.host} 的检测已提交，结果将在更新后显示。` : '节点配置已更新。');
      setPasswordNode(undefined); setPassword(''); void query.reload();
    } catch (err) { setError(errorText(err)); } finally { setBusy(undefined); }
  };
  const remove = async () => {
    if (!removeNode || busy != null) return;
    setBusy(removeNode.id); setError('');
    try { await api(`/nodes/${removeNode.id}`, { method: 'DELETE' }); setRemoveNode(undefined); setNotice('节点已移除，历史申请和采样记录已保留。'); void query.reload(); }
    catch (err) { setError(errorText(err)); } finally { setBusy(undefined); }
  };
  const filtered = (query.data || []).filter(node => `${node.name} ${node.host} ${node.cluster_name} ${node.generation} ${node.model} ${(node.metadata?.hardware_profile?.soc_versions || []).join(' ')}`.toLowerCase().includes(search.toLowerCase()));
  return <><PageHeader eyebrow="CLUSTER INVENTORY" title="集群管理" description="集中维护机器台账，检查节点互联与共享存储。"><button className="button secondary" disabled={query.loading} onClick={() => void query.reload()}><Icon name="refresh" size={16} />刷新</button>{user.admin && <button className="button primary" onClick={() => setAdd(true)}><Icon name="plus" size={17} />纳管节点</button>}</PageHeader><ErrorNotice text={query.error || error} retry={query.error ? () => void query.reload() : undefined} />{notice && <div className="notice success" role="status"><Icon name="check" /><span>{notice}</span></div>}<div className="panel"><div className="panel-toolbar"><h2>机器台账 <span className="count-chip">{query.data?.length ?? '—'}</span></h2><small className="muted" role="status">每 3 秒刷新 · 最近更新 {query.updatedAt?.toLocaleTimeString() || '等待连接'}</small><div className="search-input"><Icon name="search" size={17} /><input aria-label="搜索机器台账" placeholder="搜索 IP、名称或集群" value={search} onChange={e => setSearch(e.target.value)} /></div></div>{!query.data && query.loading ? <Loading /> : !query.data && query.error ? <Empty title="暂时无法读取机器台账" detail="请检查连接后重试。" /> : !filtered.length ? <Empty title={query.data?.length ? '没有匹配的机器' : '从第一台机器开始'} detail={user.admin ? '填写 SSH 连接信息，中心管理机将发现 NPU 并执行纳管检查。' : '管理员纳管节点后，即可查看机器及申请资源。'} action={user.admin && !query.data?.length && <button className="button primary" onClick={() => setAdd(true)}><Icon name="plus" size={16} />纳管节点</button>} /> : <div className="table-scroll"><table className="inventory-table"><thead><tr><th>节点 / SSH</th><th><span className="metric-heading">集群与规格 <HardwareMetricHelp /></span></th><th>当前资源</th><th>节点状态</th><th>互联与共享盘</th><th>操作</th></tr></thead><tbody>{filtered.map(node => <tr key={node.id}><td><Link className="strong-link" to={`/nodes/${node.id}`}>{node.name || node.host}</Link><code className="subline">{node.host}:{node.port}</code><small className="subline">SSH · {node.ssh_user}</small>{(user.admin || user.can_view_credentials) && <CredentialsButton nodeId={node.id} host={node.host} />}</td><td><span className="generation">{node.generation}</span> <strong>{modelLabel(node)}</strong>{clusterLabel(node) && <small className="subline">{clusterLabel(node)}</small>}<HardwareSummary node={node} /><small className="subline">每卡显存 {Array.from(new Set(node.devices.map(d => bytes(d.memory_total)))).join(' / ') || '待采集'}</small></td><td><span>{node.devices.filter(d => ['available', 'free'].includes(d.status?.toLowerCase())).length} 空闲</span><small className="subline">{node.devices.filter(d => d.request_id != null).length} 已申请 · {node.devices.filter(d => d.processes?.length || (d.ai_core || 0) > 0).length} 实际运行</small><small className="subline">{Array.from(new Set(node.devices.map(d => d.owner_name).filter(Boolean))).join('、') || '暂无申请人'}</small></td><td><Badge value={node.maintenance ? 'maintenance' : node.status} /><small className="subline">{collectionLabel(node)}</small><small className="subline">最近采样：{node.sampled_at ? dateTime(node.sampled_at) : '尚未完成'}</small>{node.reason && <small className="subline reason-text">{node.reason}</small>}</td><td><button className="text-button" onClick={() => setSelectedId(node.id)}>查看检测结果</button><small className="subline">{node.probe_requested ? '互联与共享盘检测已排队' : node.mounts?.map(m => m.target || m.path).join('、') || '未发现共享盘'}</small></td><td><div className="table-actions">{user.admin ? <><button className="text-button" disabled={busy != null} onClick={() => void mutate(node, 'probe')}>{busy === node.id ? '处理中…' : '重新检测'}</button><button className="text-button" disabled={busy != null} onClick={() => void mutate(node, 'maintenance')}>{node.maintenance ? '结束维护' : '进入维护'}</button><button className="text-button" onClick={() => { setPasswordNode(node); setPassword(''); setError(''); }}>更新密码</button><button className="text-button danger-text" disabled={busy != null} onClick={() => { setRemoveNode(node); setError(''); }}>移除节点</button></> : <Link to={`/nodes/${node.id}`}>查看实时信息 →</Link>}</div></td></tr>)}</tbody></table></div>}</div>
  {add && <AddNode onClose={() => setAdd(false)} onCreated={() => { setAdd(false); setNotice('节点已纳管，基本信息已读取；正在采集 NPU 信息，页面每 3 秒自动刷新。'); void query.reload(); }} />}
  {removeNode && <Modal title={`移除 ${removeNode.host}`} onClose={() => { if (busy == null) setRemoveNode(undefined); }}><div className="modal-body"><p>确认从 Hive 移除节点 <strong>{removeNode.name}</strong>（{removeNode.host}）？</p><p className="muted">停止采集并清除保存的 SSH 密码，保留历史记录。服务器上的进程和文件不会被删除。有有效申请、任务或算力测试时无法移除。</p><ErrorNotice text={error} /></div><div className="modal-actions"><button className="button secondary" disabled={busy != null} onClick={() => setRemoveNode(undefined)}>取消</button><button className="button danger" disabled={busy != null} onClick={() => void remove()}>{busy != null ? '正在移除…' : '确认移除'}</button></div></Modal>}
  {passwordNode && <Modal title={`更新 ${passwordNode.host} 的连接密码`} onClose={() => { setPasswordNode(undefined); setPassword(''); }}><form onSubmit={event => { event.preventDefault(); void mutate(passwordNode, 'password'); }}><div className="modal-body"><Field label="SSH 密码" hint="更新平台保存的连接凭据，不修改节点系统密码。"><input type="password" autoComplete="new-password" value={password} required onChange={e => setPassword(e.target.value)} /></Field><ErrorNotice text={error} /></div><div className="modal-actions"><button type="button" className="button secondary" onClick={() => setPasswordNode(undefined)}>取消</button><button className="button primary" disabled={busy != null}>保存密码</button></div></form></Modal>}
  {selected && <Modal title={`${selected.host} · 纳管检测`} onClose={() => setSelectedId(undefined)} wide><div className="modal-body"><HardwareDetails node={selected} /><h3>节点与互联检测</h3><Metadata value={selected.metadata} />{user.admin && <NodeControls node={selected} onUpdated={() => void query.reload()} />}<h3 className="section-title">共享存储</h3>{selected.mounts?.length ? selected.mounts.map((mount, index) => <div className="mount-card" key={index}><div><strong className="mono">{mount.target || mount.path}</strong><Badge value={mount.status} /></div><dl className="detail-list"><dt>挂载源</dt><dd>{mount.source || '—'}</dd><dt>文件系统</dt><dd>{mount.fstype || '—'}</dd><dt>访问模式</dt><dd>{mount.writable == null ? '待确认' : mount.writable ? '可写' : '只读'}</dd><dt>剩余 / 总量</dt><dd>{bytes(mount.available_bytes ?? mount.free_bytes)} / {bytes(mount.total_bytes)}</dd>{mount.shared_storage_id && <><dt>共享组</dt><dd>{mount.shared_storage_id}</dd></>}{(mount.reason || mount.detail) && <><dt>检测说明</dt><dd>{mount.reason || mount.detail}</dd></>}{mount.checked_at && <><dt>检测时间</dt><dd>{dateTime(mount.checked_at)}</dd></>}</dl></div>) : <p className="muted">未发现共享盘或尚未完成检测。</p>}</div></Modal>}</>;
}

function NodeControls({ node, onUpdated }: { node: NodeInfo; onUpdated: () => void }) {
  const [mapping, setMapping] = useState(JSON.stringify(node.metadata?.hccn_ids || {}, null, 2));
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [baseline, setBaseline] = useState<ID>();
  const save = async (event: FormEvent) => {
    event.preventDefault(); if (busy) return; setError(''); setNotice('');
    let parsed: unknown;
    try { parsed = JSON.parse(mapping); if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || Object.entries(parsed).some(([key, value]) => !/^\d+:\d+$/.test(key) || typeof value !== 'number' || !Number.isInteger(value) || value < 0 || value > 1024)) throw new Error(); }
    catch { setError('映射需为 JSON 对象，键使用卡号:芯片号，值为 0–1024 的整数。'); return; }
    setBusy(true); try { await patch(`/nodes/${node.id}`, { hccn_ids: parsed }); setNotice('互联卡号映射已保存，等待重新检测。'); onUpdated(); } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  const confirmBaseline = async () => { if (baseline == null || busy) return; setBusy(true); setError(''); setNotice(''); try { await post(`/devices/${baseline}/baseline`); setBaseline(undefined); setNotice('当前无业务显存已记为该卡基线。'); onUpdated(); } catch (err) { setError(errorText(err)); } finally { setBusy(false); } };
  return <section className="node-controls"><ComputeBenchmarkControl node={node} onUpdated={onUpdated} /><ComputeSpecEditor node={node} onUpdated={onUpdated} /><h3>设备管理</h3><p className="muted small">配置设备命令映射，确认驱动常驻显存。</p><form onSubmit={event => void save(event)}><Field label="互联卡号映射" hint={'按实际设备配置，如 {"0:0": 0, "0:1": 1}；A3 映射不能仅按顺序猜测。'}><textarea className="mapping-editor" rows={3} spellCheck={false} value={mapping} onChange={e => setMapping(e.target.value)} /></Field><button className="button secondary small-button" disabled={busy}>保存映射</button></form><div className="baseline-list">{node.devices.map(device => <div key={device.id}><span>卡 {device.slot} <small>{bytes(device.memory_used)} 显存占用</small></span><button className="text-button" disabled={busy || device.request_id != null || device.ai_core !== 0 || Boolean(device.processes?.length)} onClick={() => { setBaseline(device.id); setError(''); }}>确认空闲基线</button></div>)}</div>{baseline != null && <div className="notice"><Icon name="info" size={16} /><span>确认此卡没有业务运行，将当前显存占用记为驱动基线。后端会复验无进程、AI Core 为零及采样完整性。</span><button className="text-button" disabled={busy} onClick={() => void confirmBaseline()}>确认</button><button className="text-button" onClick={() => setBaseline(undefined)}>取消</button></div>}<ErrorNotice text={error} />{notice && <div className="notice success" role="status">{notice}</div>}</section>;
}

function Metadata({ value }: { value: Record<string, unknown> }) {
  const names: Record<string, string> = { driver_version: '驱动版本', firmware_version: '固件版本', npu_smi_version: 'npu-smi 版本', connectivity: '互联检查', probes: '检测结果', checked_at: '检测时间', dependencies: '节点命令', tools: '节点命令', admission: '纳管检查' };
  const entries = Object.entries(value || {}).filter(([key]) => !['hardware_profile', 'compute_spec', 'compute_benchmark'].includes(key));
  if (!entries.length) return <p className="muted">等待纳管检测结果。</p>;
  return <dl className="detail-list metadata-list">{entries.map(([key, item]) => <div key={key}><dt>{names[key] || key}</dt><dd>{typeof item === 'object' ? <pre>{JSON.stringify(item, null, 2)}</pre> : String(item ?? '—')}</dd></div>)}</dl>;
}

function collectionLabel(node: NodeInfo) {
  const collection = node.metadata?.collection as { status?: string; started_at?: string } | undefined;
  if (collection?.status === 'failed') return '采集失败，修复连接后自动重试';
  if (!node.sampled_at) return collection?.status === 'running' ? '首次采集中…' : '等待首次采样（默认每 15 秒采集）';
  if (node.status === 'unknown') return '采样已过期或不完整，等待有效数据';
  return collection?.status === 'running' ? '正在更新实时信息…' : '实时采样正常';
}
