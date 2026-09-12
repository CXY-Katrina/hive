import { useState } from 'react';
import { post, errorText } from '../api';
import type { Credentials, ID } from '../types';
import { ErrorNotice, Icon, Loading, Modal } from './ui';

export function CredentialsButton({ nodeId, host }: { nodeId: ID; host: string }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState<Credentials>();
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const show = async () => {
    setError(''); setValue(undefined); setOpen(true); setCopied(false);
    try { setValue(await post<Credentials>(`/nodes/${nodeId}/credentials`)); } catch (err) { setError(errorText(err)); }
  };
  const close = () => { setOpen(false); setValue(undefined); setCopied(false); };
  const copy = async () => {
    try { await navigator.clipboard.writeText(value?.password || ''); setCopied(true); } catch { setError('浏览器未允许复制，请手动选择密码复制。'); }
  };
  return <><button className="text-button" onClick={() => void show()}>查看凭据</button>{open && <Modal title={`${host} · SSH 凭据`} onClose={close}><div className="modal-body"><ErrorNotice text={error} />{!value && !error && <Loading />}{value && <><dl className="detail-list"><dt>连接命令</dt><dd><code>ssh -p {value.port} {value.ssh_user}@{value.host}</code></dd><dt>机器密码</dt><dd className="secret"><code>{value.password || '未设置密码'}</code>{value.password && <button className="icon-button" aria-label="复制密码" onClick={() => void copy()}><Icon name={copied ? 'check' : 'copy'} /></button>}</dd></dl><p className="muted small">凭据查看已记录。请仅使用申请分配的卡。</p></>}</div></Modal>}</>;
}
