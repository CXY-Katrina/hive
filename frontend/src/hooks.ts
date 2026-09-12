import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorText } from './api';

export function useQuery<T>(path: string | null, interval = 0) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(Boolean(path));
  const [updatedAt, setUpdatedAt] = useState<Date>();
  const controller = useRef<AbortController | null>(null);
  const reload = useCallback(async () => {
    if (!path) return;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    try {
      const result = await api<T>(path, { signal: current.signal });
      if (!current.signal.aborted) { setData(result); setError(''); setUpdatedAt(new Date()); }
    } catch (err) {
      if (!current.signal.aborted) setError(errorText(err));
    } finally { if (!current.signal.aborted) setLoading(false); }
  }, [path]);
  useEffect(() => {
    setData(undefined); setError(''); setUpdatedAt(undefined);
    void reload();
    const timer = interval ? window.setInterval(() => { if (!document.hidden) void reload(); }, interval) : undefined;
    const onVisible = () => { if (interval && !document.hidden) void reload(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => { controller.current?.abort(); clearInterval(timer); document.removeEventListener('visibilitychange', onVisible); };
  }, [reload, interval]);
  return { data, error, loading, updatedAt, reload };
}

export function navigate(path: string) {
  window.history.pushState(null, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}
export function usePath() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => { const sync = () => setPath(window.location.pathname); window.addEventListener('popstate', sync); return () => window.removeEventListener('popstate', sync); }, []);
  return path;
}
