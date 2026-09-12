export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
  });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === 'string') message = payload.detail;
      else if (Array.isArray(payload.detail)) message = payload.detail.map((item: { msg?: string }) => item.msg || '输入无效').join('；');
      else if (typeof payload.message === 'string') message = payload.message;
    } catch { /* Preserve HTTP status when a proxy returned a non-JSON error. */ }
    if (response.status === 401 && path !== '/session') window.dispatchEvent(new Event('hive:session-expired'));
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const post = <T,>(path: string, value?: unknown) => api<T>(path, { method: 'POST', ...(value === undefined ? {} : { body: JSON.stringify(value) }) });
export const patch = <T,>(path: string, value: unknown) => api<T>(path, { method: 'PATCH', body: JSON.stringify(value) });
export const errorText = (error: unknown) => error instanceof Error ? error.message : '操作未完成，请稍后重试';
export function operationKey() { return crypto.randomUUID(); }
