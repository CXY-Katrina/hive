import { useRef, useState } from 'react';
import type { WorkflowStep } from '../workflowTypes';
import { ErrorNotice, Field } from './ui';

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
function Uploads({ label, files, onChange, onReading }: { label: string; files: NonNullable<WorkflowStep['files']>; onChange: (files: NonNullable<WorkflowStep['files']>) => void; onReading: (delta: number) => void }) {
  const [error, setError] = useState(''), [renaming, setRenaming] = useState<number>();
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
  return <div className="workflow-upload"><label className="upload-button"><span>+ 上传文件</span><input type="file" multiple aria-label={`${label} · 上传文件`} accept=".sh,.py,.yaml,.yml" onChange={event => void read(event.currentTarget)} /></label>{files.length > 0 && <div className="uploaded-files">{files.map((file, index) => <div className="uploaded-file" key={index}>{renaming === index ? <input aria-label={`文件名 ${file.name}`} value={file.name} required pattern="[A-Za-z0-9_./-]+" onChange={event => onChange(files.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} onBlur={() => setRenaming(undefined)} /> : <code>{file.name}</code>}<button type="button" className="text-button" aria-label={`重命名 ${file.name}`} onClick={() => setRenaming(index)}>重命名</button><button type="button" className="text-button danger-text" aria-label={`移除文件 ${file.name}`} onClick={() => onChange(files.filter((_, i) => i !== index))}>×</button></div>)}</div>}<ErrorNotice text={error} /></div>;
}
export function WorkflowFiles({ label, steps, onChange, onReading }: { label: string; steps: WorkflowStep[]; onChange: (steps: WorkflowStep[]) => void; onReading: (delta: number) => void }) {
  return <section className="workflow-files"><div className="workflow-row-heading"><h5>{label}</h5><button type="button" className="text-button" onClick={() => onChange([...steps, { type: 'shell', path: '', args: [], launch: '', files: [] }])}>+ 添加 {label}</button></div>{steps.map((step, index) => <div className="workflow-step compact-step" key={index}><div className="workflow-row-heading"><strong>步骤 {index + 1}</strong><button type="button" className="text-button danger-text" aria-label={`删除 ${label} ${index + 1}`} onClick={() => onChange(steps.filter((_, i) => i !== index))}>删除</button></div><Uploads label={`${label} ${index + 1}`} files={stepFiles(step)} onReading={onReading} onChange={files => onChange(steps.map((item, i) => i === index ? { ...item, path: '', launch: stepCommand(item), files, uploaded_content: undefined, inputs: undefined } : item))} /><Field label={`${label} ${index + 1} · 启动命令`}><textarea className="code-editor launch-editor" rows={2} spellCheck={false} value={stepCommand(step)} placeholder="bash run.sh 或 python3 runner.py config.yaml" onChange={event => onChange(steps.map((item, i) => i === index ? { ...item, path: '', launch: event.target.value, files: stepFiles(item), uploaded_content: undefined, inputs: undefined } : item))} /></Field></div>)}</section>;
}
