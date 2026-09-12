import { useState } from 'react';
import { api, errorText, patch } from '../api';
import { useQuery } from '../hooks';
import type { ID, User } from '../types';
import { dateTime, Empty, ErrorNotice, Loading, Modal, PageHeader } from '../components/ui';

interface Member extends User { created_at: string; last_login_at: string }

export function MembersPage() {
  const query = useQuery<Member[]>('/members', 15000);
  const [busy, setBusy] = useState<ID>();
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [remove, setRemove] = useState<Member>();
  const [pending, setPending] = useState<{ id: ID; key: 'can_request' | 'can_view_credentials'; value: boolean }>();
  const update = async (member: Member, key: 'can_request' | 'can_view_credentials', value: boolean) => {
    if (busy != null) return;
    setBusy(member.id); setPending({ id: member.id, key, value }); setError(''); setNotice('');
    try {
      await patch(`/members/${member.id}`, { can_request: member.can_request, can_view_credentials: member.can_view_credentials, [key]: value });
      setNotice(`${member.username} 的权限已更新，立即生效。`); await query.reload();
    } catch (err) { setError(errorText(err)); } finally { setBusy(undefined); setPending(undefined); }
  };
  const confirmRemove = async () => {
    if (!remove || busy != null) return;
    setBusy(remove.id); setError('');
    try { await api(`/members/${remove.id}`, { method: 'DELETE' }); setNotice(`已删除成员 ${remove.username}。`); setRemove(undefined); await query.reload(); }
    catch (err) { setError(errorText(err)); } finally { setBusy(undefined); }
  };
  return <><PageHeader eyebrow="MEMBER ACCESS" title="人员管理" description="管理成员的服务器申请和密码查看权限。"><button className="button secondary" onClick={() => void query.reload()} disabled={query.loading}>刷新</button></PageHeader>
    <p className="notice">成员首次登录后出现在列表中，两项权限默认关闭。“查看服务器密码”允许查看已纳管节点的 SSH 连接凭据。</p>
    <ErrorNotice text={query.error || error} />{notice && <p className="notice success" role="status">{notice}</p>}
    <div className="panel"><div className="panel-toolbar"><h2>团队成员 <span className="count-chip">{query.data?.length ?? '—'}</span></h2></div>
      {!query.data && query.loading ? <Loading /> : !query.data?.length ? <Empty title="暂无成员" /> : <div className="table-scroll"><table><thead><tr><th>成员</th><th>最近登录</th><th>申请服务器</th><th>查看服务器密码</th><th>操作</th></tr></thead><tbody>{query.data.map(member => <tr key={member.id}>
        <td><strong>{member.username}</strong><small className="subline">{member.admin ? '管理员' : '成员'}</small></td><td>{dateTime(member.last_login_at)}</td>
        <td><label className="check-field simple"><input type="checkbox" aria-label={`${member.username} 申请服务器`} checked={pending?.id === member.id && pending.key === 'can_request' ? pending.value : member.can_request} disabled={member.admin || busy != null} onChange={event => void update(member, 'can_request', event.target.checked)} />{member.can_request ? '已开启' : '未开启'}</label></td>
        <td><label className="check-field simple"><input type="checkbox" aria-label={`${member.username} 查看服务器密码`} checked={pending?.id === member.id && pending.key === 'can_view_credentials' ? pending.value : member.can_view_credentials} disabled={member.admin || busy != null} onChange={event => void update(member, 'can_view_credentials', event.target.checked)} />{member.can_view_credentials ? '已开启' : '未开启'}</label></td>
        <td>{member.admin ? <span className="muted">管理员不可删除</span> : <button className="text-button danger-text" disabled={busy != null} onClick={() => { setRemove(member); setError(''); }}>删除成员</button>}</td>
      </tr>)}</tbody></table></div>}
    </div>
    {remove && <Modal title={`删除成员 ${remove.username}`} onClose={() => { if (busy == null) setRemove(undefined); }}><div className="modal-body"><p>删除后立即退出该成员的所有登录会话，并禁止使用此用户名重新登录。历史申请和审计记录会保留。</p><p className="muted">有未结束的申请或任务时，需要先取消或归还资源。</p><ErrorNotice text={error} /></div><div className="modal-actions"><button className="button secondary" disabled={busy != null} onClick={() => setRemove(undefined)}>取消</button><button className="button danger" disabled={busy != null} onClick={() => void confirmRemove()}>确认删除</button></div></Modal>}
  </>;
}
