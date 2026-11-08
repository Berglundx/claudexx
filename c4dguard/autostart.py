"""Start the runner automatically after the computer restarts.

* Windows: a .cmd in the user's Startup folder (runs at logon). It loops, so
  if the guard itself ever crashes it is started again.
* macOS:   a LaunchAgent with RunAtLoad + KeepAlive-on-failure.
* Linux:   a systemd --user service with Restart=on-failure.

Rendering resumes when the user logs in. For unattended render machines,
enable automatic login (Cinema 4D's license usually needs a user session).
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.c4drenderguard.runner"
PKG_PARENT = str(Path(__file__).resolve().parent.parent)


def _python() -> str:
    exe = sys.executable
    if sys.platform == "win32" and exe.lower().endswith("pythonw.exe"):
        exe = exe[:-len("pythonw.exe")] + "python.exe"
    return exe


def _win_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return (Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            / "Startup" / "C4D Render Guard.cmd")


def _mac_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _linux_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user" / "c4dguard.service"


def location() -> Path:
    if sys.platform == "win32":
        return _win_path()
    if sys.platform == "darwin":
        return _mac_path()
    return _linux_path()


def is_installed() -> bool:
    return location().exists()


def install(delay: int = 60) -> Path:
    py = _python()
    home = os.environ.get("C4DGUARD_HOME", "")
    path = location()
    path.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        lines = [
            "@echo off",
            "title C4D Render Guard",
            f'set "PYTHONPATH={PKG_PARENT};%PYTHONPATH%"',
        ]
        if home:
            lines.append(f'set "C4DGUARD_HOME={home}"')
        lines += [
            f'"{py}" -m c4dguard run --delay {delay}',
            ":loop",
            "if %errorlevel%==0 goto end",
            "echo Render Guard stopped unexpectedly, restarting in 30 seconds...",
            "timeout /t 30 /nobreak >nul",
            f'"{py}" -m c4dguard run',
            "goto loop",
            ":end",
        ]
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")

    elif sys.platform == "darwin":
        env = {"PYTHONPATH": PKG_PARENT}
        if home:
            env["C4DGUARD_HOME"] = home
        from . import paths
        log = str(paths.logs_dir() / "launchd.log")
        plist = {
            "Label": LABEL,
            "ProgramArguments": [py, "-m", "c4dguard", "run", "--delay", str(delay)],
            "EnvironmentVariables": env,
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 30,
            "StandardOutPath": log,
            "StandardErrorPath": log,
        }
        with open(path, "wb") as fh:
            plistlib.dump(plist, fh)
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        subprocess.run(["launchctl", "load", str(path)], capture_output=True)

    else:
        env = f'Environment="PYTHONPATH={PKG_PARENT}"\n'
        if home:
            env += f'Environment="C4DGUARD_HOME={home}"\n'
        path.write_text(
            "[Unit]\nDescription=C4D Render Guard\nAfter=network-online.target\n\n"
            "[Service]\nType=simple\n" + env +
            f"ExecStart={py} -m c4dguard run --delay {delay}\n"
            "Restart=on-failure\nRestartSec=30\n\n"
            "[Install]\nWantedBy=default.target\n",
            encoding="utf-8",
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        subprocess.run(["systemctl", "--user", "enable", "c4dguard.service"], capture_output=True)
    return path


def uninstall() -> Path:
    path = location()
    if sys.platform == "darwin" and path.exists():
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
    elif sys.platform.startswith("linux") and path.exists():
        subprocess.run(["systemctl", "--user", "disable", "c4dguard.service"], capture_output=True)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return path
