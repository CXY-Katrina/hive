import { useEffect, useMemo, useState } from 'react';
import { HardwareSummary, HardwareDetails, modelLabel, clusterLabel } from '../components/HardwareProfile';
import { useQuery } from '../hooks';
import type { Device, MetricDescriptor, NodeInfo, Sample } from '../types';
import { Badge, bytes, dateTime, Empty, ErrorNotice, Icon, Link, Loading, Modal, PageHeader, shortId, statusLabel, validMetric } from '../components/ui';

const pageSize = 8;
export function NodesPage({ nodeId }: { nodeId?: string }) {
  const query = useQuery<NodeInfo[]>('/nodes', 15000);
  const metrics = useQuery<MetricDescriptor[]>('/metrics');
  const [search, setSearch] = useState('');
  const [generation, setGeneration] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [detailSelection, setDetailSelection] = useState<{ nodeId: NodeInfo['id']; deviceId: Device['id'] }>();
  const detailNode = query.data?.find(node => node.id === detailSelection?.nodeId);
  const detailDevice = detailNode?.devices.find(device => device.id === detailSelection?.deviceId);
  const detail = detailNode && detailDevice ? { node: detailNode, device: detailDevice } : undefined;
  useEffect(() => {
    if (query.data && detailSelection && !query.data.some(node => node.id === detailSelection.nodeId && node.devices.some(device => device.id === detailSelection.deviceId))) setDetailSelection(undefined);
  }, [query.data, detailSelection]);
  const [extensions, setExtensions] = useState(false);
  useEffect(() => { setPage(1); }, [search, generation, status, nodeId]);
  const filtered = useMemo(() => (query.data || []).filter(node => {
    if (nodeId && String(node.id) !== nodeId) return false;
    if (generation && node.generation !== generation) return false;
    if (status && !node.devices?.some(device => device.status?.toLowerCase() === status)) return false;
    const contents = [node.name, node.host, node.model, node.cluster_name, ...(node.metadata?.hardware_profile?.soc_versions || []), ...node.devices.flatMap(device => [device.owner_name || '', ...device.processes.flatMap(process => [String(process.pid), process.container_name || ''])])].join(' ').toLowerCase();
    return contents.includes(search.toLowerCase());
  }), [query.data, search, generation, status, nodeId]);
  const devices = filtered.flatMap(node => node.devices || []);
  const counts = { available: devices.filter(d => ['available', 'free'].includes(d.status?.toLowerCase())).length, allocated: devices.filter(d => d.request_id != null).length, external: devices.filter(d => ['external', 'external_busy'].includes(d.status?.toLowerCase())).length, unknown: devices.filter(d => d.status?.toLowerCase() === 'unknown' || !validMetric(d.quality)).length };
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, pages);
  return <><PageHeader eyebrow="LIVE OVERVIEW" title={nodeId ? '节点详情' : '服务器实时信息'} description="所有纳管节点，一览每张卡的登记状态与实际运行。"><span className="refresh-caption"><span className="connection-dot" />15 秒刷新{query.updatedAt && <small>{query.updatedAt.toLocaleTimeString('zh-CN')} 更新</small>}</span><button className="button secondary" onClick={() => void query.reload()} disabled={query.loading}><Icon name="refresh" size={16} />刷新</button></PageHeader>
  {nodeId && <Link to="/nodes/" className="back-link">← 返回全部节点</Link>}
  <div className="stats-grid">{[{ label: '纳管节点', value: filtered.length, sub: `${devices.length} 张 NPU 卡`, icon: 'server', tone: '' }, { label: '空闲可申请', value: counts.available, sub: '依据当前有效采样', icon: 'chip', tone: 'green' }, { label: '已登记申请', value: counts.allocated, sub: '含环境准备保护期', icon: 'request', tone: 'blue' }, { label: '平台外占用', value: counts.external, sub: `${counts.unknown} 张卡采样待确认`, icon: 'task', tone: 'amber' }].map(item => <div className={`stat-card ${item.tone}`} key={item.label}><div><span>{item.label}</span><strong>{query.data ? item.value : '—'}</strong><small>{item.sub}</small></div><span className="stat-icon"><Icon name={item.icon} size={22} /></span></div>)}</div>
  <ErrorNotice text={query.error} retry={() => void query.reload()} />
  <div className="toolbar"><div className="search-input"><Icon name="search" size={18} /><input aria-label="搜索节点、申请人或容器" placeholder="搜索 IP、机型、申请人、Docker 容器…" value={search} onChange={e => setSearch(e.target.value)} /></div><select aria-label="筛选代际" value={generation} onChange={e => setGeneration(e.target.value)}><option value="">全部代际</option>{['A2', 'A3', 'A5'].map(g => <option key={g}>{g}</option>)}</select><select aria-label="筛选卡状态" value={status} onChange={e => setStatus(e.target.value)}><option value="">全部状态</option>{['available', 'allocated', 'external', 'unknown', 'maintenance', 'fault'].map(s => <option key={s} value={s}>{statusLabel(s)}</option>)}</select><label className="check-field simple"><input type="checkbox" checked={extensions} onChange={e => setExtensions(e.target.checked)} />扩展指标</label></div>
  {extensions && <ErrorNotice text={metrics.error} retry={() => void metrics.reload()} />}
  {!query.data && query.loading ? <Loading /> : !query.data && query.error ? <div className="panel"><Empty title="暂时无法读取节点信息" detail="请检查连接后重试。" /></div> : filtered.length === 0 ? <div className="panel"><Empty title={query.data?.length ? '没有匹配的节点' : '还没有纳管节点'} detail={query.data?.length ? '尝试调整搜索或筛选条件。' : '纳管机器后，这里会显示所有节点的实时 NPU 状态。'} action={!query.data?.length && <Link className="button primary" to="/clusters">前往集群管理<Icon name="arrow" size={16} /></Link>} /></div> : <div className="node-list">{filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize).map(node => <article className="node-panel panel" key={node.id}><div className="node-heading"><div className="node-identity"><span className="node-icon"><Icon name="server" /></span><div><h2><Link to={`/nodes/${node.id}`}>{node.name || node.host}</Link><span className="generation">{node.generation}</span></h2><span className="muted mono">{node.host}</span><span className="node-model">{modelLabel(node)}{clusterLabel(node) && ` · ${clusterLabel(node)}`}</span><HardwareSummary node={node} /></div></div><div className="node-heading-right"><Badge value={node.maintenance ? 'maintenance' : node.status} /><small>采样 {dateTime(node.sampled_at)}</small></div></div>{node.reason && <div className="node-reason">{node.reason}</div>}<div className="table-scroll"><table className="device-table"><thead><tr><th>NPU 卡</th><th>AI Core</th><th>显存 HBM</th><th>平台登记</th><th>实际进程 / Docker 容器</th><th>状态</th></tr></thead><tbody>{node.devices.map(device => <DeviceRow key={device.id} device={device} metrics={extensions ? metrics.data || [] : []} onDetail={() => setDetailSelection({ nodeId: node.id, deviceId: device.id })} />)}</tbody></table>{!node.devices.length && <p className="inline-empty">尚未发现 NPU 设备，等待节点检测与采样。</p>}</div><div className="node-footer"><span><Icon name="server" size={14} />互联：{probeLabel(node.metadata)}</span><span>共享盘：{node.mounts?.length ? node.mounts.map(mount => `${mount.target || mount.path || '未知路径'} · ${statusLabel(mount.status)}`).join('，') : '未发现 / 待检测'}</span></div></article>)}</div>}
  {filtered.length > 0 && <div className="pagination"><span>共 {filtered.length} 个节点 · 统计覆盖全部筛选结果</span><div><button className="button secondary small-button" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>上一页</button><span>{currentPage} / {pages}</span><button className="button secondary small-button" disabled={currentPage >= pages} onClick={() => setPage(currentPage + 1)}>下一页</button></div></div>}
  {detail && <DeviceDetail node={detail.node} device={detail.device} descriptors={metrics.data || []} onClose={() => setDetailSelection(undefined)} />}</>;
}

