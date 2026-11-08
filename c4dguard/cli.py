"""Command line interface: python -m c4dguard <command> ..."""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import autostart, paths
from . import frames as framelib
from . import jobs as J
from .runner import Runner, is_runner_active


def setup_logging(verbose: bool = False) -> None:
    root = logging.getLogger("c4dguard")
    if root.handlers:
        return
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(paths.logs_dir() / "guard.log", maxBytes=5_000_000, backupCount=3,
                             encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)


def resolve_output(scene: Path, output: str | None) -> str:
    """Turn the --output argument into an output *prefix* (folder + file name)."""
    stem = scene.stem
    if output and Path(output).is_absolute() and not output.endswith(("/", "\\")) \
            and not Path(output).is_dir():
        return output
    if not output:
        return str(scene.parent / "renders" / stem / stem)
    out = Path(output).expanduser()
    if output.endswith(("/", "\\")) or out.is_dir():
        return str(out.resolve() / stem)
    return str(out.resolve())


def progress(job: J.Job) -> tuple[int, int]:
    wanted = set(job.frames())
    done = sum(1 for f in framelib.scan(job.output) if f in wanted)
    return done, len(wanted)


def spawn_background_runner() -> None:
    """Start `c4dguard run` as an independent process (survives closing the caller)."""
    cmd = paths.self_command(windowless=True) + ["run"]
    kwargs: dict = {"stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                   | subprocess.DETACHED_PROCESS)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


# ----------------------------------------------------------------- commands
def cmd_add(args, store: J.JobStore) -> int:
    from . import project, quick
    from . import settings as S
    scene = Path(args.scene).expanduser().resolve()
    if not scene.exists():
        print(f"Scene not found: {scene}", file=sys.stderr)
        return 1
    exe = args.commandline or ""
    if not exe and not S.commandline():
        print("Warning: Cinema 4D Commandline not found; pass --commandline or set "
              "C4D_COMMANDLINE before rendering.", file=sys.stderr)
    start, end, step, output = args.start, args.end, args.step, args.output
    if start is None or end is None:
        print(f"Reading render settings from {scene.name} ...")
        info = project.read_project(scene, exe or S.commandline())
        if info is None:
            print("Could not read the frame range from the project; pass --start and --end.",
                  file=sys.stderr)
            return 1
        start = info.start if start is None else start
        end = info.end if end is None else end
        step = step or info.step
        if not output:
            output = project.output_prefix(scene, info)
    step = step or 1
    if end < start or step < 1:
        print("Invalid frame range", file=sys.stderr)
        return 1
    job = J.Job(
        id=J.new_job_id(), scene=str(scene), output=resolve_output(scene, output),
        start=start, end=end, step=step, commandline=exe,
        chunk_size=args.chunk, take=args.take or "",
        extra_args=shlex.split(args.extra or "", posix=sys.platform != "win32"),
        frame_timeout=args.timeout, max_attempts=args.max_attempts,
    )
    store.save(job)
    quick.ensure_autostart()
    print(f"Added job {job.id}: {scene.name} frames {job.start}-{job.end} -> {job.output}")
    return 0


def cmd_run(args, store: J.JobStore) -> int:
    setup_logging(args.verbose)
    log = logging.getLogger("c4dguard")
    if args.delay:
        log.info("Waiting %ds for the system to settle before rendering...", args.delay)
        time.sleep(args.delay)
    if getattr(args, "keep_alive", False):
        return keep_alive(args)
    only = None
    if args.jobs:
        only = []
        for prefix in args.jobs:
            job = store.find(prefix)
            if not job:
                print(f"No unique job matches '{prefix}'", file=sys.stderr)
                return 1
            only.append(job.id)
    stop = threading.Event()

    def _stop(*_):
        log.info("Stopping (rendering will resume next time the guard runs)...")
        stop.set()

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    return Runner(store, restart_delay=args.restart_delay, stop_event=stop).run_all(only)


def keep_alive(args) -> int:
    """Run the renderer as a child process and restart it if it ever crashes."""
    log = logging.getLogger("c4dguard")
    cmd = paths.self_command(windowless=True) + ["run", "--restart-delay", str(args.restart_delay)]
    cmd += list(args.jobs or [])
    while True:
        rc = subprocess.call(cmd, stdin=subprocess.DEVNULL, creationflags=paths.no_window_flags())
        if rc == 0:
            return 0
        log.error("Renderer stopped unexpectedly (exit code %s); restarting in 30s", rc)
        time.sleep(30)


def cmd_render(args, store: J.JobStore) -> int:
    before = {j.id for j in store.all()}
    rc = cmd_add(args, store)
    if rc:
        return rc
    new = [j.id for j in store.all() if j.id not in before]
    args.jobs, args.delay = new, 0
    if is_runner_active():
        print("A runner is already active; the job was queued and will render after the current one.")
        return 0
    return cmd_run(args, store)


def cmd_status(args, store: J.JobStore) -> int:
    jobs = store.all()
    print(f"Runner: {'ACTIVE' if is_runner_active() else 'not running'}    "
          f"Resume after reboot: {'on' if autostart.is_installed() else 'off'}")
    if not jobs:
        print("No jobs.")
        return 0
    print(f"{'ID':<22} {'STATUS':<10} {'DONE':>11} {'RESTARTS':>8}  SCENE")
    for job in jobs:
        done, total = progress(job)
        print(f"{job.id:<22} {job.status:<10} {f'{done}/{total}':>11} {job.restarts:>8}  "
              f"{Path(job.scene).name}")
        if job.last_error and job.status != J.DONE:
            print(f"{'':<22} last problem: {job.last_error}")
    return 0


def _with_job(args, store) -> J.Job | None:
    job = store.find(args.job)
    if not job:
        print(f"No unique job matches '{args.job}'", file=sys.stderr)
    return job


def cmd_set_status(status: str):
    def handler(args, store: J.JobStore) -> int:
        job = _with_job(args, store)
        if not job:
            return 1
        job.status = status
        if status == J.QUEUED:
            job.attempts = {}
        store.save(job)
        print(f"Job {job.id} is now {status}.")
        return 0
    return handler


def cmd_remove(args, store: J.JobStore) -> int:
    job = _with_job(args, store)
    if not job:
        return 1
    store.delete(job.id)
    print(f"Removed job {job.id} (rendered frames were kept).")
    return 0


def cmd_autostart(args, store: J.JobStore) -> int:
    if args.action == "install":
        p = autostart.install(delay=args.delay)
        print(f"Installed: {p}\nUnfinished renders will resume automatically after a restart/logon.")
    elif args.action == "uninstall":
        print(f"Removed: {autostart.uninstall()}")
    else:
        print(f"{'Installed' if autostart.is_installed() else 'Not installed'}: {autostart.location()}")
    return 0


def cmd_gui(args, store: J.JobStore) -> int:
    try:
        from .gui import main as gui_main
    except ImportError:
        if getattr(args, "files", None):
            # No Tkinter: still render the dropped files from the console.
            rc = 0
            for f in args.files:
                ns = build_parser().parse_args(["add", f])
                rc |= cmd_add(ns, store)
            spawn_background_runner()
            return rc
        print("The GUI needs Tkinter (included with the python.org installers). "
              "Use the command line instead, see: python -m c4dguard --help", file=sys.stderr)
        return 1
    return gui_main(getattr(args, "files", None))


def _job_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("scene", help="the .c4d project file")
    p.add_argument("-s", "--start", type=int, help="first frame (default: from the project)")
    p.add_argument("-e", "--end", type=int, help="last frame (default: from the project)")
    p.add_argument("--step", type=int, default=0, help="frame step (default: from the project)")
    p.add_argument("-o", "--output", help="output folder or path prefix "
                   "(default: <scene folder>/renders/<scene>/<scene>)")
    p.add_argument("--commandline", help="path to Cinema 4D Commandline (default: auto-detect)")
    p.add_argument("--chunk", type=int, default=10,
                   help="frames rendered per Cinema 4D launch (default 10)")
    p.add_argument("--take", help="take to render")
    p.add_argument("--timeout", type=int, default=0,
                   help="seconds without a new frame before C4D is considered hung (0 = auto)")
    p.add_argument("--max-attempts", type=int, default=5,
                   help="give up after a frame crashes this many times in a row (default 5)")
    p.add_argument("--extra", help='extra Commandline arguments, e.g. "-threads 16"')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="c4dguard",
        description="Render Cinema 4D projects and automatically recover from crashes and restarts.")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("add", help="queue a render job")
    _job_options(p)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("render", help="queue a job and render it now")
    _job_options(p)
    p.add_argument("--restart-delay", type=float, default=15.0)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("run", help="render all queued/unfinished jobs")
    p.add_argument("jobs", nargs="*", help="only these job ids (default: all)")
    p.add_argument("--delay", type=int, default=0, help="wait N seconds before starting")
    p.add_argument("--restart-delay", type=float, default=15.0,
                   help="seconds to wait before relaunching a crashed Cinema 4D")
    p.add_argument("--keep-alive", action="store_true",
                   help="restart the renderer itself if it ever crashes (used at logon)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", aliases=["list"], help="show jobs and progress")
    p.set_defaults(func=cmd_status)

    for name, status, text in (("pause", J.PAUSED, "pause a job"),
                               ("resume", J.QUEUED, "resume a paused job"),
                               ("retry", J.QUEUED, "retry a failed job")):
        p = sub.add_parser(name, help=text)
        p.add_argument("job")
        p.set_defaults(func=cmd_set_status(status))

    p = sub.add_parser("remove", help="delete a job (keeps rendered frames)")
    p.add_argument("job")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("autostart", help="resume rendering automatically after a reboot")
    p.add_argument("action", choices=["install", "uninstall", "status"])
    p.add_argument("--delay", type=int, default=60,
                   help="seconds to wait after logon before rendering (default 60)")
    p.set_defaults(func=cmd_autostart)

    p = sub.add_parser("gui", help="open the window (optionally rendering the given .c4d files)")
    p.add_argument("files", nargs="*")
    p.set_defaults(func=cmd_gui)
    return parser


def _attach_parent_console() -> None:
    """The Windows .exe has no console of its own (so double-clicking it shows
    no black window). When it is run from a terminal, print into that one."""
    if sys.platform != "win32" or not paths.is_frozen() or sys.stdout is not None:
        return
    import ctypes
    if ctypes.windll.kernel32.AttachConsole(-1):  # -1 = parent process's console
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stderr = sys.stdout


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # `c4dguard shot.c4d` (e.g. a file dropped on the app icon) opens the window with it.
    if argv and argv[0].lower().endswith(".c4d"):
        argv = ["gui"] + argv
    if argv and argv[0] not in ("gui",) and not argv[0].lower().endswith(".c4d"):
        _attach_parent_console()
    args = parser.parse_args(argv)
    if not args.command:
        return cmd_gui(args, J.JobStore())
    return args.func(args, J.JobStore())
