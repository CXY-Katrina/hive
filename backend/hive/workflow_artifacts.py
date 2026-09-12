"""Bounded, immutable generic workflow artifacts; no business result inference."""
import hashlib
import json
import math
from pathlib import Path
import re

from .domain import DomainError, encode


MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')


def metric_document(raw):
    def pairs(values):
        result = {}
        for key,value in values:
            if key in result:
                raise ValueError()
            result[key]=value
        return result
    try:
        document = json.loads(raw.decode('utf-8'),object_pairs_hook=pairs)
        if (not isinstance(document,dict) or set(document)-{'metrics','verdict'}
                or not isinstance(document.get('metrics'),list) or len(document['metrics'])>10000
                or document.get('verdict','unknown') not in {'passed','failed','unknown'}):
            raise ValueError()
        for metric in document['metrics']:
            if (not isinstance(metric,dict) or set(metric)-{'name','value','unit','tags','verdict'}
                    or not isinstance(metric.get('name'),str) or not 1<=len(metric['name'])<=128
                    or type(metric.get('value')) not in (int,float) or not math.isfinite(metric['value'])
                    or not isinstance(metric.get('unit'),str) or len(metric['unit'])>64
                    or not isinstance(metric.get('tags'),dict) or len(metric['tags'])>32
                    or any(not isinstance(k,str) or not 1<=len(k)<=64 or not isinstance(v,str) or len(v)>256 for k,v in metric['tags'].items())
                    or metric.get('verdict','unknown') not in {'passed','failed','unknown'}):
                raise ValueError()
            metric.setdefault('verdict','unknown')
        document.setdefault('verdict','unknown')
        return document
    except (ValueError,TypeError,UnicodeError,RecursionError,OverflowError):
        raise DomainError('指标产物须符合通用 metrics JSON 格式；不推断业务结论，原文保留在归档中',422) from None


class WorkflowArtifacts:
    def __init__(self, runtime, data_dir):
        self.runtime = runtime
        self.root = Path(data_dir).resolve() / 'workflow-artifacts'

    def _directory(self, task_id, job_id, artifact_id=None):
        if any(not isinstance(value,str) or not IDENTIFIER.fullmatch(value) or value in {'.','..'}
               or value.endswith('.') or re.fullmatch(r'CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]',value.split('.')[0],re.I)
               for value in (task_id,job_id)):
            raise DomainError('无效的任务或 job 标识',422)
        if artifact_id is not None and (not isinstance(artifact_id,str) or not re.fullmatch(r'[0-9a-f]{64}',artifact_id)):
            raise DomainError('无效的产物标识',422)
        path = self.root / task_id / job_id
        if artifact_id:
            path /= artifact_id
        self._guard(path)
        return path

    def _guard(self, path):
        if not path.resolve().is_relative_to(self.root):
            raise DomainError('产物路径超出归档目录',409)
        for candidate in (path,*path.parents):
            if candidate.is_symlink() or getattr(candidate,'is_junction',lambda:False)():
                raise DomainError('产物归档路径不能包含符号链接或目录联接',409)
            if candidate == self.root:
                break

    def collect(self, node, identity, task_id, job_id, items):
        self._directory(task_id,job_id)
        if not isinstance(items,list) or len(items)>16:
            raise DomainError('每个 job 最多归档 16 个产物',422)
        seen = set()
        for item in items:
            if (not isinstance(item,dict) or set(item)!={'path','label','kind'}
                    or not isinstance(item['path'],str) or not item['path'].startswith('/')
                    or len(item['path'])>4096 or '\x00' in item['path']
                    or any(part in {'','.','..'} for part in item['path'][1:].split('/'))
                    or item['path'] in seen or not isinstance(item['label'],str) or not 1<=len(item['label'].strip())<=128
                    or item['kind'] not in {'file','metrics'}):
                raise DomainError('产物须有唯一的容器内绝对文件路径、label 和 file/metrics kind',422)
            seen.add(item['path'])
        artifacts, metrics, remaining = [], [], MAX_TOTAL
        for item in items:
            try:
                raw = self.runtime.read_file(node,identity,item['path'],max_bytes=min(MAX_FILE,remaining))
            except DomainError:
                raise
            except Exception:
                raise DomainError('产物读取失败；未确认归档完整',503) from None
            if not isinstance(raw,bytes) or len(raw)>min(MAX_FILE,remaining):
                raise DomainError('产物超过单文件 4 MiB 或总计 16 MiB 上限',422)
            remaining -= len(raw)
            artifact_id = hashlib.sha256(item['path'].encode()).hexdigest()
            meta = dict(id=artifact_id,path=item['path'],label=item['label'],kind=item['kind'],
                        size=len(raw),sha256=hashlib.sha256(raw).hexdigest(),container_id=identity['container_id'],
                        host_boot_id=identity['host_boot_id'])
            directory = self._directory(task_id,job_id,artifact_id)
            directory.mkdir(parents=True,exist_ok=True)
            self._guard(directory)
            content_path, metadata_path = directory/'content', directory/'metadata.json'
            for path in (content_path,metadata_path):
                self._guard(path)
            try:
                with content_path.open('xb') as file:
                    file.write(raw)
            except FileExistsError:
                if not content_path.is_file() or content_path.stat().st_size!=len(raw) or content_path.read_bytes()!=raw:
                    raise DomainError('同一路径的产物内容已经变化，保留原归档',409) from None
            try:
                with metadata_path.open('x',encoding='utf-8') as file:
                    file.write(encode(meta))
            except FileExistsError:
                if not metadata_path.is_file() or metadata_path.stat().st_size>16384:
                    raise DomainError('原归档元数据无效',409) from None
                try:
                    saved = json.loads(metadata_path.read_text(encoding='utf-8'))
                except (ValueError,UnicodeError):
                    raise DomainError('原归档元数据无效',409) from None
                if saved != meta:
                    raise DomainError('产物声明或容器身份与原归档不一致',409) from None
            artifacts.append(meta)
            if item['kind']=='metrics':
                metrics.append({'artifact_id':artifact_id,**metric_document(raw)})
        return {'artifacts':artifacts,'metrics':metrics}

    def read(self, task_id, job_id, artifact_id):
        directory = self._directory(task_id,job_id,artifact_id)
        content, metadata = directory/'content',directory/'metadata.json'
        for path in (content,metadata):
            self._guard(path)
        if not content.is_file() or not metadata.is_file():
            raise DomainError('归档产物不存在或尚未完整写入',404)
        if content.stat().st_size>MAX_FILE or metadata.stat().st_size>16384:
            raise DomainError('归档产物大小校验失败',409)
        try:
            saved = json.loads(metadata.read_text(encoding='utf-8'))
            raw = content.read_bytes()
            if saved['id']!=artifact_id or saved['size']!=len(raw) or saved['sha256']!=hashlib.sha256(raw).hexdigest():
                raise ValueError()
        except (ValueError,KeyError,TypeError):
            raise DomainError('归档产物校验失败',409) from None
        return {'path':content,'metadata':saved}
