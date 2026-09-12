import { useEffect, useState, type FormEvent } from 'react';
import { errorText, patch, post } from '../api';
import type { NodeInfo } from '../types';
import { dateTime, ErrorNotice, Field } from './ui';

export function modelLabel(node: NodeInfo) {
  if (node.model_label) return node.model_label;
  const profile = node.metadata?.hardware_profile;
  const model = node.model?.trim() || profile?.system_product?.trim() || '';
  const separator = model.lastIndexOf('/');
  const suffix = separator >= 0 ? model.slice(separator + 1).trim() : '';
  const isBoard = suffix && (suffix === profile?.board_product?.trim() || /^IT22HMDA_[A-Za-z0-9_]+$/.test(suffix));
  return (isBoard ? model.slice(0, separator).trim() : model) || '机型待发现';
}

export function clusterLabel(node: NodeInfo) {
  const name = node.cluster_name?.trim() || '';
  return ['default', '默认集群'].includes(name.toLowerCase()) ? '' : name;
}

function computeConfirmed(node: NodeInfo) {
  const profile = node.metadata?.hardware_profile;
  const spec = node.metadata?.compute_spec;
  if (node.generation !== 'A3' || !profile || profile.quality !== 'ok' || !spec || !profile.soc_versions.length || !spec.confirmed_soc_versions?.length) return false;
  const current = [...new Set(profile.soc_versions)].sort();
  const confirmed = [...new Set(spec.confirmed_soc_versions)].sort();
  return current.length === confirmed.length && current.every((version, index) => version === confirmed[index]);
}

export function architectureLabel(node: NodeInfo) {
  const system = node.metadata?.hardware_profile?.host_system;
  if (!system) return '待采集';
  const label = ({ arm64: 'ARM64', arm: 'ARM', x86_64: 'x86_64', x86: 'x86', unknown: '待确认' })[system.architecture] || '待确认';
  return system.architecture !== 'unknown' && system.quality !== 'ok' ? `${label}（待复核）` : label;
}

const benchmarkStatus = { QUEUED: '排队中', RUNNING: '测试中', RECOVERING: '等待测试结束复核', SUCCEEDED: '测试完成', FAILED: '测试失败' };
function benchmarkActive(node: NodeInfo) {
  return ['QUEUED', 'RUNNING', 'RECOVERING'].includes(node.metadata?.compute_benchmark?.status || '');
}
function benchmarkNeedsReview(node: NodeInfo) {
  const result = node.metadata?.compute_benchmark;
  return result?.status === 'SUCCEEDED' && (!result.boot_id || !node.boot_id || result.boot_id !== node.boot_id);
}

function measuredComputeLabel(node: NodeInfo) {
  const benchmark = node.metadata?.compute_benchmark;
  if (benchmarkNeedsReview(node)) return '历史结果待复测';
  if (benchmark?.status === 'SUCCEEDED' && Number.isFinite(benchmark.min_tflops) && Number.isFinite(benchmark.max_tflops)) {
    const low = benchmark.min_tflops!.toLocaleString('en-US', { maximumFractionDigits: 2 });
    const high = benchmark.max_tflops!.toLocaleString('en-US', { maximumFractionDigits: 2 });
    return `${low === high ? low : `${low}–${high}`} TFLOPS`;
  }
  if (benchmark) return benchmarkStatus[benchmark.status] || '未测试';
  const tool = node.metadata?.hardware_profile?.ascend_dmi;
  return tool && !tool.available ? tool.path ? '工具不可用' : '工具未安装' : '未测试';
}

export function HardwareSummary({ node }: { node: NodeInfo }) {
  const profile = node.metadata?.hardware_profile;
  return <div className="hardware-summary">
    <span><span className="muted">CPU 架构</span> <strong>{architectureLabel(node)}</strong></span>
    <span><span className="muted">SoC version</span> <strong>{profile?.soc_versions?.join(' / ') || '待确认'}</strong>{profile?.soc_versions?.length && profile.quality !== 'ok' ? <small>（采集不完整）</small> : null}</span>
    <span title={node.metadata?.compute_benchmark?.recovery_reason || node.metadata?.compute_benchmark?.reason || profile?.ascend_dmi?.reason || undefined}><span className="muted">实测算力</span> <strong>{measuredComputeLabel(node)}</strong> <small>FP16 · 每逻辑设备</small></span>
  </div>;
}

