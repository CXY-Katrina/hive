from dataclasses import dataclass, field
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "hive"
    mysql_password: str = ""
    mysql_database: str = "hive"
    secret_key: str = ""
    admin_users: tuple[str, ...] = ("admin",)
    origin: str = "http://127.0.0.1:18000"
    cookie_secure: bool = True
    known_hosts: Path = Path("data/known_hosts")
    data_dir: Path = Path("data")
    sample_seconds: int = 15
    stale_seconds: int = 45
    ssh_timeout: int = 20
    ssh_workers: int = 8

    @classmethod
    def from_env(cls, env_file=None):
        file=Path(env_file or ".env")
        if file.exists():
            for line in file.read_text(encoding="utf-8-sig").splitlines():
                line=line.strip()
                if not line or line.startswith("#"):
                    continue
                key,separator,value=line.partition("=")
                key,value=key.strip(),value.strip()
                if not separator or not key.startswith("HIVE_"):
                    raise ValueError("Invalid Hive environment file entry")
                if len(value)>=2 and value[0]==value[-1] and value[0] in {"'",'"'}:
                    value=value[1:-1]
                os.environ.setdefault(key,value)
        values = {}
        for name in cls.__dataclass_fields__:
            raw = os.getenv("HIVE_" + name.upper())
            if raw is None:
                continue
            default = getattr(cls(), name)
            if isinstance(default, bool):
                values[name] = raw.lower() == "true"
            elif isinstance(default, int):
                values[name] = int(raw)
            elif isinstance(default, Path):
                values[name] = Path(raw)
            elif isinstance(default, tuple):
                values[name] = tuple(x.strip() for x in raw.split(",") if x.strip())
            else:
                values[name] = raw
        result = cls(**values)
        if result.sample_seconds < 1 or result.ssh_workers < 1 or result.ssh_timeout < 1:
            raise ValueError("Invalid worker/SSH settings")
        return result
