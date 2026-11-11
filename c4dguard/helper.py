"""The helper that runs *inside* Cinema 4D when rendering visibly.

Cinema 4D is started with `g_additionalModulePath` pointing at a folder that
holds a tiny Python plugin (PLUGIN_SOURCE below). When Cinema 4D has started,
the plugin reads the task file named in $C4DGUARD_TASK, opens the project,
sets the frame range and save path, and runs "Render to Picture Viewer" -
the same thing a person would do by hand. It reports every step to a status
file that the guard watches.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import paths

PLUGIN_NAME = "c4dguard_helper.pyp"

PLUGIN_SOURCE = r'''
"""C4D Render Guard helper (written by C4D Render Guard; safe to delete)."""
import json
import os
import threading
import time
import traceback

import c4d

TASK_FILE = os.environ.get("C4DGUARD_TASK", "")
RENDER_TO_PICTURE_VIEWER = 12099
_started = False


def _load_task():
    with open(TASK_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _report(task, stage, message=""):
    try:
        with open(task["status"], "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"stage": stage, "message": message, "time": time.time()}) + "\n")
    except Exception:
        pass


def _same_file(a, b):
    norm = lambda p: os.path.normcase(os.path.normpath(p))
    return norm(a) == norm(b)


def _open_project(task):
    scene = task["scene"]
    doc = c4d.documents.GetActiveDocument()
    current = os.path.join(doc.GetDocumentPath() or "", doc.GetDocumentName() or "") if doc else ""
    if not (doc and _same_file(current, scene)):
        doc = None
        if task.get("quiet", True):
            # Load without SCENEFILTER_DIALOGSALLOWED: Cinema 4D then shows no
            # "missing fonts / plugins / newer version" pop-ups for this project.
            flags = c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS
            try:
                doc = c4d.documents.LoadDocument(scene, flags, None)
                if doc is not None:
                    c4d.documents.InsertBaseDocument(doc)
                    c4d.documents.SetActiveDocument(doc)
            except Exception:
                doc = None
        if doc is None:                                   # the ordinary "File > Open"
            if not c4d.documents.LoadFile(scene):
                raise RuntimeError("Cinema 4D could not open " + scene)
            doc = c4d.documents.GetActiveDocument()
    if doc is None:
        raise RuntimeError("no document is open after loading " + scene)
    return doc


def _apply_settings(task, doc):
    """Frame range + where to save, exactly like editing the Render Settings."""
    notes = []
    rd = doc.GetActiveRenderData()
    fps = doc.GetFps()
    rd[c4d.RDATA_FRAMESEQUENCE] = getattr(c4d, "RDATA_FRAMESEQUENCE_MANUAL", 0)
    rd[c4d.RDATA_FRAMEFROM] = c4d.BaseTime(task["start"], fps)
    rd[c4d.RDATA_FRAMETO] = c4d.BaseTime(task["end"], fps)
    rd[c4d.RDATA_FRAMESTEP] = task["step"]
    rd[c4d.RDATA_GLOBALSAVE] = True
    rd[c4d.RDATA_SAVEIMAGE] = True
    rd[c4d.RDATA_PATH] = task["output"]
    rd[c4d.RDATA_NAMEFORMAT] = getattr(c4d, "RDATA_NAMEFORMAT_0", 0)      # name0000.ext
    # A movie file cannot be resumed frame by frame: switch to an image sequence.
    movies = [getattr(c4d, n) for n in ("FILTER_MOVIE", "FILTER_AVI", "FILTER_QTVRSAVER_PANORAMA",
                                        "FILTER_QTVRSAVER_OBJECT") if hasattr(c4d, n)]
    if rd[c4d.RDATA_FORMAT] in movies:
        rd[c4d.RDATA_FORMAT] = getattr(c4d, "FILTER_PNG", 1023671)
        notes.append("the project saves a movie file; using a PNG sequence instead")
    if task.get("quiet", True) and hasattr(c4d, "RDATA_TEXTUREERROR"):
        # "Texture Error" stops the render with a message when a texture is missing.
        if rd[c4d.RDATA_TEXTUREERROR]:
            rd[c4d.RDATA_TEXTUREERROR] = False
            notes.append("missing textures will not stop the render")
    c4d.EventAdd()
    return notes


def _watch(task):
    """Tell the guard when the Picture Viewer render has finished."""
    flag = getattr(c4d, "CHECKISRUNNING_EXTERNALRENDERING", 1)
    seen_running = False
    waited = 0.0
    idle = 0
    while True:
        time.sleep(0.5)
        waited += 0.5
        try:
            running = bool(c4d.CheckIsRunning(flag))
        except Exception:
            running = False
        if running:
            seen_running, idle = True, 0
            continue
        idle += 1
        if (seen_running and idle >= 4) or (not seen_running and waited >= 30):
            _report(task, "done", "" if seen_running else "the render never appeared to start")
            return


def _start():
    global _started
    if _started or not TASK_FILE:
        return                      # Cinema 4D was not started by the guard: do nothing
    _started = True
    try:
        task = _load_task()
    except Exception:
        return
    try:
        _report(task, "started", "Cinema 4D " + str(c4d.GetC4DVersion()))
        doc = _open_project(task)
        _report(task, "opened", doc.GetDocumentName())
        notes = _apply_settings(task, doc)
        c4d.CallCommand(RENDER_TO_PICTURE_VIEWER)
        _report(task, "rendering", "; ".join(notes))
        threading.Thread(target=_watch, args=(task,), daemon=True).start()
    except Exception:
        _report(task, "error", traceback.format_exc())


def PluginMessage(id, data):
    if id == c4d.C4DPL_PROGRAM_STARTED:
        _start()
        return True
    return False
'''

STAGE_TEXT = {
    "": "Starting Cinema 4D…",
    "started": "Cinema 4D is open, loading the project…",
    "opened": "Project loaded, starting the render…",
    "rendering": "Rendering in Cinema 4D",
    "done": "Cinema 4D finished rendering",
    "error": "Cinema 4D reported an error",
}


def plugin_dir() -> Path:
    """Write the helper plugin (always fresh, so updates take effect) and return its folder."""
    d = paths.app_home() / "c4d_plugin"
    d.mkdir(parents=True, exist_ok=True)
    target = d / PLUGIN_NAME
    if not target.exists() or target.read_text(encoding="utf-8") != PLUGIN_SOURCE:
        target.write_text(PLUGIN_SOURCE, encoding="utf-8")
    return d


def write_task(job_id: str, scene: str, output: str, start: int, end: int, step: int,
               quiet: bool = True) -> tuple[Path, Path]:
    """Create the task + (empty) status file for one Cinema 4D launch."""
    d = paths.app_home() / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    task, status = d / f"{job_id}.task.json", d / f"{job_id}.status.jsonl"
    status.write_text("", encoding="utf-8")
    task.write_text(json.dumps({"scene": scene, "output": output, "start": start, "end": end,
                                "step": step, "quiet": quiet, "status": str(status)}),
                    encoding="utf-8")
    return task, status


def read_status(status: Path) -> list[dict]:
    out = []
    try:
        for line in status.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out