function probeLabel(metadata: Record<string, unknown>) {
  const admission = metadata?.admission as { checks?: number; passed?: number; peer_count?: number; scope?: string } | undefined;
  if (admission && admission.checks != null) return `${admission.scope === 'representative' ? '抽检 ' : ''}${admission.passed || 0} / ${admission.checks} 项通过 · ${admission.peer_count || 0} 个对端`;
  const probe = metadata?.connectivity;
  if (typeof probe === 'string') return statusLabel(probe);
  if (probe && typeof probe === 'object' && 'status' in probe) return statusLabel(String(probe.status));
  return '查看纳管检测结果';
}
function DeviceRow({ device, metrics, onDetail }: { device: Device; metrics: MetricDescriptor[]; onDetail: () => void }) {
  const good = validMetric(device.quality) && (!device.sampled_at || Date.now() - Date.parse(device.sampled_at) <= 45000);
  const memoryPercent = device.memory_total && device.memory_used != null ? device.memory_used / device.memory_total * 100 : null;
  return <tr><td><button className="device-link" onClick={onDetail}><Icon name="chip" size={18} />卡 {device.slot}<Icon name="chevron" size={13} /></button><small className="subline">{statusLabel(device.health)}</small></td><td><MetricBar value={good ? device.ai_core : null} /><button className="trend-link" onClick={onDetail}>10 分钟趋势 ↗</button>{metrics.filter(m => device.extensions && m.key in device.extensions).map(metric => <small className="subline" key={metric.key}>{metric.label || metric.name || metric.key}：{good ? device.extensions?.[metric.key] ?? '—' : '—'} {metric.unit}</small>)}</td><td><span className="metric-number">{good ? bytes(device.memory_used) : '—'} <span className="muted">/ {bytes(device.memory_total)}</span></span><div className="meter memory"><i style={{ width: `${good && memoryPercent != null ? Math.min(100, Math.max(0, memoryPercent)) : 0}%` }} /></div></td><td>{device.owner_name ? <><strong className="owner-name">{device.owner_name}</strong><small className="subline">#{shortId(device.request_id || '')}</small>{device.protected_until && <small className="subline">保护至 {dateTime(device.protected_until)}</small>}</> : <span className="muted">未登记申请</span>}</td><td className="process-cell">{device.processes?.length ? device.processes.map(process => <div className="process-line" key={process.pid}><code>PID {process.pid}</code><span title={process.container_id || process.reason}>{process.container_name || (['host', 'bare_metal'].includes(process.container_status || process.container_kind || '') ? '宿主机进程' : '容器未识别')}</span>{process.name && <small>{process.name}</small>}</div>) : <span className="muted">{good ? '未检测到 NPU 进程' : '进程信息待确认'}</span>}</td><td><Badge value={device.status} />{!good && <small className="subline" title={device.reason}>{statusLabel(device.quality)}</small>}{device.reason && <small className="subline reason-text">{device.reason}</small>}</td></tr>;
}
function MetricBar({ value }: { value: number | null }) { return <div className="metric-bar"><span className="metric-number">{value == null ? '—' : value.toFixed(1) + '%'}</span><div className="meter"><i style={{ width: `${Math.max(0, Math.min(100, value || 0))}%` }} /></div></div>; }

function DeviceDetail({ node, device, descriptors, onClose }: { node: NodeInfo; device: Device; descriptors: MetricDescriptor[]; onClose: () => void }) {
  const history = useQuery<Sample[]>(`/devices/${device.id}/history`, 15000);
  const [metricKey, setMetricKey] = useState('ai_core');
  const descriptor = descriptors.find(item => item.key === metricKey);
  const samples = (history.data || []).filter(sample => Date.parse(sample.sampled_at) >= Date.now() - 10 * 60 * 1000).sort((a, b) => Date.parse(a.sampled_at) - Date.parse(b.sampled_at));
  return <Modal title={`${node.host} · 卡 ${device.slot}`} onClose={onClose} wide><div className="modal-body"><HardwareDetails node={node} slot={device.slot} /><div className="chart-heading"><div><h3>最近 10 分钟</h3><p className="muted small">缺测保留断点，不会按零值绘制。</p></div><select aria-label="历史指标" value={metricKey} onChange={e => setMetricKey(e.target.value)}><option value="ai_core">AI Core 利用率</option><option value="memory_used">显存已用</option>{descriptors.filter(m => !['ai_core', 'memory_used', 'memory_total'].includes(m.key)).map(m => <option key={m.key} value={m.key}>{m.label || m.name || m.key}</option>)}</select></div><ErrorNotice text={history.error} retry={() => void history.reload()} />{history.loading && !history.data ? <Loading /> : samples.length ? <Trend samples={samples} metricKey={metricKey} unit={descriptor?.unit || (metricKey === 'ai_core' ? '%' : metricKey === 'memory_used' ? 'bytes' : '')} /> : <Empty title="暂无有效历史数据" detail="完成采样后将显示该卡的趋势。" />}<dl className="detail-list"><dt>平台状态</dt><dd><Badge value={device.status} /></dd><dt>登记申请人</dt><dd>{device.owner_name || '无平台申请记录'}</dd><dt>最近状态说明</dt><dd>{device.reason || '—'}</dd></dl>{device.processes?.length > 0 && <div className="process-details"><h3>实际 NPU 进程</h3>{device.processes.map(process => <div key={process.pid}><strong>PID {process.pid} · {process.name || '进程名未采集'}</strong><p>{process.container_name || '未识别容器 / 宿主机进程'}</p>{process.container_id && <code className="long-code">{process.container_id}</code>}{process.reason && <small>{process.reason}</small>}</div>)}</div>}</div></Modal>;
}

function Trend({ samples, metricKey, unit }: { samples: Sample[]; metricKey: string; unit: string }) {
  const valueOf = (sample: Sample) => !validMetric(sample.quality) ? null : metricKey === 'ai_core' ? sample.ai_core : metricKey === 'memory_used' ? sample.memory_used : sample.extensions?.[metricKey] ?? null;
  const end = Date.now(); const start = end - 600000;
  const values = samples.map(valueOf);
  const max = metricKey === 'ai_core' ? 100 : Math.max(...values.filter((v): v is number => v != null), 1);
  const paths: string[] = []; let path = ''; let previous = 0;
  samples.forEach((sample, index) => { const value = values[index]; const time = Date.parse(sample.sampled_at); if (value == null || (previous && time - previous > (sample.sample_interval_seconds || 15) * 1500)) { if (path) paths.push(path); path = ''; } if (value != null) { const x = 46 + (time - start) / 600000 * 614; const y = 162 - Math.max(0, value) / max * 136; path += `${path ? ' L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`; } previous = time; });
  if (path) paths.push(path);
  return <div className="trend"><svg viewBox="0 0 690 202" role="img" aria-label={`${metricKey} 最近十分钟历史曲线，断线表示缺测`}><text x="6" y="28">{unit === 'bytes' ? bytes(max) : `${max.toFixed(0)}${unit}`}</text><text x="25" y="165">0</text>{[26, 94, 162].map(y => <line key={y} x1="46" y1={y} x2="660" y2={y} stroke="var(--border)" strokeDasharray="4 4" />)}{paths.map((segment, index) => <path key={index} d={segment} fill="none" stroke="var(--accent)" strokeWidth="2.5" strokeLinecap="round" />)}<text x="46" y="190">10 分钟前</text><text x="624" y="190">现在</text></svg>{values.every(value => value == null) && <span className="chart-empty">此时间段采样无效或指标未支持</span>}</div>;
}
