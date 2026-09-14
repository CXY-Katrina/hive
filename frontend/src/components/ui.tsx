import { useEffect, useId, useRef, type ReactNode } from 'react';
import { navigate } from '../hooks';

const statusLabels: Record<string, string> = {
  available: '空闲', free: '空闲', allocated: '已申请', protected: '保护中', active: '使用中', running: '执行中',
  external: '平台外占用', external_busy: '平台外占用', unknown: '未知', offline: '离线', online: '在线', healthy: '正常',
  ok: '正常', good: '正常', failed: '失败', error: '异常', unhealthy: '异常', maintenance: '维护中',
  pending: '待处理', queued: '排队中', reserving: '分配中', probing: '检测中', preparing: '准备中',
  delivered: '已交付', releasing: '释放中', cleaning: '清理中', collecting: '收集中', released: '已归还',
  cancelled: '已取消', cancelling: '取消中', succeeded: '已完成', completed: '已完成', expired: '已过期',
  unsupported: '未支持', stale: '采样过期', missing: '缺测', verified: '已验证', passed: '已通过',
  candidate: '共享候选', local: '本地挂载', read_only: '只读', blocked: '待处理', timeout: '超时', reserved: '已预留', prepared: '准备就绪', closed: '已关闭', fault: '故障',
};
export function statusLabel(value?: string) { return value ? statusLabels[value.toLowerCase()] || value : '未知'; }
export function Badge({ value, label }: { value?: string; label?: string }) {
  const key = value?.toLowerCase() || 'unknown';
  const tone = ['available', 'free', 'online', 'healthy', 'ok', 'good', 'succeeded', 'completed', 'verified', 'passed'].includes(key) ? 'green'
    : ['allocated', 'protected', 'active', 'running', 'delivered'].includes(key) ? 'blue'
    : ['external', 'external_busy', 'queued', 'pending', 'preparing', 'reserving', 'probing', 'cleaning', 'releasing', 'cancelling', 'collecting'].includes(key) ? 'amber'
    : ['failed', 'error', 'unhealthy', 'offline', 'timeout'].includes(key) ? 'red' : 'gray';
  return <span className={`badge ${tone}`}><i />{label || statusLabel(value)}</span>;
}
export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    nodes: <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>,
    server: <><rect x="3" y="3" width="18" height="7" rx="2" /><rect x="3" y="14" width="18" height="7" rx="2" /><path d="M7 6.5h.01M7 17.5h.01M12 6.5h5M12 17.5h5" /></>,
    request: <><rect x="4" y="5" width="16" height="16" rx="2" /><path d="M8 3v4M16 3v4M4 11h16M9 16h6M12 13v6" /></>,
    task: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m7 9 3 3-3 3M13 15h4" /></>,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 12-1l2 6M4 12l2 6a7 7 0 0 0 12-1" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4.5 4.5" /></>,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    user: <><circle cx="12" cy="8" r="4" /><path d="M4 21v-2a8 8 0 0 1 16 0v2" /></>,
    logout: <><path d="M9 4H4v16h5M10 12h11m-4-4 4 4-4 4" /></>,
    chip: <><rect x="6" y="6" width="12" height="12" rx="2" /><rect x="9" y="9" width="6" height="6" rx="1" /><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" /></>,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>,
    chevron: <path d="m9 5 7 7-7 7" />,
    check: <path d="m5 12 4 4L19 6" />,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    copy: <><rect x="8" y="8" width="12" height="13" rx="2" /><path d="M15 8V3H3v12h5" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.info}</svg>;
}
export function Link({ to, children, className, label }: { to: string; children: ReactNode; className?: string; label?: string }) {
  return <a className={className} href={to} aria-label={label} onClick={event => { if (!event.ctrlKey && !event.metaKey && !event.shiftKey && event.button === 0) { event.preventDefault(); navigate(to); } }}>{children}</a>;
}
export function Field({ label, hint, children, className = '' }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return <label className={`field ${className}`}><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}
export function Empty({ title, detail, action }: { title: string; detail?: string; action?: ReactNode }) {
  return <div className="empty"><span className="empty-icon"><Icon name="nodes" size={28} /></span><h3>{title}</h3>{detail && <p>{detail}</p>}{action}</div>;
}
export function ErrorNotice({ text, retry }: { text?: string; retry?: () => void }) {
  return text ? <div className="notice error" role="alert"><Icon name="info" /><span>{text}</span>{retry && <button className="text-button" onClick={retry}>重试</button>}</div> : null;
}
export function Loading() { return <div className="loading" role="status"><span className="spinner" />正在加载…</div>; }
export function Modal({ title, children, onClose, wide = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useId();
  useEffect(() => { const element = dialog.current!; element.showModal(); return () => element.close(); }, []);
  return <dialog className={`modal ${wide ? 'wide' : ''}`} ref={dialog} aria-labelledby={heading} onCancel={event => { event.preventDefault(); onClose(); }} onClick={event => { if (event.target === event.currentTarget) { const box = event.currentTarget.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) onClose(); } }}><div className="modal-header"><h2 id={heading}>{title}</h2><button type="button" className="icon-button" aria-label="关闭" onClick={onClose}><Icon name="close" /></button></div>{children}</dialog>;
}
export function PageHeader({ eyebrow, title, description, children }: { eyebrow: string; title: string; description: string; children?: ReactNode }) {
  return <div className="page-heading"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div><div className="heading-actions">{children}</div></div>;
}
export function dateTime(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'; }
export function bytes(value?: number | null) { if (value == null || !Number.isFinite(value)) return '—'; const gib = value / 1024 ** 3; return `${gib >= 1024 ? (gib / 1024).toFixed(1) + ' TiB' : gib.toFixed(1) + ' GiB'}`; }
export function shortId(id: string | number) { return String(id).slice(0, 8); }
export function validMetric(quality?: string) { return ['ok', 'good', 'valid'].includes((quality || '').toLowerCase()); }
