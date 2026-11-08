"""Read render settings (frame range, output path) from a .c4d project.

Uses c4dpy, the Python interpreter that ships next to Cinema 4D's
Commandline, to open the project and print its active render settings.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

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
    """c4dpy lives in the same install as Commandline."""
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


def read_project(scene: Path, commandline: str | None, timeout: int = 300) -> ProjectInfo | None:
    """Return the project's frame range, or None if it could not be read."""
    c4dpy = find_c4dpy(commandline)
    if not c4dpy:
        log.info("c4dpy not found next to %s; cannot read the frame range", commandline)
        return None
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "c4dguard_info.py"
        script.write_text(C4DPY_SCRIPT, encoding="utf-8")
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            res = subprocess.run([c4dpy, str(script), str(scene)], capture_output=True,
                                 text=True, errors="replace", timeout=timeout,
                                 stdin=subprocess.DEVNULL, **kwargs)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("reading %s with c4dpy failed: %s", scene.name, exc)
            return None
    for line in (res.stdout or "").splitlines():
        if line.startswith(MARKER):
            try:
                d = json.loads(line[len(MARKER):])
                info = ProjectInfo(int(d["start"]), int(d["end"]), int(d.get("step", 1)),
                                   d.get("path", ""), bool(d.get("save_image", True)))
            except (ValueError, KeyError, TypeError):
                break
            if info.end < info.start:
                info.end = info.start
            return info
    log.warning("could not read render settings of %s (c4dpy exit %s): %s", scene.name,
                res.returncode, (res.stdout + res.stderr)[-500:])
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