export function HardwareDetails({ node, slot }: { node: NodeInfo; slot?: string | number }) {
  const profile = node.metadata?.hardware_profile;
  const system = profile?.host_system;
  const spec = node.metadata?.compute_spec;
  const devices = profile?.devices?.filter(device => slot == null || String(device.slot) === String(slot)) || [];
  return <section className="hardware-details"><h3>硬件规格</h3><HardwareSummary node={node} /><dl className="detail-list">
    <dt>系统产品</dt><dd>{profile?.system_product || '待确认'}<small className="subline">来源：cat /sys/class/dmi/id/product_name</small></dd>
    <dt>CPU 架构</dt><dd>{architectureLabel(node)}<small className="subline">判定依据：uname -m → {system?.machine || '待采集'}</small>{system?.reason && <small className="subline reason-text">{system.reason}</small>}</dd>
    <dt>uname -a</dt><dd><code className="system-uname">{system?.uname || '待采集'}</code><small className="subline">采集时间：{dateTime(system?.checked_at)}</small></dd>
    <dt>ascend-dmi</dt><dd>{profile?.ascend_dmi ? profile.ascend_dmi.available ? '可用' : profile.ascend_dmi.path ? '不可用' : '未安装' : '待检测'}{profile?.ascend_dmi?.version && <small className="subline">版本：{profile.ascend_dmi.version}</small>}{profile?.ascend_dmi?.path && <small className="subline mono">{profile.ascend_dmi.path}</small>}{profile?.ascend_dmi?.reason && <small className="subline reason-text">{profile.ascend_dmi.reason}</small>}</dd>
    <dt>SoC version</dt><dd>{devices.length ? devices.map(device => <div key={device.slot}>设备 {device.slot}：{device.soc_version || '待确认'}{device.chip_version && <small className="subline">Chip version：{device.chip_version}</small>}</div>) : '待采集'}{profile?.source && <small className="subline">来源：{profile.source}</small>}</dd>
    <dt>规格采集时间</dt><dd>{dateTime(profile?.checked_at)}{profile?.reason && <small className="subline reason-text">{profile.reason}</small>}</dd>
    <dt>理论算力口径</dt><dd>{node.generation === 'A3' ? 'FP16 稠密理论峰值 / 双芯模块（非单个逻辑设备）' : '该代际的算力口径待配置'}</dd>
    <dt>理论规格依据</dt><dd>{spec ? <>{spec.source}<small className="subline">登记值：{spec.fp16_tflops_per_module} TFLOPS · {dateTime(spec.updated_at)}</small><small className="subline">确认时 SoC：{spec.confirmed_soc_versions?.join(' / ') || '未记录'}</small>{!computeConfirmed(node) && <small className="subline reason-text">当前 SoC 采集不完整或与登记时不同，请管理员复核。</small>}</> : '待管理员依据规格资料登记'}</dd>
  </dl><ComputeBenchmarkDetails node={node} /></section>;
}

function ComputeBenchmarkDetails({ node }: { node: NodeInfo }) {
  const result = node.metadata?.compute_benchmark;
  return <div className="compute-benchmark-details"><h4>实测算力</h4>{result ? <><dl className="detail-list">
    <dt>测试状态</dt><dd>{benchmarkNeedsReview(node) ? '历史结果待复测' : benchmarkStatus[result.status]}{benchmarkNeedsReview(node) && <small className="subline reason-text">节点启动标识已变化或尚未确认，以下为历史测试值，请在整机空闲时重新测试。</small>}{result.reason && <small className="subline reason-text">{result.reason}</small>}{result.recovery_reason && <small className="subline reason-text">{result.recovery_reason}</small>}</dd>
    <dt>测量口径</dt><dd>FP16 实测值 / 逻辑设备，各卡分别执行，不等同于双芯模块理论峰值。</dd>
    <dt>执行命令</dt><dd><code className="system-uname">{result.command}</code></dd>
    <dt>测试时间</dt><dd>{dateTime(result.finished_at || result.started_at || result.requested_at)}</dd>
  </dl>{result.last_output && <details className="benchmark-command-output"><summary>查看命令输出</summary>{result.last_device_id != null && <small className="subline">Device ID {result.last_device_id}</small>}<pre>{result.last_output}</pre></details>}{result.status === 'SUCCEEDED' && Boolean(result.devices?.length) && <div className="benchmark-device-grid">{result.devices.map(device => <div key={device.device_id}><span>卡 {device.slot}<small className="subline">Device ID {device.logical_id}</small></span><strong>{device.tflops.toLocaleString('en-US', { maximumFractionDigits: 2 })} <small>TFLOPS</small></strong></div>)}</div>}</> : <p className="muted small">尚未执行 ascend-dmi 算力测试，管理员可在整机空闲时手动发起。</p>}</div>;
}

