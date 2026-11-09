"""Turn a dropped .c4d file into a render job with no questions asked
(unless the frame range cannot be read from the project)."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from . import autostart, paths, project
from . import jobs as J
from . import settings as S

log = logging.getLogger("c4dguard")


def _same(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except OSError:
        return False


def existing_job(store: J.JobStore, scene: Path) -> J.Job | None:
    """Most recent job for this project, if any."""
    matches = [j for j in store.all() if _same(j.scene, scene)]
    return matches[-1] if matches else None


def new_pending_job(store: J.JobStore, scene: Path, prefs: S.Settings) -> J.Job:
    job = J.Job(id=J.new_job_id(), scene=str(scene.resolve()),
                output=project.output_prefix(scene, None), start=0, end=0,
                commandline=prefs.commandline, chunk_size=prefs.chunk_size,
                frame_timeout=prefs.frame_timeout, status=J.PREPARING)
    store.save(job)
    return job


def finalize(store: J.JobStore, job: J.Job, start: int, end: int, step: int = 1,
             output: str | None = None) -> J.Job:
    job.start, job.end, job.step = start, max(start, end), max(1, step)
    if output:
        job.output = output
    job.status = J.QUEUED
    store.save(job)
    ensure_autostart()
    return job


def ensure_autostart() -> None:
    if S.load().auto_resume and not autostart.is_installed():
        try:
            autostart.install()
            log.info("Enabled resume-after-reboot")
        except OSError as exc:
            log.warning("could not enable resume-after-reboot: %s", exc)


def read_and_finalize(store: J.JobStore, job: J.Job, cancel=None) -> bool:
    """Read the frame range with c4dpy. Returns False if it must be asked for.

    Never raises: any problem just means "ask the user".
    """
    scene = Path(job.scene)
    try:
        info = project.read_project(scene, S.commandline(), cancel=cancel)
    except Exception:  # noqa: BLE001 - a crash here must never leave the job stuck
        log.exception("reading %s failed", scene.name)
        return False
    if info is None:
        return False
    current = store.load(job.id)
    if current is None or current.status != J.PREPARING:
        return True       # the user removed it or typed the frames in meanwhile
    finalize(store, current, info.start, info.end, info.step, project.output_prefix(scene, info))
    log.info("[%s] %s: frames %d-%d -> %s", job.id, scene.name, current.start, current.end,
             current.output)
    return True


# ------------------------------------------------ hand-off to an open window
def inbox() -> Path:
    """Files dropped on the app icon while the window is already open land here."""
    d = paths.app_home() / "inbox"
    d.mkdir(exist_ok=True)
    return d


def send_to_open_window(files: list[str]) -> None:
    for f in files:
        (inbox() / f"{uuid.uuid4().hex}.txt").write_text(str(Path(f).resolve()), encoding="utf-8")
