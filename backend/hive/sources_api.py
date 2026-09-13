"""Source routes kept separate from resource and execution orchestration."""
from fastapi import Depends
from typing import Literal
from pydantic import BaseModel, ConfigDict
from .domain import DomainError
from .sources import validate_file_request


class ResolveSource(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    pr: int | str
    revision: Literal['head', 'merged'] = 'head'


class SourceFile(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source: dict
    path: str


def register_source_routes(app, services, current, respond):
    @app.post('/api/sources/resolve')
    def resolve(body: ResolveSource, actor=Depends(current)):
        return respond(services.sources.resolve(body.pr, revision=body.revision))

    @app.post('/api/sources/file')
    def file(body: SourceFile, actor=Depends(current)):
        validate_file_request(body.source, body.path)
        current_source = services.sources.resolve(body.source['pr'], revision=body.source.get('revision','head'))
        if any(body.source[key] != current_source[key] for key in ('head_sha', 'vllm_sha', 'commit_file_sha256')):
            raise DomainError('PR 来源已变化或提交 SHA 不匹配，请重新解析后查看文件', 409)
        return respond(services.sources.file(current_source, body.path))
