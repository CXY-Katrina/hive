import { useEffect, useState } from 'react';
import type { WorkflowStep } from '../workflowTypes';
import { ErrorNotice, Field } from './ui';

export function JsonField({ label, value, onChange, kind = 'array' }: { label: string; value: unknown; onChange: (value: unknown) => void; kind?: 'array' | 'object' }) {
  const serialized = JSON.stringify(value);
  const [text, setText] = useState(serialized);
  useEffect(() => setText(serialized), [serialized]);
  return <Field label={label}><textarea aria-label={label} className="code-editor" rows={2} spellCheck={false} value={text} onChange={event => {
    setText(event.target.value);
    try {
      const next = JSON.parse(event.target.value);
      if (kind === 'array' ? !Array.isArray(next) || next.some(item => typeof item !== 'string') : !next || typeof next !== 'object' || Array.isArray(next) || Object.values(next).some(item => typeof item !== 'string')) throw new Error();
      event.target.setCustomValidity(''); onChange(next);
    } catch { event.target.setCustomValidity(kind === 'array' ? '请输入字符串组成的 JSON 数组，例如 ["--flag", "value"]。' : '请输入值为字符串的 JSON 对象，例如 {"KEY":"value"}。'); }
  }} /></Field>;
}

function UploadFile({ label, type, onRead, onReading }: { label: string; type: WorkflowStep['type']; onRead: (content: string, name: string) => void; onReading: (delta: number) => void }) {
  const [error, setError] = useState('');
  const [size, setSize] = useState<number>();
  const upload = async (input: HTMLInputElement) => {
    const file = input.files?.[0]; if (!file) return;
    setError(''); setSize(undefined); input.setCustomValidity(''); onReading(1);
    try {
      if (file.size > 256 * 1024) throw new Error('每个文件最多 256 KiB。');
      const extension = file.name.split('.').pop()?.toLowerCase();
      if (!(type === 'shell' ? ['sh'] : type === 'python' ? ['py'] : ['yaml', 'yml']).includes(extension || '')) throw new Error('文件扩展名与所选类型不一致。');
      const bytes = await file.arrayBuffer();
      const content = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
      if (content.includes('\0')) throw new Error('不接受包含空字符的二进制文件。');
      onRead(content, file.name); setSize(file.size);
    } catch (err) { const message = err instanceof Error ? err.message : '无法读取文件'; setError(message); input.setCustomValidity(message); }
    finally { onReading(-1); }
  };
  return <div className="workflow-upload"><label className="field"><span>{label}</span><input type="file" aria-label={label} accept={type === 'shell' ? '.sh' : type === 'python' ? '.py' : '.yaml,.yml'} onChange={event => void upload(event.currentTarget)} /></label>{size != null && <small>已读取 {size} B · 与 PR 文件关联核验</small>}<ErrorNotice text={error} /></div>;
}

export function WorkflowFiles({ label, steps, onChange, onReading }: { label: string; steps: WorkflowStep[]; onChange: (steps: WorkflowStep[]) => void; onReading: (delta: number) => void }) {
  const update = (index: number, value: Partial<WorkflowStep>) => onChange(steps.map((step, i) => i === index ? { ...step, ...value } : step));
  const move = (index: number, offset: number) => { const next = [...steps]; [next[index], next[index + offset]] = [next[index + offset], next[index]]; onChange(next); };
  return <section className="workflow-files"><div className="workflow-row-heading"><h5>{label}</h5><button type="button" className="text-button" onClick={() => onChange([...steps, { type: 'shell', path: '', args: [] }])}>添加 {label}文件</button></div>{steps.map((step, index) => {
    const prefix = `${label} ${index + 1}`;
    return <div className="workflow-step" key={index}><div className="workflow-row-heading"><strong>{index + 1} / {step.type.toUpperCase()}</strong><div className="toolbar-actions"><button type="button" className="text-button" disabled={index === 0} aria-label={`${prefix} 上移`} onClick={() => move(index, -1)}>↑</button><button type="button" className="text-button" disabled={index === steps.length - 1} aria-label={`${prefix} 下移`} onClick={() => move(index, 1)}>↓</button><button type="button" className="text-button danger-text" aria-label={`删除 ${prefix}`} onClick={() => onChange(steps.filter((_, i) => i !== index))}>删除</button></div></div>
      <div className="form-grid"><Field label={`${prefix} · 文件类型`}><select value={step.type} onChange={event => update(index, { type: event.target.value as WorkflowStep['type'], uploaded_content: undefined, runner: event.target.value === 'yaml' ? { type: 'python', path: '', args: ['${input}'] } : undefined })}><option value="shell">Shell</option><option value="python">Python</option><option value="yaml">YAML + 外部执行器</option></select></Field><Field label={`${prefix} · PR 路径`}><input required aria-label={`${prefix} · PR 路径`} value={step.path} onChange={event => update(index, { path: event.target.value })} placeholder="ci/run.sh" /></Field></div>
      {step.type === 'yaml' && <div className="form-grid"><Field label={`${prefix} · 执行器类型`}><select value={step.runner?.type || 'python'} onChange={event => update(index, { runner: { type: event.target.value as 'shell' | 'python', path: step.runner?.path || '', args: step.runner?.args || ['${input}'] } })}><option value="python">Python</option><option value="shell">Shell</option></select></Field><Field label={`${prefix} · YAML 执行入口`}><input required value={step.runner?.path || ''} onChange={event => update(index, { runner: { type: step.runner?.type || 'python', path: event.target.value, args: step.runner?.args || ['${input}'] } })} placeholder="ci/runner.py" /></Field></div>}
      <JsonField label={`${prefix} · 参数（JSON 数组）`} value={step.type === 'yaml' ? step.runner?.args || ['${input}'] : step.args} onChange={value => step.type === 'yaml' ? update(index, { runner: { type: step.runner?.type || 'python', path: step.runner?.path || '', args: value as string[] } }) : update(index, { args: value as string[] })} />
      <UploadFile label={`${prefix} · 上传文件`} type={step.type} onReading={onReading} onRead={(content, filename) => update(index, { uploaded_content: content, path: step.path || filename })} />
      <details><summary>附加 YAML 输入文件</summary>{step.inputs?.map((input, inputIndex) => <div className="workflow-input" key={inputIndex}><Field label={`${prefix} 输入 ${inputIndex + 1} · PR 路径`}><input required value={input.path} onChange={event => update(index, { inputs: step.inputs!.map((item, i) => i === inputIndex ? { ...item, path: event.target.value } : item) })} /></Field><UploadFile label={`${prefix} 输入 ${inputIndex + 1} · 上传文件`} type="yaml" onReading={onReading} onRead={(content, filename) => update(index, { inputs: step.inputs!.map((item, i) => i === inputIndex ? { ...item, path: item.path || filename, uploaded_content: content } : item) })} /><button type="button" className="text-button" onClick={() => update(index, { inputs: step.inputs!.filter((_, i) => i !== inputIndex) })}>删除输入文件</button></div>)}<button type="button" className="text-button" onClick={() => update(index, { inputs: [...(step.inputs || []), { type: 'yaml', path: '' }] })}>添加 {prefix} 输入文件</button></details>
    </div>;
  })}</section>;
}
