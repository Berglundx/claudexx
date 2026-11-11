"""Render jobs and their crash-safe persistence on disk.

Every job is one JSON file. Files are written to a temp file, flushed to disk
and then atomically renamed, so a power cut can never leave a half-written job.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import paths

PREPARING = "preparing"   # used by v1.0.x while reading a project; upgraded on open
QUEUED = "queued"
RENDERING = "rendering"
PAUSED = "paused"
DONE = "done"
FAILED = "failed"

RUNNABLE = (QUEUED, RENDERING)


@dataclass
class Job:
    id: str
    scene: str
    # Output path *prefix*, e.g. D:/renders/shot010/shot010 -> shot0100000.png ...
    output: str
    start: int
    end: int
    step: int = 1
    commandline: str = ""          # explicit program to run; "" = auto-detect at render time
    mode: str = "app"              # app | background | commandline (paths.MODES)
    phase: str = ""                # what is happening right now, for the UI
    chunk_size: int = 10           # frames per Commandline launch
    take: str = ""
    extra_args: list[str] = field(default_factory=list)
    frame_timeout: int = 0         # seconds without a new frame = hung; 0 = automatic
    max_attempts: int = 5          # consecutive failures on one frame before giving up
    status: str = QUEUED
    created: float = field(default_factory=time.time)
    # Recovery bookkeeping
    attempts: dict[str, int] = field(default_factory=dict)   # frame -> failed tries
    active_chunk: dict | None = None   # {"frames": [...], "started": ts} while c4d runs
    restarts: int = 0
    last_error: str = ""
    max_frame_seconds: float = 0.0
    finished: float = 0.0

    def frames(self) -> list[int]:
        return list(range(self.start, self.end + 1, self.step))

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class JobStore:
    def __init__(self, directory: Path | None = None):
        self.dir = directory or paths.jobs_dir()
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        return self.dir / f"{job_id}.json"

    def save(self, job: Job) -> None:
        _atomic_write(self._path(job.id), json.dumps(asdict(job), indent=2))

    def load(self, job_id: str) -> Job | None:
        p = self._path(job_id)
        try:
            return Job.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None

    def all(self) -> list[Job]:
        jobs = []
        for p in self.dir.glob("*.json"):
            try:
                jobs.append(Job.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError):
                continue
        return sorted(jobs, key=lambda j: j.created)

    def find(self, prefix: str) -> Job | None:
        """Look a job up by its id or a unique prefix of it."""
        matches = [j for j in self.all() if j.id.startswith(prefix)]
        return matches[0] if len(matches) == 1 else None

    def delete(self, job_id: str) -> None:
        try:
            self._path(job_id).unlink()
        except FileNotFoundError:
            pass
