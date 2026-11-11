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


# ----------------------------------------------------------- render programs
APP = "app"                    # open Cinema 4D visibly and render in the Picture Viewer
BACKGROUND = "background"      # Cinema 4D itself, without a window (-nogui -render)
COMMANDLINE = "commandline"    # the separate Commandline renderer
MODES = (APP, BACKGROUND, COMMANDLINE)


def install_dir_of(exe: str) -> Path:
    """Cinema 4D's install folder, given any program inside it."""
    p = Path(exe)
    if p.is_dir():
        return p
    for parent in p.parents:                 # macOS: step out of Something.app/Contents/MacOS
        if parent.suffix == ".app":
            return parent.parent
    return p.parent


def find_install() -> Path | None:
    exe = find_commandline()
    return install_dir_of(exe) if exe else None


def find_app(install: str | Path | None = None) -> str | None:
    """The Cinema 4D application itself ($C4D_APP overrides)."""
    env = os.environ.get("C4D_APP")
    if env and Path(env).exists():
        return env
    root = Path(install) if install else find_install()
    if not root:
        return None
    root = install_dir_of(str(root))
    for name in ("Cinema 4D.exe", "CINEMA 4D.exe",
                 "Cinema 4D.app/Contents/MacOS/Cinema 4D",
                 "CINEMA 4D.app/Contents/MacOS/CINEMA 4D",
                 "bin/Cinema 4D", "Cinema 4D"):
        p = root / name
        if p.is_file():
            return str(p)
    return None


def find_commandline_in(install: str | Path | None = None) -> str | None:
    env = os.environ.get("C4D_COMMANDLINE")
    if env and Path(env).exists():
        return env
    if not install:
        return find_commandline()
    root = install_dir_of(str(install))
    for name in ("Commandline.exe", "Commandline.app/Contents/MacOS/Commandline",
                 "bin/Commandline", "Commandline"):
        p = root / name
        if p.is_file():
            return str(p)
    return None


def program_for(mode: str, install: str | Path | None = None) -> str | None:
    """The executable that renders in the given mode."""
    return find_commandline_in(install) if mode == COMMANDLINE else find_app(install)


# ------------------------------------------------- starting other programs
def isolate_from_bundle() -> None:
    """Undo what the packaged app (PyInstaller) does to the process so that
    programs we start - Cinema 4D above all - load their *own* libraries.

    On Windows the bootloader calls SetDllDirectory(<our temp folder>), and
    child processes inherit that: Cinema 4D would then find our copies of
    common DLLs (VC runtime, OpenSSL, zlib, Tcl/Tk, ...) before its own.
    """
    if not is_frozen():
        return
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        except Exception:  # noqa: BLE001
            pass


def child_env(extra: dict[str, str] | None = None, same_app: bool = False) -> dict[str, str]:
    """Environment for a program we start.

    same_app=True is for starting another copy of this app: it must unpack
    itself independently, or it would break when the first copy exits.
    """
    env = dict(os.environ)
    if is_frozen():
        for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):      # restore the user's own values
            orig = env.pop(var + "_ORIG", None)
            if orig is not None:
                env[var] = orig
            elif var in env:
                del env[var]
        if same_app:
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        else:
            for var in ("TCL_LIBRARY", "TK_LIBRARY", "PYTHONHOME", "PYTHONPATH"):
                env.pop(var, None)
    env.update(extra or {})
    return env
