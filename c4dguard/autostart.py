"""Start the renderer automatically after the computer restarts.

* Windows: a "Run" entry in the user's registry (no admin rights, no console
  window), starting `run --keep-alive` so the guard restarts itself if it
  ever crashes.
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

from . import paths

LABEL = "com.c4drenderguard.runner"
WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WIN_VALUE = "C4D Render Guard"


def _quote(args: list[str]) -> str:
    return subprocess.list2cmdline(args)


def _win_old_cmd() -> Path:
    """Startup-folder script used by earlier versions."""
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return (Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            / "Startup" / "C4D Render Guard.cmd")


def _mac_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _linux_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user" / "c4dguard.service"


def location() -> str:
    if sys.platform == "win32":
        return "HKEY_CURRENT_USER\\" + WIN_RUN_KEY + "\\" + WIN_VALUE
    return str(_mac_path() if sys.platform == "darwin" else _linux_path())


def is_installed() -> bool:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY) as key:
                winreg.QueryValueEx(key, WIN_VALUE)
            return True
        except OSError:
            return False
    return Path(location()).exists()


def _home_env() -> dict[str, str]:
    home = os.environ.get("C4DGUARD_HOME")
    return {"C4DGUARD_HOME": home} if home else {}


def install(delay: int = 60) -> str:
    if sys.platform == "win32":
        import winreg
        args = paths.self_command(windowless=True) + ["run", "--keep-alive", "--delay", str(delay)]
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY) as key:
            winreg.SetValueEx(key, WIN_VALUE, 0, winreg.REG_SZ, _quote(args))
        try:
            _win_old_cmd().unlink()
        except OSError:
            pass
        return location()

    args = paths.self_command(windowless=True) + ["run", "--delay", str(delay)]
    path = Path(location())
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        log = str(paths.logs_dir() / "launchd.log")
        plist = {
            "Label": LABEL,
            "ProgramArguments": args,
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 30,
            "ProcessType": "Interactive",
            "StandardOutPath": log,
            "StandardErrorPath": log,
        }
        if _home_env():
            plist["EnvironmentVariables"] = _home_env()
        with open(path, "wb") as fh:
            plistlib.dump(plist, fh)
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        subprocess.run(["launchctl", "load", str(path)], capture_output=True)
    else:
        env = "".join(f'Environment="{k}={v}"\n' for k, v in _home_env().items())
        path.write_text(
            "[Unit]\nDescription=C4D Render Guard\nAfter=network-online.target\n\n"
            "[Service]\nType=simple\n" + env +
            f"ExecStart={_quote(args)}\n"
            "Restart=on-failure\nRestartSec=30\n\n"
            "[Install]\nWantedBy=default.target\n",
            encoding="utf-8",
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        subprocess.run(["systemctl", "--user", "enable", "c4dguard.service"], capture_output=True)
    return str(path)


def uninstall() -> str:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, WIN_VALUE)
        except OSError:
            pass
        try:
            _win_old_cmd().unlink()
        except OSError:
            pass
        return location()
    path = Path(location())
    if sys.platform == "darwin" and path.exists():
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
    elif path.exists():
        subprocess.run(["systemctl", "--user", "disable", "c4dguard.service"], capture_output=True)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return str(path)