export function ComputeBenchmarkControl({ node, onUpdated }: { node: NodeInfo; onUpdated: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const active = benchmarkActive(node);
  const tool = node.metadata?.hardware_profile?.ascend_dmi;
  const run = async () => {
    if (busy || active) return;
    setBusy(true); setError(''); setNotice('');
    try { await post(`/nodes/${node.id}/compute-benchmark`); setNotice('算力测试已提交，测试期间整机暂停分配，结果将自动更新。'); onUpdated(); }
    catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return <section className="compute-benchmark-control"><h3>手动测试实测算力</h3><p className="muted small">使用 ascend-dmi 逐卡执行 FP16 测试。提交前检查整机无申请、无业务进程且采样完整；测试期间暂停整机分配，完成后恢复原维护状态。</p><button className="button secondary small-button" disabled={busy || active || !tool?.available} onClick={() => void run()}>{busy ? '正在提交…' : active ? benchmarkStatus[node.metadata!.compute_benchmark!.status] : '运行 FP16 算力测试'}</button>{!tool?.available && <p className="muted small">{tool?.reason || '需先检测到可用的 ascend-dmi 工具。'}</p>}<ErrorNotice text={error} />{notice && <div className="notice success" role="status">{notice}</div>}</section>;
}

export function ComputeSpecEditor({ node, onUpdated }: { node: NodeInfo; onUpdated: () => void }) {
  const spec = node.metadata?.compute_spec;
  const [value, setValue] = useState(spec ? String(spec.fp16_tflops_per_module) : '');
  const [source, setSource] = useState(spec?.source || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => { setValue(spec ? String(spec.fp16_tflops_per_module) : ''); setSource(spec?.source || ''); }, [node.id, spec?.updated_at, spec?.fp16_tflops_per_module, spec?.source]);
  const complete = node.generation === 'A3' && node.metadata?.hardware_profile?.quality === 'ok' && Boolean(node.metadata.hardware_profile.soc_versions.length);
  const save = async (clear: boolean) => {
    if (busy) return;
    const amount = Number(value);
    setError(''); setNotice('');
    if (!clear && (!complete || !Number.isFinite(amount) || amount <= 0 || !source.trim())) { setError('需先完成 SoC 采集，再填写正数算力与规格依据。'); return; }
    setBusy(true);
    try {
      await patch(`/nodes/${node.id}`, clear ? { clear_compute_spec: true } : { compute_spec: { fp16_tflops_per_module: amount, source: source.trim() } });
      if (clear) { setValue(''); setSource(''); }
      setNotice(clear ? '已清除算力登记。' : '算力规格已保存，并关联当前采集的 SoC version。'); onUpdated();
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  const submit = (event: FormEvent) => { event.preventDefault(); void save(false); };
  return <form className="compute-spec-editor" onSubmit={submit}><h3>理论规格（手动登记）</h3><p className="muted small">SoC version 由节点只读采集。按厂商规格资料填写同一双芯模块的 FP16 稠密峰值，例如 560 或 752 TFLOPS。</p><div className="form-grid">
    <Field label="算力（TFLOPS / 双芯模块）"><input type="number" required min="0.01" max="1000000" step="any" value={value} onChange={event => setValue(event.target.value)} placeholder="例如 560 或 752" /></Field>
    <Field label="规格依据" hint="填写对应型号的规格文档链接或资料说明。"><input required maxLength={512} value={source} onChange={event => setSource(event.target.value)} placeholder="型号与规格资料来源" /></Field>
  </div>{!complete && <p className="muted small">{node.generation === 'A3' ? 'SoC 信息尚未完整，完成节点重新检测后可登记算力。' : '当前双芯模块算力登记适用于 A3，其他代际待配置对应口径。'}</p>}<div className="compute-spec-actions"><button className="button secondary small-button" disabled={busy || !complete}>保存算力</button>{spec && <button className="text-button" type="button" disabled={busy} onClick={() => void save(true)}>清除算力登记</button>}</div><ErrorNotice text={error} />{notice && <div className="notice success" role="status">{notice}</div>}</form>;
}
