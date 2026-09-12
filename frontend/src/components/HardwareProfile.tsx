import { useEffect, useState, type FormEvent } from 'react';
import { errorText, patch } from '../api';
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

export function HardwareSummary({ node }: { node: NodeInfo }) {
  const profile = node.metadata?.hardware_profile;
  const spec = node.metadata?.compute_spec;
  return <div className="hardware-summary">
    <span><span className="muted">SoC version</span> <strong>{profile?.soc_versions?.join(' / ') || '待确认'}</strong>{profile?.soc_versions?.length && profile.quality !== 'ok' ? <small>（采集不完整）</small> : null}</span>
    <span><span className="muted">算力</span> <strong>{spec && computeConfirmed(node) ? `${spec.fp16_tflops_per_module} TFLOPS` : spec ? '待复核' : '待确认'}</strong> {node.generation === 'A3' && <small>FP16 稠密 · 每双芯模块</small>}</span>
  </div>;
}

export function HardwareDetails({ node, slot }: { node: NodeInfo; slot?: string | number }) {
  const profile = node.metadata?.hardware_profile;
  const spec = node.metadata?.compute_spec;
  const devices = profile?.devices?.filter(device => slot == null || String(device.slot) === String(slot)) || [];
  return <section className="hardware-details"><h3>硬件规格</h3><HardwareSummary node={node} /><dl className="detail-list">
    <dt>系统产品</dt><dd>{profile?.system_product || '待确认'}<small className="subline">来源：cat /sys/class/dmi/id/product_name</small></dd>
    <dt>SoC version</dt><dd>{devices.length ? devices.map(device => <div key={device.slot}>设备 {device.slot}：{device.soc_version || '待确认'}{device.chip_version && <small className="subline">Chip version：{device.chip_version}</small>}</div>) : '待采集'}{profile?.source && <small className="subline">来源：{profile.source}</small>}</dd>
    <dt>规格采集时间</dt><dd>{dateTime(profile?.checked_at)}{profile?.reason && <small className="subline reason-text">{profile.reason}</small>}</dd>
    <dt>算力口径</dt><dd>{node.generation === 'A3' ? 'FP16 稠密理论峰值 / 双芯模块（非单个逻辑设备）' : '该代际的算力口径待配置'}</dd>
    <dt>算力依据</dt><dd>{spec ? <>{spec.source}<small className="subline">登记值：{spec.fp16_tflops_per_module} TFLOPS · {dateTime(spec.updated_at)}</small><small className="subline">确认时 SoC：{spec.confirmed_soc_versions?.join(' / ') || '未记录'}</small>{!computeConfirmed(node) && <small className="subline reason-text">当前 SoC 采集不完整或与登记时不同，请管理员复核。</small>}</> : '待管理员依据规格资料登记'}</dd>
  </dl></section>;
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
  return <form className="compute-spec-editor" onSubmit={submit}><h3>登记算力规格</h3><p className="muted small">SoC version 由节点只读采集。按厂商规格资料填写同一双芯模块的 FP16 稠密峰值，例如 560 或 752 TFLOPS。</p><div className="form-grid">
    <Field label="算力（TFLOPS / 双芯模块）"><input type="number" required min="0.01" max="1000000" step="any" value={value} onChange={event => setValue(event.target.value)} placeholder="例如 560 或 752" /></Field>
    <Field label="规格依据" hint="填写对应型号的规格文档链接或资料说明。"><input required maxLength={512} value={source} onChange={event => setSource(event.target.value)} placeholder="型号与规格资料来源" /></Field>
  </div>{!complete && <p className="muted small">{node.generation === 'A3' ? 'SoC 信息尚未完整，完成节点重新检测后可登记算力。' : '当前双芯模块算力登记适用于 A3，其他代际待配置对应口径。'}</p>}<div className="compute-spec-actions"><button className="button secondary small-button" disabled={busy || !complete}>保存算力</button>{spec && <button className="text-button" type="button" disabled={busy} onClick={() => void save(true)}>清除算力登记</button>}</div><ErrorNotice text={error} />{notice && <div className="notice success" role="status">{notice}</div>}</form>;
}
