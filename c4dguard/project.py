"""Read render settings (frame range, output path) from a .c4d project.

Uses c4dpy, the Python interpreter that ships next to Cinema 4D's
Commandline, to open the project and print its active render settings.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import paths

log = logging.getLogger("c4dguard")

MARKER = "C4DGUARD_INFO:"

C4DPY_SCRIPT = r'''
import json, sys
import c4d

path = sys.argv[-1]
flags = c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS
doc = c4d.documents.LoadDocument(path, flags, None)
if doc is None:
    print("C4DGUARD_ERROR: could not open " + path)
    sys.exit(2)
rd = doc.GetActiveRenderData()
fps = doc.GetFps()
seq = rd[c4d.RDATA_FRAMESEQUENCE]
if seq == getattr(c4d, "RDATA_FRAMESEQUENCE_CURRENTFRAME", 1):
    start = end = doc.GetTime().GetFrame(fps)
elif seq == getattr(c4d, "RDATA_FRAMESEQUENCE_ALLFRAMES", 2):
    start, end = doc.GetMinTime().GetFrame(fps), doc.GetMaxTime().GetFrame(fps)
elif seq == getattr(c4d, "RDATA_FRAMESEQUENCE_PREVIEWRANGE", 3):
    start, end = doc.GetLoopMinTime().GetFrame(fps), doc.GetLoopMaxTime().GetFrame(fps)
else:
    start, end = rd[c4d.RDATA_FRAMEFROM].GetFrame(fps), rd[c4d.RDATA_FRAMETO].GetFrame(fps)
info = {
    "start": int(start),
    "end": int(end),
    "step": max(1, int(rd[c4d.RDATA_FRAMESTEP] or 1)),
    "save_image": bool(rd[c4d.RDATA_SAVEIMAGE]),
    "path": rd[c4d.RDATA_PATH] or "",
    "fps": int(fps),
}
print("C4DGUARD_INFO:" + json.dumps(info))
sys.stdout.flush()
c4d.documents.KillDocument(doc)
'''


@dataclass
class ProjectInfo:
    start: int
    end: int
    step: int = 1
    path: str = ""          # save path from the render settings (may be relative / tokenized)
    save_image: bool = True


def find_c4dpy(commandline: str | None) -> str | None:
    """c4dpy lives in the same install as Commandline ($C4DPY overrides)."""
    env = os.environ.get("C4DPY")
    if env and Path(env).is_file():
        return env
    if not commandline:
        return None
    exe = Path(commandline)
    # macOS: .../Commandline.app/Contents/MacOS/Commandline -> install root is 4 levels up
    roots = [exe.parent]
    if ".app" in str(exe):
        roots.append(exe.parents[3])
    names = ["c4dpy.exe", "c4dpy", "c4dpy.app/Contents/MacOS/c4dpy"]
    for root in roots:
        for name in names:
            p = root / name
            if p.is_file():
                return str(p)
    return None


READ_TIMEOUT = 90   # seconds; after this the user is simply asked for the frame range


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=paths.no_window_flags())
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _parse(text: str) -> ProjectInfo | None:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(MARKER):
            try:
                d = json.loads(line[len(MARKER):])
                info = ProjectInfo(int(d["start"]), int(d["end"]), int(d.get("step", 1)),
                                   d.get("path", ""), bool(d.get("save_image", True)))
            except (ValueError, KeyError, TypeError):
                return None
            info.end = max(info.end, info.start)
            return info
    return None


def read_project(scene: Path, commandline: str | None, timeout: float = READ_TIMEOUT,
                 cancel: threading.Event | None = None) -> ProjectInfo | None:
    """Return the project's frame range, or None if it could not be read.

    c4dpy is run with its output going to a log file (not a pipe, which a
    stuck child process could keep open forever). As soon as the answer line
    appears, c4dpy is killed: it is known to linger after a script ends, and
    it may sit waiting on a license prompt. It is also killed on timeout or
    when `cancel` is set.
    """
    c4dpy = find_c4dpy(commandline)
    if not c4dpy:
        log.info("c4dpy not found next to %s; cannot read the frame range", commandline)
        return None
    out_path = paths.logs_dir() / "c4dpy-last.log"
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "c4dguard_info.py"
        script.write_text(C4DPY_SCRIPT, encoding="utf-8")
        kwargs: dict = {"stdin": subprocess.DEVNULL, "stderr": subprocess.STDOUT}
        if sys.platform == "win32":
            kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                       | paths.no_window_flags())
        else:
            kwargs["start_new_session"] = True
        log.info("reading render settings of %s with %s", scene.name, c4dpy)
        start = time.time()
        with open(out_path, "wb") as out:
            try:
                proc = subprocess.Popen([c4dpy, str(script), str(scene)], stdout=out, **kwargs)
            except OSError as exc:
                log.warning("could not start c4dpy: %s", exc)
                return None
            info, reason = None, ""
            try:
                while True:
                    text = out_path.read_bytes().decode("utf-8", "replace")
                    info = _parse(text)
                    if info:
                        break
                    if proc.poll() is not None:
                        time.sleep(0.3)          # let the last output land
                        info = _parse(out_path.read_bytes().decode("utf-8", "replace"))
                        reason = f"c4dpy exited with code {proc.returncode}"
                        break
                    if cancel is not None and cancel.is_set():
                        reason = "cancelled"
                        break
                    if time.time() - start > timeout:
                        reason = f"c4dpy gave no answer within {timeout:.0f}s"
                        break
                    time.sleep(0.25)
            finally:
                _kill_tree(proc)
    if info:
        log.info("%s: frames %d-%d (read in %.0fs)", scene.name, info.start, info.end,
                 time.time() - start)
        return info
    tail = out_path.read_bytes().decode("utf-8", "replace")[-800:].strip()
    log.warning("could not read render settings of %s: %s. c4dpy output: %s",
                scene.name, reason, tail or "(none)")
    return None


def output_prefix(scene: Path, info: ProjectInfo | None) -> str:
    """Where frames go: the project's own save path when it is a plain path,
    otherwise <project folder>/renders/<project>/<project>."""
    default = scene.parent / "renders" / scene.stem / scene.stem
    if not info or not info.path or not info.save_image or "$" in info.path:
        return str(default)
    p = Path(info.path)
    if not p.is_absolute():
        p = scene.parent / p
    if info.path.endswith(("/", "\\")) or p.is_dir():
        p = p / scene.stem
    # Drop an image extension if one was typed into the name.
    if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".psd", ".bmp",
                            ".tga", ".hdr", ".dpx", ".iff", ".b3d"}:
        p = p.with_suffix("")
    return str(p.resolve())
