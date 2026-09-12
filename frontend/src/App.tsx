import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api, ApiError, errorText, post } from './api';
import { navigate, usePath } from './hooks';
import type { User } from './types';
import { ErrorNotice, Icon, Link, Loading } from './components/ui';
import { NodesPage } from './pages/NodesPage';
import { ClustersPage } from './pages/ClustersPage';
import { MembersPage } from './pages/MembersPage';
import { RequestsPage } from './pages/RequestsPage';

const navigation = [
  { path: '/nodes/', label: '实时信息', icon: 'nodes' },
  { path: '/clusters', label: '集群管理', icon: 'server' },
  { path: '/requests', label: '机器申请', icon: 'request' },
  { path: '/members', label: '人员管理', icon: 'user' },
];

// Keep the task implementation available for a later release, without exposing
// its page, navigation or login return target in the current workspace.
function workspacePath(path: string) {
  return path === '/' || path === '/login' || /^\/tasks(?:\/|$)/.test(path) ? '/nodes/' : path;
}

export default function App() {
  const path = usePath();
  const [user, setUser] = useState<User | null>();
  const [error, setError] = useState('');
  const initialPath = useRef(workspacePath(path));

  const loadSession = async () => {
    setError('');
    try { setUser(await api<User>('/session')); }
    catch (err) {
      if (err instanceof ApiError && err.status === 401) setUser(null);
      else setError(errorText(err));
    }
  };

  useEffect(() => { void loadSession(); }, []);
  useEffect(() => {
    if (!user) return;
    const refresh = () => { if (!document.hidden) void api<User>('/session').then(setUser).catch(err => { if (err instanceof ApiError && err.status === 401) setUser(null); }); };
    const timer = window.setInterval(refresh, 15000);
    window.addEventListener('focus', refresh);
    return () => { window.clearInterval(timer); window.removeEventListener('focus', refresh); };
  }, [user?.id]);
  useEffect(() => {
    const expire = () => {
      initialPath.current = workspacePath(window.location.pathname);
      setUser(null);
      navigate('/login', true);
    };
    window.addEventListener('hive:session-expired', expire);
    return () => window.removeEventListener('hive:session-expired', expire);
  }, []);
  useEffect(() => {
    if (user === null && path !== '/login') navigate('/login', true);
    if (user && (path === '/' || path === '/login')) navigate(initialPath.current, true);
    else if (user && workspacePath(path) !== path) navigate(workspacePath(path), true);
  }, [user, path]);

  const logout = async () => {
    setError('');
    try {
      await api('/session', { method: 'DELETE' });
      initialPath.current = '/nodes/';
      setUser(null);
    } catch (err) { setError(errorText(err)); }
  };

  if (user === undefined) {
    return <div className="boot">
      <img src="/hive.svg" alt="" /><h1>Hive</h1>
      {error ? <ErrorNotice text={error} retry={() => void loadSession()} /> : <Loading />}
    </div>;
  }
  if (!user) return <Login onLogin={value => { setUser(value); navigate(initialPath.current, true); }} />;

  const visibleNavigation = navigation.filter(item => item.path !== '/members' || user.admin);
  const active = visibleNavigation.find(item => path.startsWith(item.path.replace(/\/$/, '')));
  return <div className="app-shell">
    <aside className="sidebar">
      <Link to="/nodes/" className="brand">
        <img src="/hive.svg" alt="" />
        <span>Hive<small>NPU RESOURCE PLATFORM</small></span>
      </Link>
      <div className="nav-caption">WORKSPACE / 工作空间</div>
      <nav aria-label="主导航">
        {visibleNavigation.map((item, index) =>
          <Link key={item.path} to={item.path} label={item.label}
            className={`nav-link ${active?.path === item.path ? 'active' : ''}`}>
            <Icon name={item.icon} /><span>{item.label}</span>
            <small className="nav-index" aria-hidden="true">0{index + 1}</small>
            {active?.path === item.path && <span className="nav-dot" />}
          </Link>
        )}
      </nav>
      <div className="sidebar-bottom">
        <Icon name="task" size={15} />
        <div>CLUSTER WORKSPACE<small>资源协作终端</small></div>
        <span className="version">v0.1</span>
      </div>
    </aside>
    <div className="main-shell">
      <header className="topbar">
        <div className="breadcrumb">工作空间<Icon name="chevron" size={14} /><span>{active?.label || '页面'}</span></div>
        <span className="console-label" aria-hidden="true">[ HIVE / RESOURCE CONSOLE ]</span>
        <div className="user-menu">
          <span className="avatar">{Array.from(user.username)[0]}</span>
          <div><strong>{user.username}</strong><small>{user.admin ? '管理员' : '协作成员'}</small></div>
          <button className="icon-button" onClick={() => void logout()} title="退出登录" aria-label="退出登录">
            <Icon name="logout" size={18} />
          </button>
        </div>
      </header>
      <main>
        <ErrorNotice text={error} />
        {path.startsWith('/nodes') ? <NodesPage nodeId={path.split('/').filter(Boolean)[1]} />
          : path.startsWith('/clusters') ? <ClustersPage user={user} />
          : path.startsWith('/members') ? user.admin ? <MembersPage /> : <div className="notice error">此页面仅管理员可访问。</div>
          : path.startsWith('/requests') ? <RequestsPage user={user} />
          : workspacePath(path) !== path ? <Loading />
          : <div className="panel empty"><h1>页面不存在</h1><Link to="/nodes/">返回实时信息</Link></div>}
      </main>
      <footer>HIVE / RESOURCE NETWORK<span>让资源清晰，让协作有序。</span></footer>
    </div>
  </div>;
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (!username.trim() || /[\u0000-\u001f\u007f]/.test(username)) {
      setError('请输入有效的用户名。');
      return;
    }
    setBusy(true); setError('');
    try { onLogin(await post<User>('/session', { username: username.trim(), password })); }
    catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  };

  return <div className="login-page">
    <section className="login-story">
      <a className="brand" href="/login">
        <img src="/hive.svg" alt="" />
        <span>Hive<small>NPU RESOURCE PLATFORM</small></span>
      </a>
      <div className="story-copy">
        <span className="eyebrow">YOUR NEXT COMPUTE / 算力协作空间</span>
        <h1>连接算力。<br /><span>即刻进入状态。</span></h1>
        <p>看见每张卡的实时状态，<br />为下一次探索，找到合适的资源。</p>
        <div className="terminal-art" aria-hidden="true">
          <div className="terminal-bar"><i /><i /><i /><span>hive / resource-workspace</span></div>
          <div className="terminal-lines">
            <div className="terminal-line"><strong>01 / OBSERVE · 实时节点</strong><small>SoC version / AI Core / HBM</small></div>
            <div className="terminal-line"><strong>02 / REQUEST · 资源申请</strong><small>整机或部分卡 · 多机互联</small></div>
            <div className="terminal-line"><strong>03 / CONNECT · 开始协作</strong><small>SSH 连接 · 共享工作空间</small></div>
          </div>
        </div>
      </div>
      <p className="story-footer">COMPUTE TOGETHER<span>让每一份算力，各就其位。</span></p>
    </section>
    <section className="login-form-area">
      <form className="login-form" onSubmit={event => void submit(event)}>
        <span className="eyebrow">ACCESS / 欢迎来到 HIVE</span>
        <h2>进入工作空间</h2>
        <p>使用你的用户名，记录每一次资源申请。</p>
        <label className="field">
          <span>用户名</span>
          <div className="input-icon">
            <Icon name="user" />
            <input autoFocus autoComplete="username" required maxLength={64} value={username}
              onChange={event => setUsername(event.target.value)} placeholder="请输入你的用户名" />
          </div>
        </label>
        <label className="field"><span>管理员密码</span><input type="password" autoComplete="current-password" maxLength={72} value={password} onChange={event => setPassword(event.target.value)} placeholder="管理员必填，普通成员留空" /></label>
        <ErrorNotice text={error} />
        <button className="button primary login-submit" disabled={busy}>
          {busy ? '正在进入…' : '进入平台'}<Icon name="arrow" size={18} />
        </button>
        <p className="login-note">管理员需输入密码。普通成员使用固定用户名登记，权限由管理员开启。</p>
      </form>
      <span className="login-copyright">Hive · 团队 NPU 资源工作台</span>
    </section>
  </div>;
}
