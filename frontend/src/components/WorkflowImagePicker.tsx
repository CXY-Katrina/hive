import { useRef, useState } from 'react';
import { api, errorText } from '../api';
import { ErrorNotice, Field } from './ui';
interface ImageTag { name: string; image: string; last_modified?: string }
export function WorkflowImagePicker({label,value,onChange}:{label:string;value:string;onChange:(value:string)=>void}) {
  const [tags,setTags]=useState<ImageTag[]>([]), [search,setSearch]=useState(''), [page,setPage]=useState(0), [hasMore,setHasMore]=useState(true), [busy,setBusy]=useState(false), [error,setError]=useState('');
  const pending=useRef(false);
  const load=async()=>{
    if(pending.current)return; pending.current=true;setBusy(true);setError('');
    try { const data=await api<{tags:ImageTag[];has_more:boolean}>(`/images/vllm-ascend/tags?page=${page+1}`);setTags(values=>[...new Map([...values,...data.tags].map(tag=>[tag.name,tag])).values()]);setPage(page+1);setHasMore(data.has_more); }
    catch(err){setError(errorText(err));}finally{pending.current=false;setBusy(false);}
  };
  const matches=tags.filter(tag=>tag.name.toLowerCase().includes(search.toLowerCase()));
  return <div className="workflow-image-picker"><Field label={label}><input required value={value} onChange={event=>onChange(event.target.value)} placeholder="现有镜像、映射别名或 Quay 镜像" /></Field><details onToggle={event=>{if(event.currentTarget.open && !page)void load();}}><summary>搜索 Quay 镜像标签</summary><input aria-label="搜索镜像标签" value={search} onChange={event=>setSearch(event.target.value)} placeholder="搜索已加载的标签" /><div className="image-tag-options">{matches.map(tag=><button type="button" className="text-button" key={tag.name} title={tag.image} onClick={()=>onChange(tag.image)}>{tag.name}</button>)}</div>{!busy&&!matches.length&&<p className="muted small">暂无匹配标签，可继续加载或手工填写镜像。</p>}<ErrorNotice text={error} />{hasMore&&<button type="button" className="text-button" disabled={busy} onClick={()=>void load()}>{busy?'正在加载标签…':error?'重试加载标签':'加载更多标签'}</button>}<p className="muted small">优先使用本机镜像，缺失时拉取。</p></details></div>;
}
