"""App-wide preferences, stored next to the jobs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields

from . import paths
from .jobs import _atomic_write


@dataclass
class Settings:
    commandline: str = ""        # "" = auto-detect
    chunk_size: int = 10
    frame_timeout: int = 0       # 0 = automatic
    auto_resume: bool = True     # resume after reboot (installs the startup entry)
    last_range: list = field(default_factory=lambda: [0, 90, 1])   # prefill for the next drop


def _path():
    return paths.app_home() / "settings.json"


def load() -> Settings:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    known = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in data.items() if k in known})


def save(s: Settings) -> None:
    _atomic_write(_path(), json.dumps(asdict(s), indent=2))


def commandline(s: Settings | None = None) -> str | None:
    s = s or load()
    return s.commandline or paths.find_commandline()
