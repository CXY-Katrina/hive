from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, Any
import json
import uuid


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def uid() -> str:
    return str(uuid.uuid4())


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str, allow_nan=False)


def decode(value: Any, fallback=None):
    if value is None:
        return fallback
    return json.loads(value) if isinstance(value, (str, bytes)) else value


class DomainError(Exception):
    def __init__(self, message: str, code: int = 409):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Actor:
    id: str
    username: str
    admin: bool = False
    can_request: bool = False
    can_view_credentials: bool = False


SYSTEM = Actor("system", "system", True)


@dataclass
class Process:
    pid: int
    start_time: str
    boot_id: str
    device_ids: list[str]
    name: str = ""
    container_id: str | None = None
    container_name: str | None = None
    container_status: str = "unknown"


@dataclass
class DeviceSample:
    slot: str
    command_id: str
    chip_id: str
    logical_id: str
    memory_total: int
    memory_used: int | None
    ai_core: float | None
    health: str
    quality: str = "ok"
    reason: str = ""
    processes: list[Process] = field(default_factory=list)
    process_complete: bool = False
    extensions: dict = field(default_factory=dict)


@dataclass
class Snapshot:
    devices: list[DeviceSample]
    boot_id: str
    sampled_at: datetime
    quality: str = "ok"
    reason: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    code: int


class Transport(Protocol):
    def run(self, node: dict, script: str, timeout: int = 20) -> CommandResult: ...
    def put(self, node: dict, path: str, content: bytes) -> None: ...
    def read(self, node: dict, path: str, offset: int = 0, limit: int = 65536) -> bytes: ...


class HardwareAdapter(Protocol):
    name: str
    def collect(self, node: dict) -> Snapshot: ...
    def environment(self, devices: list[dict]) -> dict[str, str]: ...
    def connectivity(self, source: dict, target: dict, source_device: dict, target_device: dict) -> dict: ...
