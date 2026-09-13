import { useCallback, useEffect, useRef, useState } from 'react';
import type { ResourceSpec } from './types';
import type { WorkflowEnvironment, WorkflowJob, WorkflowSource } from './workflowTypes';
import { defaultResource } from './components/ResourceForm';

export interface WorkflowDraft {
  name: string; pr: string; source?: WorkflowSource; environments: WorkflowEnvironment[]; jobs: WorkflowJob[];
  reuse: boolean; spaceId: string; retainMinutes: number; presetId?: string | number; presetName?: string;
  presetTags?: Record<string, string>; selectedEnvironment: number; selectedJobId?: string;
}
interface RequestDraft { version: 1; resource: ResourceSpec; createTask: boolean; workflow?: WorkflowDraft }
const memory = new Map<string, RequestDraft>();
let opening: Promise<IDBDatabase> | undefined;
function database() {
  if (!opening) opening = new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open('hive-request-drafts', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('drafts');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error('浏览器草稿存储被其他页面占用'));
  });
  return opening;
}
const empty = (): RequestDraft => ({ version: 1, resource: defaultResource(), createTask: false });
async function load(username: string): Promise<RequestDraft> {
  if (memory.has(username)) return memory.get(username)!;
  const db = await database();
  const value = await new Promise<RequestDraft | undefined>((resolve, reject) => {
    const request = db.transaction('drafts').objectStore('drafts').get(username);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  const valid = value?.version === 1 && value.resource && typeof value.createTask === 'boolean' && (!value.workflow || Array.isArray(value.workflow.jobs) && Array.isArray(value.workflow.environments));
  const result = valid ? value! : empty(); memory.set(username, result); return result;
}
async function save(username: string, value: RequestDraft) {
  const db = await database();
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction('drafts', 'readwrite');
    transaction.objectStore('drafts').put(value, username);
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error);
    transaction.onerror = () => reject(transaction.error);
  });
}

export function useRequestDraft(username: string) {
  const [value, setValue] = useState<RequestDraft>(() => memory.get(username) || empty());
  const [readyUser, setReadyUser] = useState(''), [error, setError] = useState(''), [saved, setSaved] = useState(false);
  const current = useRef(value), sequence = useRef(0);
  useEffect(() => {
    let active = true;
    void load(username).then(draft => { if (active) { current.current = draft; setValue(draft); setReadyUser(username); setSaved(Boolean(draft.workflow)); } }).catch(() => {
      if (active) { const draft = memory.get(username) || empty(); current.current = draft; setValue(draft); setReadyUser(username); setError('浏览器无法保存草稿，当前页面的输入仍可继续使用。'); }
    });
    return () => { active = false; };
  }, [username]);
  const update = useCallback((change: (previous: RequestDraft) => RequestDraft) => {
    const next = change(current.current); current.current = next; memory.set(username, next); setValue(next); setSaved(false);
    const revision = ++sequence.current;
    void save(username, next).then(() => { if (sequence.current === revision) { setSaved(true); setError(''); } }).catch(() => { if (sequence.current === revision) setError('草稿未能保存到浏览器，请检查可用空间；当前页面仍保留输入。'); });
  }, [username]);
  return { value, update, ready: readyUser === username, error, saved };
}
