"""Resource alias declarations are data; registering them executes no node commands."""
from typing import Literal
import json
import posixpath
import re
from pydantic import BaseModel, ConfigDict, Field, model_validator


class MappingEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['model', 'dataset', 'image', 'package']
    name: str = Field(min_length=1, max_length=255)
    target: str = Field(min_length=1, max_length=4096)

    @model_validator(mode='after')
    def valid_mapping(self):
        self.name = self.name.strip()
        self.target = self.target.strip()
        if self.kind in {'model', 'dataset'}:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', self.name):
                raise ValueError('ModelScope 名称需为组织/名称')
        elif not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,254}', self.name):
            raise ValueError('资源逻辑名称无效')
        if self.kind == 'image':
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}', self.target):
                raise ValueError('请填写本机镜像名、标签或摘要')
        else:
            if (not self.target.startswith('/') or self.target.startswith('//') or '\\' in self.target
                or '..' in self.target.split('/') or any(ord(char) < 32 or ord(char) == 127 for char in self.target)):
                raise ValueError('资源位置需为本机绝对路径，禁止路径跳转和控制字符')
            self.target = posixpath.normpath(self.target)
        return self


class MappingsUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=0, strict=True)
    entries: list[MappingEntry] = Field(max_length=128)

    @model_validator(mode='after')
    def unique_aliases(self):
        identities = {(item.kind, item.name) for item in self.entries}
        if len(identities) != len(self.entries):
            raise ValueError('同一资源类型中的逻辑名称不能重复')
        if len(json.dumps([item.model_dump() for item in self.entries], ensure_ascii=False,
                          separators=(',', ':')).encode()) > 16384:
            raise ValueError('资源映射总量最多 16 KiB')
        return self
