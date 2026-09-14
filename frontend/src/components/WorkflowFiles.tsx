import { createContext, useContext, useRef, useState } from 'react';
import type { WorkflowStep } from '../workflowTypes';
import { ErrorNotice, Field, Modal } from './ui';

export const WorkflowFileContext = createContext<{ files: {name: string; content: string}[]; save: (name: string, content: string) => void } | undefined>(undefined);
const quote = (value: string) => /^[A-Za-z0-9_./:${}-]+$/.test(value) ? value : "'" + value.replace(/'/g, "'\\''") + "'";
export function stepCommand(step: WorkflowStep): string {
  if (step.launch?.trim() || !step.path) return step.launch || '';
  if (!step.path) return '';
  const runner = step.type === 'yaml' ? step.runner : undefined;
  const input = runner ? step.path : step.inputs?.[0]?.path;
  const args = [...(runner?.args || []), ...step.args].map(arg => input ? arg.replaceAll('${input}', input) : arg);
  return [runner?.type === 'python' || step.type === 'python' ? 'python3' : 'bash', runner?.path || step.path, ...args].map(quote).join(' ');
}
export function stepFiles(step: WorkflowStep): NonNullable<WorkflowStep['files']> {
  if (step.files?.length) return step.files;
  return [...(step.uploaded_content != null ? [{ name: step.path, content: step.uploaded_content }] : []), ...(step.inputs || []).filter(input => input.uploaded_content != null).map(input => ({ name: input.path, content: input.uploaded_content! }))];
}
function Uploads({ label, files, onChange, onReading, listOnly = false }: { listOnly?: boolean; label: string; files: NonNullable<WorkflowStep['files']>; onChange: (files: NonNullable<WorkflowStep['files']>) => void; onReading: (delta: number) => void }) {
  const [error, setError] = useState(''), [renaming, setRenaming] = useState<number>();
  const shared = useContext(WorkflowFileContext);
  const [editing, setEditing] = useState<{name:string;content:string}>();
  const saveEdit = () => { if (!editing) return; if (editing.content.includes('\0') || new TextEncoder().encode(editing.content).length > 256 * 1024) { setError('文件须为 UTF-8 文本，最多 256 KiB，不能包含空字符。'); return; } if (shared) shared.save(editing.name, editing.content); else onChange(files.map(file => file.name === editing.name ? editing : file)); setEditing(undefined); setError(''); };
  const latest = useRef({ files, onChange }); latest.current = { files, onChange };
  const read = async (input: HTMLInputElement) => {
    const values = Array.from(input.files || []); if (!values.length) return;
    setError(''); input.setCustomValidity(''); onReading(1);
    try {
      const loaded = await Promise.all(values.map(async file => {
        if (!/\.(sh|py|ya?ml)$/i.test(file.name)) throw new Error('请选择 Shell、Python 或 YAML 文件。');
        if (file.size > 256 * 1024) throw new Error('每个文件最多 256 KiB。');
        const content = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
        if (content.includes('\0')) throw new Error('文件须为 UTF-8 文本，不能包含空字符。');
        return { name: file.name, content };
      }));
      const merged = [...latest.current.files];
      loaded.forEach(file => { const existing = merged.findIndex(item => item.name === file.name); if (existing >= 0) merged[existing] = file; else merged.push(file); });
      latest.current.onChange(merged); input.value = '';
    } catch (err) { const reason = err instanceof Error ? err.message : '文件读取失败'; setError(reason); input.setCustomValidity(reason); }
    finally { onReading(-1); }
  };
  return <div className="workflow-upload">{!listOnly && <label className="upload-button"><span>+ 上传文件</span><input type="file" multiple aria-label={`${label} · 上传文件`} accept=".sh,.py,.yaml,.yml" onChange={event => void read(event.currentTarget)} /></label>}{files.length > 0 && <div className="uploaded-files">{files.map((file, index) => <div className="uploaded-file" key={index}>{renaming === index ? <input aria-label={`文件名 ${file.name}`} value={file.name} required pattern="[A-Za-z0-9_./-]+" onChange={event => onChange(files.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} onBlur={() => setRenaming(undefined)} /> : <code>{file.name}</code>}<button type="button" className="text-button" aria-label={`编辑 ${file.name}`} onClick={() => { setError(''); setEditing({ ...file }); }}>编辑</button>{!listOnly && <><button type="button" className="text-button" aria-label={`重命名 ${file.name}`} onClick={() => setRenaming(index)}>重命名</button><button type="button" className="text-button danger-text" aria-label={`移除文件 ${file.name}`} onClick={() => onChange(files.filter((_, i) => i !== index))}>×</button></>}</div>)}</div>}<ErrorNotice text={error} />{editing && <Modal title={`编辑文件 · ${editing.name}`} onClose={() => setEditing(undefined)}><div className="modal-body"><details><summary>浏览共享附件</summary><div className="workflow-file-navigation">{(shared?.files || files).map(file => <button type="button" className="text-button" key={file.name} onClick={() => { if (file.name === editing.name) return; const original = (shared?.files || files).find(value => value.name === editing.name); if (editing.content === original?.content || window.confirm('切换附件将放弃当前未保存的编辑，是否继续？')) setEditing({ ...file }); }}>{file.name}</button>)}</div></details><Field label="文件内容"><textarea className="code-editor file-content-editor" spellCheck={false} value={editing.content} onChange={event => setEditing({ ...editing, content: event.target.value })} /></Field><p className="muted small">保存会同步本任务中所有同名附件，脚本由你编辑的内容执行。</p><ErrorNotice text={error} /></div><div className="modal-actions"><button type="button" className="button secondary" aria-label="取消" onClick={() => setEditing(undefined)}>取消</button><button type="button" className="button primary" onClick={saveEdit}>保存文件</button></div></Modal>}</div>;
}
export function WorkflowFiles({ label, steps, onChange, onReading, single = false }: { single?: boolean; label: string; steps: WorkflowStep[]; onChange: (steps: WorkflowStep[]) => void; onReading: (delta: number) => void }) {
  return <section className="workflow-files"><div className="workflow-row-heading"><h5>{label}</h5>{!single && <button type="button" className="text-button" onClick={() => onChange([...steps, { type: 'shell', path: '', args: [], launch: '', files: [] }])}>+ 添加 {label}</button>}</div>{steps.map((step, index) => <div className="workflow-step compact-step" key={index}><div className="workflow-row-heading"><strong>步骤 {index + 1}</strong>{!single && <button type="button" className="text-button danger-text" aria-label={`删除 ${label} ${index + 1}`} onClick={() => onChange(steps.filter((_, i) => i !== index))}>删除</button>}</div><Uploads label={`${label} ${index + 1}`} files={stepFiles(step)} onReading={onReading} onChange={files => onChange(steps.map((item, i) => i === index ? { ...item, path: '', launch: stepCommand(item), files, uploaded_content: undefined, inputs: undefined } : item))} /><Field label={`${label} ${index + 1} · 启动命令`}><textarea className="code-editor launch-editor" rows={2} spellCheck={false} value={stepCommand(step)} placeholder="bash run.sh 或 python3 runner.py config.yaml" onChange={event => onChange(steps.map((item, i) => i === index ? { ...item, path: '', launch: event.target.value, files: stepFiles(item), uploaded_content: undefined, inputs: undefined } : item))} /></Field></div>)}</section>;
}

export function WorkflowTaskFiles({files}:{files:NonNullable<WorkflowStep['files']>}) {
 return <section className="workflow-block task-file-list" aria-label="任务文件"><div className="workflow-block-heading"><h3>任务文件</h3><span className="count-chip">{files.length}</span></div><p className="muted small">包含各步骤使用的 Shell、Python 和 YAML 文件。编辑保存会同步所有同名附件。</p>{files.length ? <Uploads label="任务文件" files={files} listOnly onChange={()=>{}} onReading={()=>{}} /> : <p className="muted small">当前未附加脚本，可在步骤中上传文件或载入预置。</p>}</section>;
}
