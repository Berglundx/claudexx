#!/usr/bin/env python3
"""Stand-in for the Cinema 4D application, used by the tests.

* With -render it behaves like the headless renderer (see fake_commandline.py).
* Otherwise it behaves like the visible app: it loads the plugin found in
  g_additionalModulePath - the guard's *real* helper - against a fake `c4d`
  module, sends it C4DPL_PROGRAM_STARTED and then stays open until killed.

Extra env controls for the visible app:
  FAKE_APP   "noplugin": ignore plugins (helper never reports)
             "noload":   the project cannot be opened
             "nosave":   the render runs but saves nothing
             "exit":     quit by itself after the render (like a user closing it early)
"""
import os
import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_commandline as fc  # noqa: E402


def make_c4d(mode: str):
    c4d = types.ModuleType("c4d")
    c4d.C4DPL_PROGRAM_STARTED = 450000215
    names = ["RDATA_FRAMESEQUENCE", "RDATA_FRAMEFROM", "RDATA_FRAMETO", "RDATA_FRAMESTEP",
             "RDATA_GLOBALSAVE", "RDATA_SAVEIMAGE", "RDATA_PATH", "RDATA_NAMEFORMAT", "RDATA_FORMAT"]
    for i, n in enumerate(names):
        setattr(c4d, n, 5000 + i)
    c4d.RDATA_FRAMESEQUENCE_MANUAL, c4d.RDATA_NAMEFORMAT_0 = 0, 0
    c4d.FILTER_MOVIE, c4d.FILTER_AVI, c4d.FILTER_PNG, c4d.FILTER_TIF = 1125, 1122, 1023671, 1100
    c4d.CHECKISRUNNING_EXTERNALRENDERING = 1

    class BaseTime:
        def __init__(self, frame, fps):
            self.frame = frame

    class Doc:
        def __init__(self, path=""):
            self.path = path
            self.rd = {c4d.RDATA_FORMAT: c4d.FILTER_MOVIE if os.environ.get("FAKE_MOVIE") else c4d.FILTER_TIF,
                       c4d.RDATA_SAVEIMAGE: False, c4d.RDATA_NAMEFORMAT: 3}

        def GetDocumentPath(self): return os.path.dirname(self.path)
        def GetDocumentName(self): return os.path.basename(self.path) or "Untitled 1"
        def GetActiveRenderData(self): return self.rd
        def GetFps(self): return 25

    state = {"doc": Doc(), "thread": None}

    def LoadFile(path):
        if mode == "noload" or not os.path.isfile(path):
            return False
        state["doc"] = Doc(path)
        return True

    def render():
        rd = state["doc"].rd
        assert rd[c4d.RDATA_SAVEIMAGE] and rd[c4d.RDATA_GLOBALSAVE], "saving must be switched on"
        assert rd[c4d.RDATA_NAMEFORMAT] == 0 and rd[c4d.RDATA_FRAMESEQUENCE] == 0
        assert rd[c4d.RDATA_FORMAT] != c4d.FILTER_MOVIE, "movie formats cannot be resumed"
        secs = float(os.environ.get("FAKE_FRAME_SECONDS", "0.1"))
        always = os.environ.get("FAKE_ALWAYS_CRASH")
        for f in range(rd[c4d.RDATA_FRAMEFROM].frame, rd[c4d.RDATA_FRAMETO].frame + 1,
                       rd[c4d.RDATA_FRAMESTEP]):
            time.sleep(secs)
            if always and int(always) == f:
                os._exit(3)
            if fc.once("FAKE_CRASH", f):
                os._exit(139)
            if fc.once("FAKE_HANG", f):
                while True:
                    time.sleep(1)
            if mode == "nosave":
                continue
            out = Path(f"{rd[c4d.RDATA_PATH]}{f:04d}.png")
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".part")
            tmp.write_bytes(fc.png_bytes())
            os.replace(tmp, out)

    def CallCommand(cid, *a):
        assert cid == 12099, cid
        state["thread"] = threading.Thread(target=render, daemon=True)
        state["thread"].start()

    c4d.BaseTime = BaseTime
    c4d.documents = types.SimpleNamespace(GetActiveDocument=lambda: state["doc"], LoadFile=LoadFile)
    c4d.CallCommand = CallCommand
    c4d.EventAdd = lambda *a: None
    c4d.GetC4DVersion = lambda: 2025100
    c4d.CheckIsRunning = lambda flag: bool(state["thread"] and state["thread"].is_alive())
    return c4d, state


def run_app(args):
    mode = os.environ.get("FAKE_APP", "")
    print("Cinema 4D (fake) starting", flush=True)
    c4d, state = make_c4d(mode)
    sys.modules["c4d"] = c4d
    plugin_dirs = [a.split("=", 1)[1] for a in args if a.startswith("g_additionalModulePath=")]
    plugins = []
    if mode != "noplugin":
        for d in plugin_dirs:
            for pyp in Path(d).glob("*.pyp"):
                ns = {"__name__": pyp.stem, "__file__": str(pyp)}
                exec(compile(pyp.read_text(encoding="utf-8"), str(pyp), "exec"), ns)
                plugins.append(ns)
    for ns in plugins:
        ns["PluginMessage"](c4d.C4DPL_PROGRAM_STARTED, None)
    while True:                                   # a GUI app stays open
        time.sleep(0.2)
        t = state["thread"]
        if mode == "exit" and t is not None and not t.is_alive():
            return 0


if __name__ == "__main__":
    if "-render" in sys.argv:
        assert "-nogui" in sys.argv, "headless rendering needs -nogui"
        sys.exit(fc.main())
    sys.exit(run_app(sys.argv[1:]))
