"""Install the fake Cinema 4D programs in a way each OS can execute."""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def install(src_name: str, dest: Path) -> Path:
    """Copy tests/<src_name> to `dest` as a runnable program; return its path."""
    src = HERE / src_name
    if sys.platform == "win32":
        script = dest.with_suffix(".py")
        shutil.copy(src, script)
        cmd = dest.with_suffix(".cmd")
        cmd.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        return cmd
    shutil.copy(src, dest)
    dest.chmod(0o755)
    return dest


def kill_tree(pid: int) -> None:
    """Kill a process and all its children instantly (simulated power cut)."""
    if sys.platform == "win32":
        import subprocess
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        import signal
        os.killpg(pid, signal.SIGKILL)


if __name__ == "__main__":
    # python tests/fakes.py <dir>  -> fake C4D install + a scene, for smoke-testing builds
    base = Path(sys.argv[1]).resolve()
    (base / "C4D").mkdir(parents=True, exist_ok=True)
    (base / "proj").mkdir(exist_ok=True)
    exe = install("fake_commandline.py", base / "C4D" / "Commandline")
    for name in ("cli", "gui"):
        (base / "proj" / f"{name}.c4d").write_text("fake")
    print(f"COMMANDLINE={exe}")
