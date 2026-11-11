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


def create_job(store: J.JobStore, scene: Path, prefs: S.Settings, start: int, end: int,
               step: int = 1, output: str | None = None) -> J.Job:
    """A ready-to-render job for a dropped project."""
    job = J.Job(id=J.new_job_id(), scene=str(scene.resolve()),
                output=output or project.default_output(scene),
                start=start, end=max(start, end), step=max(1, step),
                mode=prefs.render_mode, chunk_size=prefs.chunk_size,
                frame_timeout=prefs.frame_timeout, status=J.QUEUED)
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


# ------------------------------------------------ hand-off to an open window
def inbox() -> Path:
    """Files dropped on the app icon while the window is already open land here."""
    d = paths.app_home() / "inbox"
    d.mkdir(exist_ok=True)
    return d


def send_to_open_window(files: list[str]) -> None:
    for f in files:
        (inbox() / f"{uuid.uuid4().hex}.txt").write_text(str(Path(f).resolve()), encoding="utf-8")
