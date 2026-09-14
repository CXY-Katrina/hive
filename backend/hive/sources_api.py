"""Source routes kept separate from resource and execution orchestration."""
from fastapi import Depends
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .domain import DomainError
from .sources import validate_file_request


class ResolveSource(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    pr: int | str | None = None
    commit: str | None = Field(default=None, pattern=r'^[0-9a-f]{40}$')
    branch: Literal['main'] | None = None
    revision: Literal['head', 'merged', 'commit', 'branch'] = 'head'

    @model_validator(mode='after')
    def one_source(self):
        if (sum(value is not None for value in (self.pr, self.commit, self.branch)) != 1
                or self.revision == 'commit' and self.commit is None
                or self.revision == 'branch' and self.branch is None):
            raise ValueError('请选择 main、PR 或完整 commit SHA 中的一种来源')
        return self


class SourceFile(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source: dict
    path: str


def register_source_routes(app, services, current, respond):
    @app.post('/api/sources/resolve')
    def resolve(body: ResolveSource, actor=Depends(current)):
        return respond(services.sources.resolve_spec(body.model_dump(exclude_none=True)))

    @app.post('/api/sources/file')
    def file(body: SourceFile, actor=Depends(current)):
        validate_file_request(body.source, body.path)
        current_source = services.sources.resolve_spec(body.source)
        if any(body.source[key] != current_source[key] for key in ('head_sha', 'vllm_sha', 'commit_file_sha256')):
            raise DomainError('来源已变化或提交 SHA 不匹配，请重新解析后查看文件', 409)
        return respond(services.sources.file(current_source, body.path))
