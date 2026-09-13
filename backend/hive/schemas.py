import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from .node_mapping_schemas import MappingEntry, MappingsUpdate


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    username: str
    password: str = Field(default='', max_length=72)


class MemberPermissions(Input):
    can_request: bool
    can_view_credentials: bool


class NodeConnection(Input):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="root", pattern=r"^[a-z_][a-z0-9_-]{0,63}$")
    password: str = Field(min_length=1, max_length=4096)
    host_key_fingerprint: str | None = Field(default=None, pattern=r'^SHA256:[A-Za-z0-9+/]{43}$')

    @field_validator('host')
    @classmethod
    def valid_host(cls, value):
        import ipaddress
        value = value.strip()
        try:
            ipaddress.ip_address(value)
        except ValueError:
            if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?', value):
                raise ValueError('请输入有效 IP 或主机名')
        return value


class NodeCreate(NodeConnection):
    name: str = Field(min_length=1, max_length=128)
    generation: str = Field(min_length=1,max_length=16,pattern=r"^[A-Za-z0-9_.-]+$")
    adapter: str = Field(default="ascend",pattern=r"^[a-z0-9_.-]{1,64}$")
    vendor: str = Field(default="ascend",pattern=r"^[a-z0-9_.-]{1,64}$")
    device_kind: str = Field(default="npu",pattern=r"^[a-z0-9_.-]{1,32}$")
    model: str = Field(default='', max_length=128)
    cluster_name: str = Field(default="default", min_length=1, max_length=128)
    mappings: list[MappingEntry] | None = Field(default=None, max_length=128)

    @field_validator('mappings')
    @classmethod
    def valid_resource_mappings(cls, entries):
        return MappingsUpdate(version=0, entries=entries).entries if entries is not None else None


class ComputeSpec(Input):
    fp16_tflops_per_module: float = Field(gt=0, le=1000000, allow_inf_nan=False)
    source: str = Field(min_length=1, max_length=512)

    @field_validator('source')
    @classmethod
    def nonempty_source(cls, value):
        if not value.strip():
            raise ValueError('请填写算力规格来源')
        return value.strip()


class NodeUpdate(Input):
    maintenance: bool | None = None
    password: str | None = Field(default=None, min_length=1, max_length=4096)
    ssh_user: str | None = Field(default=None, pattern=r"^[a-z_][a-z0-9_-]{0,63}$")
    hccn_ids: dict[str, int] | None = None
    compute_spec: ComputeSpec | None = None
    clear_compute_spec: bool = False

    @model_validator(mode='after')
    def one_compute_action(self):
        if self.clear_compute_spec and self.compute_spec:
            raise ValueError('不能同时登记和清除算力规格')
        return self

    @field_validator("hccn_ids")
    @classmethod
    def valid_mapping(cls, mapping):
        if mapping is not None and (len(mapping)>128 or any(not re.fullmatch(r"[0-9]+:[0-9]+", k) or v<0 or v>1024 for k,v in mapping.items())):
            raise ValueError("无效 hccn 卡号映射")
        return mapping


class ResourceSpec(Input):
    target_node_ids: list[str] = Field(default_factory=list, max_length=64)
    generation: str = Field(min_length=1,max_length=16,pattern=r"^[A-Za-z0-9_.-]+$")
    vendor: str = Field(default="ascend",pattern=r"^[a-z0-9_.-]{1,64}$")
    device_kind: str = Field(default="npu",pattern=r"^[a-z0-9_.-]{1,32}$")
    model: str | None = Field(default=None, max_length=128)
    mode: Literal["whole", "partial"] = "partial"
    machine_count: int = Field(default=1, ge=1, le=64)
    cards_per_node: int = Field(default=1, ge=1, le=128)
    min_memory_gib: float = Field(default=0, ge=0, le=65536, allow_inf_nan=False)
    require_interconnect: bool = False
    queue: bool = True
    wait_minutes: int | None = Field(default=None, ge=1, le=10080)
    purpose: Literal["debug", "task"] = "debug"
    shared_storage_id: str | None = Field(default=None, max_length=128)
    note: str = Field(default="", max_length=1024)

    @model_validator(mode="after")
    def single_node(self):
        if self.target_node_ids and (len(set(self.target_node_ids)) != len(self.target_node_ids) or len(self.target_node_ids) < self.machine_count or any(not re.fullmatch(r'[0-9a-f-]{36}', node) for node in self.target_node_ids)):
            raise ValueError('指定节点需唯一、有效且数量不少于申请机器数')
        if self.machine_count == 1:
            self.require_interconnect = False
        return self


class RequestCreate(Input):
    idempotency_key: str = Field(min_length=1, max_length=128)
    spec: ResourceSpec


class TaskCreate(Input):
    idempotency_key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    script: str = Field(min_length=1, max_length=262144)
    workdir: str = Field(default="/tmp", min_length=1, max_length=512)
    environment: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=3600, ge=1, le=604800)
    resource: ResourceSpec

    @field_validator("workdir")
    @classmethod
    def absolute_workdir(cls, value):
        if not value.startswith("/") or "\x00" in value or "\n" in value:
            raise ValueError("工作目录必须为 Linux 绝对路径")
        return value

    @field_validator("environment")
    @classmethod
    def env_names(cls, value):
        if len(value)>128:
            raise ValueError("环境变量过多")
        for key, val in value.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or "\x00" in val or len(val)>16384:
                raise ValueError("无效环境变量")
            if key.startswith("HIVE_") or key == "ASCEND_RT_VISIBLE_DEVICES":
                raise ValueError("分配设备与 HIVE_ 变量由平台设置")
        return value
