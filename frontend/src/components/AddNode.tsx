import { useState, type FormEvent } from 'react';
import { errorText, post } from '../api';
import { ErrorNotice, Field, Modal } from './ui';

interface ConnectionCheck {
  status: 'ok' | 'host_key_required';
  host_key: { algorithm: string; fingerprint: string };
  model?: string;
  detail: string;
}

export function AddNode({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState({ name: '', host: '', port: 22, ssh_user: 'root', password: '', generation: 'A3', cluster_name: '默认集群' });
  const [result, setResult] = useState<ConnectionCheck>();
  const [fingerprint, setFingerprint] = useState<string>();
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const update = (value: Partial<typeof form>) => {
    setForm({ ...form, ...value }); setResult(undefined); setFingerprint(undefined); setConfirmed(false); setError('');
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (busy) return;
    setBusy(true); setError('');
    const connection = { host: form.host.trim(), port: form.port, ssh_user: form.ssh_user, password: form.password,
      host_key_fingerprint: fingerprint || (confirmed ? result?.host_key.fingerprint : undefined) };
    try {
      if (result?.status === 'ok') {
        await post('/nodes', { ...form, ...connection, name: form.name.trim() || form.host.trim() });
        onCreated();
      } else {
        const checked = await post<ConnectionCheck>('/nodes/check', connection);
        setResult(checked);
        if (checked.status === 'ok') setFingerprint(checked.host_key.fingerprint);
      }
    } catch (err) { setError(errorText(err)); setResult(undefined); setConfirmed(false); }
    finally { setBusy(false); }
  };
  return <Modal title="纳管新节点" onClose={() => { if (!busy) onClose(); }}>
    <form onSubmit={event => void submit(event)}>
      <div className="modal-body">
        <p className="muted form-intro">先检测 SSH 连接并自动读取机型，确认后添加。NPU、互联和共享盘信息会在后台持续更新。</p>
        <div className="form-grid">
          <Field label="节点名称"><input disabled={busy} value={form.name} maxLength={128} placeholder="留空则使用服务器地址" onChange={e => update({ name: e.target.value })} /></Field>
          <Field label="集群"><input disabled={busy} required maxLength={128} value={form.cluster_name} onChange={e => update({ cluster_name: e.target.value })} /></Field>
          <Field label="服务器 IP / 主机名"><input disabled={busy} required value={form.host} maxLength={255} placeholder="10.0.0.1" onChange={e => update({ host: e.target.value })} /></Field>
          <Field label="SSH 端口"><input disabled={busy} type="number" min={1} max={65535} required value={form.port} onChange={e => update({ port: Number(e.target.value) })} /></Field>
          <Field label="SSH 账号"><input disabled={busy} required value={form.ssh_user} maxLength={64} onChange={e => update({ ssh_user: e.target.value })} /></Field>
          <Field label="SSH 密码"><input disabled={busy} type="password" required autoComplete="new-password" value={form.password} onChange={e => update({ password: e.target.value })} /></Field>
          <Field label="NPU 代际"><select disabled={busy} value={form.generation} onChange={e => update({ generation: e.target.value })}>{['A2', 'A3', 'A5'].map(g => <option key={g}>{g}</option>)}</select></Field>
          <Field label="机型 · 自动检测"><input readOnly value={result?.status === 'ok' ? result.model || '系统未提供机型' : ''} placeholder="检测连接后自动读取" /></Field>
        </div>
        {result?.status === 'host_key_required' && <div className="connection-check">
          <strong>首次连接：核验服务器身份</strong>
          <p className="muted small">请通过服务器可信控制台或管理员核对以下 SSH 主机指纹。确认后才会使用密码登录。</p>
          <code className="host-key-fingerprint">{result.host_key.algorithm} · {result.host_key.fingerprint}</code>
          <label className="check-line"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />我已核对，信任此主机指纹</label>
        </div>}
        {result?.status === 'ok' && <div className="notice success" role="status">{result.detail}。点击“添加节点”完成纳管。</div>}
        {busy && <p className="muted" role="status">{result?.status === 'ok' ? '正在复验连接并保存节点…' : '正在检测 SSH 和读取系统信息，请稍候…'}</p>}
        <ErrorNotice text={error} />
      </div>
      <div className="modal-actions">
        <button type="button" className="button secondary" disabled={busy} onClick={onClose}>取消</button>
        <button className="button primary" disabled={busy || (result?.status === 'host_key_required' && !confirmed)}>{busy ? '检测中…' : result?.status === 'ok' ? '添加节点' : result?.status === 'host_key_required' ? '确认并检测连接' : '检测连接'}</button>
      </div>
    </form>
  </Modal>;
}
