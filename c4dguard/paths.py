"""Locations of the app's state and of the Cinema 4D command line renderer."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path


def app_home() -> Path:
    """Directory that holds job files, logs and the runner lock.

    Override with the C4DGUARD_HOME environment variable.
    """
    override = os.environ.get("C4DGUARD_HOME")
    if override:
        home = Path(override)
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        home = Path(base) / "C4DRenderGuard"
    elif sys.platform == "darwin":
        home = Path.home() / "Library" / "Application Support" / "C4DRenderGuard"
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        home = Path(base) / "c4dguard"
    home.mkdir(parents=True, exist_ok=True)
    return home


def jobs_dir() -> Path:
    d = app_home() / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = app_home() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _candidates() -> list[str]:
    if sys.platform == "win32":
        roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramW6432", r"C:\Program Files")]
        patterns = []
        for root in dict.fromkeys(roots):
            patterns += [
                os.path.join(root, "Maxon Cinema 4D *", "Commandline.exe"),
                os.path.join(root, "Maxon", "Cinema 4D *", "Commandline.exe"),
                os.path.join(root, "MAXON", "CINEMA 4D R*", "Commandline.exe"),
                os.path.join(root, "MAXON", "Cinema 4D R*", "Commandline.exe"),
            ]
    elif sys.platform == "darwin":
        patterns = [
            "/Applications/Maxon Cinema 4D */Commandline.app/Contents/MacOS/Commandline",
            "/Applications/MAXON/CINEMA 4D R*/Commandline.app/Contents/MacOS/Commandline",
        ]
    else:
        patterns = [
            "/opt/maxon/cinema4d*/bin/Commandline",
            "/opt/maxon/*/bin/Commandline",
        ]
    found: list[str] = []
    for pattern in patterns:
        found += glob.glob(pattern)
    # Newest version last when sorted by name; prefer it.
    return sorted(set(found))


def find_commandline() -> str | None:
    """Return the path to Cinema 4D's Commandline renderer, or None.

    Order: $C4D_COMMANDLINE, then the newest standard install, then PATH.
    """
    env = os.environ.get("C4D_COMMANDLINE")
    if env and Path(env).exists():
        return env
    found = _candidates()
    if found:
        return found[-1]
    for name in ("Commandline", "Commandline.exe"):
        p = shutil.which(name)
        if p:
            return p
    return None


def is_frozen() -> bool:
    """True when running as the packaged .exe / .app."""
    return bool(getattr(sys, "frozen", False))


def self_command(windowless: bool = False) -> list[str]:
    """Command that starts this program again (append arguments to it)."""
    if is_frozen():
        return [sys.executable]
    exe = sys.executable
    if sys.platform == "win32":
        low = exe.lower()
        if windowless and low.endswith("python.exe"):
            cand = exe[:-len("python.exe")] + "pythonw.exe"
            exe = cand if Path(cand).exists() else exe
        elif not windowless and low.endswith("pythonw.exe"):
            exe = exe[:-len("pythonw.exe")] + "python.exe"
    launcher = Path(__file__).resolve().parent.parent / "launcher.py"
    return [exe, str(launcher)]


def no_window_flags() -> int:
    """Windows: keep console programs (Commandline, taskkill) from popping up windows."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
