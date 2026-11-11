"""The render supervisor.

For each job it repeatedly:
  1. scans the output folder for finished frames (deleting truncated ones),
  2. launches Cinema 4D's Commandline for the next chunk of missing frames,
  3. watches it: a crash (process exits before the chunk is done) or a hang
     (no new frame for too long) kills it and the loop simply starts again
     from the first missing frame.

All progress is derived from the frames on disk plus a small JSON job file,
so after a reboot the same loop picks up exactly where it left off.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import frames as framelib
from . import helper
from . import jobs as J
from . import paths
from . import popups
from . import settings as S

log = logging.getLogger("c4dguard")

POLL_SECONDS = 2.0
FIRST_FRAME_TIMEOUT = 2 * 3600      # scene load + first frame, when nothing is known yet
MIN_AUTO_TIMEOUT = 30 * 60
AUTO_TIMEOUT_FACTOR = 4


class RunnerLock:
    """OS-level exclusive lock; released automatically if the process dies."""

    def __init__(self, path: Path | None = None):
        self.path = path or (paths.app_home() / "runner.lock")
        self._fh = None

    def acquire(self) -> bool:
        self._fh = open(self.path, "a+")
        try:
            if sys.platform == "win32":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fh.close()
            self._fh = None
            return False
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(str(os.getpid()))
        self._fh.flush()
        return True

    def release(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


def is_runner_active() -> bool:
    lock = RunnerLock()
    if lock.acquire():
        lock.release()
        return False
    return True


def build_command(job: J.Job, exe: str, chunk: list[int]) -> list[str]:
    if job.mode == paths.APP:
        # Start Cinema 4D normally (with its window); our helper plugin does the rest.
        return [exe, f"g_additionalModulePath={helper.plugin_dir()}"] + list(job.extra_args)
    # Headless, the way render farms such as Deadline run it. Works with both
    # "Cinema 4D.exe" (uses the normal Cinema 4D license) and "Commandline.exe".
    cmd = [exe, "-nogui", "-render", job.scene,
           "-frame", str(chunk[0]), str(chunk[-1]), str(job.step),
           "-oimage", job.output]
    if job.take:
        cmd += ["-take", job.take]
    cmd += list(job.extra_args)
    return cmd


def stop_file() -> Path:
    """Created by the window's Quit to make the background renderer stop."""
    return paths.app_home() / "stop.request"


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, check=False,
                           creationflags=paths.no_window_flags())
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


class Runner:
    def __init__(self, store: J.JobStore | None = None, restart_delay: float = 15.0,
                 poll_seconds: float = POLL_SECONDS,
                 stop_event: threading.Event | None = None):
        self.store = store or J.JobStore()
        self.restart_delay = restart_delay
        self.poll = poll_seconds
        self.stop = stop_event or threading.Event()
        # Visible Cinema 4D: how long to wait for ...
        self.helper_timeout = 300.0   # ... the helper inside Cinema 4D to report in
        self.done_grace = 20.0        # ... the last frames after Cinema 4D says it is done
        self.settle = 15.0            # ... the last file to finish if Cinema 4D never says so
        self.popup_grace = 60.0       # ... the render to start before closing whatever is open

    def _stopping(self) -> bool:
        if not self.stop.is_set() and stop_file().exists():
            log.info("Stop requested from the window")
            self.stop.set()
        return self.stop.is_set()

    # ------------------------------------------------------------------ helpers
    def _remaining(self, job: J.Job) -> list[int]:
        on_disk = framelib.scan(job.output)
        for frame, path in list(on_disk.items()):
            if not framelib.is_complete(path):
                log.warning("[%s] frame %d is incomplete, deleting %s", job.id, frame, path)
                _unlink(path)
                del on_disk[frame]
        return [f for f in job.frames() if f not in on_disk]

    def _next_chunk(self, job: J.Job, remaining: list[int]) -> list[int]:
        chunk = [remaining[0]]
        for f in remaining[1:]:
            limit = len(job.frames()) if job.mode == paths.APP else max(1, job.chunk_size)
            if len(chunk) >= limit or f != chunk[-1] + job.step:
                break
            chunk.append(f)
        return chunk

    def _timeout(self, job: J.Job) -> float:
        if job.frame_timeout > 0:
            return job.frame_timeout
        if job.max_frame_seconds > 0:
            return max(MIN_AUTO_TIMEOUT, AUTO_TIMEOUT_FACTOR * job.max_frame_seconds)
        return FIRST_FRAME_TIMEOUT

    def _recover_interrupted(self, job: J.Job) -> None:
        """Called when a job file says C4D was running, but we are just starting.

        That means the machine or this app died mid-render. The frame that was
        being written at that moment may be corrupt; if we cannot verify it,
        throw away the newest frame written by that chunk.
        """
        chunk = job.active_chunk
        if not chunk:
            return
        log.warning("[%s] previous render of frames %s-%s was interrupted, recovering",
                    job.id, chunk["frames"][0], chunk["frames"][-1])
        self._discard_suspect_frame(job, chunk)
        job.active_chunk = None
        job.restarts += 1
        job.last_error = "interrupted (app or machine restart)"
        self.store.save(job)

    def _discard_suspect_frame(self, job: J.Job, chunk: dict) -> None:
        on_disk = framelib.scan(job.output)
        written = [(on_disk[f].stat().st_mtime, f) for f in chunk["frames"]
                   if f in on_disk and on_disk[f].stat().st_mtime >= chunk["started"] - 2]
        if not written:
            return
        _, newest = max(written)
        path = on_disk[newest]
        if framelib.verifiable(path):
            if not framelib.is_complete(path):
                log.warning("[%s] removing truncated frame %d", job.id, newest)
                _unlink(path)
        else:
            log.warning("[%s] removing possibly-partial frame %d (%s) to re-render it",
                        job.id, newest, path.name)
            _unlink(path)

    def _user_status(self, job: J.Job) -> str | None:
        """Status changes made by the user (pause/remove) while we render."""
        current = self.store.load(job.id)
        if current is None:
            return "removed"
        if current.status == J.PAUSED:
            return J.PAUSED
        return None

    # --------------------------------------------------------------------- main
    def run_all(self, only: list[str] | None = None) -> int:
        lock = RunnerLock()
        if not lock.acquire():
            log.info("Another C4D Render Guard runner is already active; nothing to do.")
            return 0
        try:
            # Jobs left "rendering" were cut off by a crash/reboot: recover them first.
            for job in self.store.all():
                if job.active_chunk:
                    self._recover_interrupted(job)
            try:
                stop_file().unlink()            # a stale request from an earlier session
            except OSError:
                pass
            done_ids: set[str] = set()
            while not self._stopping():
                pending = [j for j in self.store.all()
                           if j.status in J.RUNNABLE and j.id not in done_ids
                           and (only is None or j.id in only)]
                if not pending:
                    break
                job = pending[0]
                done_ids.add(job.id)
                self.run_job(job)
            return 0
        finally:
            lock.release()

    def run_job(self, job: J.Job) -> None:
        if job.mode not in paths.MODES:
            job.mode = paths.APP
        exe = job.commandline or S.program(job.mode)
        if not exe or not Path(exe).exists():
            what = "Commandline renderer" if job.mode == paths.COMMANDLINE else "program"
            job.status, job.last_error = J.FAILED, (
                f"Cinema 4D's {what} was not found ({exe or 'looked in the usual install folders'}). "
                "Set the Cinema 4D folder in Settings.")
            log.error("[%s] %s", job.id, job.last_error)
            self.store.save(job)
            return
        if not Path(job.scene).exists():
            job.status, job.last_error = J.FAILED, f"scene not found: {job.scene}"
            log.error("[%s] %s", job.id, job.last_error)
            self.store.save(job)
            return
        Path(job.output).parent.mkdir(parents=True, exist_ok=True)
        log.info("[%s] rendering %s frames %d-%d -> %s", job.id, Path(job.scene).name,
                 job.start, job.end, job.output)

        while not self._stopping():
            user = self._user_status(job)
            if user:
                log.info("[%s] %s by user", job.id, user)
                return
            remaining = self._remaining(job)
            if not remaining:
                job.status, job.finished, job.last_error = J.DONE, time.time(), ""
                job.active_chunk, job.phase = None, ""
                self.store.save(job)
                log.info("[%s] finished, all %d frames rendered", job.id, len(job.frames()))
                return
            chunk = self._next_chunk(job, remaining)
            tries = job.attempts.get(str(chunk[0]), 0)
            if tries >= job.max_attempts:
                job.status = J.FAILED
                job.last_error = (f"frame {chunk[0]} failed {tries} times in a row; "
                                  f"giving up ({job.last_error})")
                self.store.save(job)
                log.error("[%s] %s", job.id, job.last_error)
                return
            ok = self._render_chunk(job, exe, chunk)
            if job.status in (J.FAILED, J.PAUSED):
                return                      # given up (see last_error) or paused by the user
            if not ok and not self.stop.is_set():
                delay = min(self.restart_delay * max(1, job.attempts.get(str(chunk[0]), 1)),
                            max(self.restart_delay, 120))
                log.info("[%s] restarting Cinema 4D in %.0fs", job.id, delay)
                self.stop.wait(delay)

    def _render_chunk(self, job: J.Job, exe: str, chunk: list[int]) -> bool:
        visible = job.mode == paths.APP
        quiet = S.load().close_popups
        cmd = build_command(job, exe, chunk)
        started = time.time()
        job.status = J.RENDERING
        job.active_chunk = {"frames": chunk, "started": started}
        job.phase = helper.STAGE_TEXT[""]
        self.store.save(job)
        log.info("[%s] starting Cinema 4D (%s) for frames %d-%d: %s", job.id, job.mode,
                 chunk[0], chunk[-1], subprocess.list2cmdline(cmd))

        extra_env: dict[str, str] = {}
        status_path = None
        if visible:
            task, status_path = helper.write_task(job.id, job.scene, job.output,
                                                  chunk[0], chunk[-1], job.step, quiet=quiet)
            extra_env = {"C4DGUARD_TASK": str(task)}

        logfile = open(paths.logs_dir() / f"{job.id}.c4d.log", "ab")
        logfile.write(f"\n===== {time.ctime()} :: {subprocess.list2cmdline(cmd)}\n".encode())
        logfile.flush()
        log_path, log_offset = Path(logfile.name), logfile.tell()
        kwargs: dict = {"stdout": logfile, "stderr": subprocess.STDOUT,
                        "stdin": subprocess.DEVNULL, "cwd": str(Path(job.scene).parent),
                        "env": paths.child_env(extra_env)}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            if not visible:
                # Headless: no console window popping up next to the guard.
                kwargs["creationflags"] |= paths.no_window_flags()
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except OSError as exc:
            logfile.close()
            return self._chunk_failed(job, chunk, f"could not start Cinema 4D ({exe}): {exc}")

        watcher = popups.Watcher(proc.pid) if quiet else None
        seen = {f for f in framelib.scan(job.output) if f in chunk}
        last_progress = started
        reason, fatal, stage, helper_note = "", "", "", ""
        done_at = complete_at = None
        code = None
        try:
            while True:
                code = proc.poll()
                now = time.time()
                current = {f for f in framelib.scan(job.output) if f in chunk}
                new = current - seen
                if new:
                    elapsed = (now - last_progress) / len(new)
                    if seen or job.max_frame_seconds == 0:
                        job.max_frame_seconds = max(job.max_frame_seconds, elapsed)
                    seen |= current
                    last_progress = now
                    self.store.save(job)
                if code is not None:
                    if code != 0:
                        reason = f"Cinema 4D exited with code {code}"
                    elif visible:
                        reason = "Cinema 4D was closed before the render finished"
                    break
                if self._stopping():
                    reason = "stopped"
                    kill_tree(proc)
                    break
                user = self._user_status(job)
                if user:
                    reason = user
                    kill_tree(proc)
                    if user == J.PAUSED:
                        job.status = J.PAUSED
                    break

                if watcher:
                    # Not rendering yet after a while? Then whatever is open is in the way.
                    stuck = (visible and stage in ("", "started", "opened")
                             and now - started > self.popup_grace)
                    closed = watcher.check(stuck)
                    if closed:
                        job.popups = (job.popups + closed)[-20:]
                        self.store.save(job)

                if visible:
                    reports = helper.read_status(status_path)
                    stage = reports[-1]["stage"] if reports else ""
                    helper_note = "; ".join(r["message"] for r in reports
                                            if r["stage"] == "rendering" and r.get("message"))
                    phase = helper.STAGE_TEXT.get(stage, stage)
                    if phase != job.phase:
                        job.phase = phase
                        self.store.save(job)
                        log.info("[%s] %s", job.id, phase)
                    if stage == "error":
                        fatal = ("Cinema 4D could not start the render: "
                                 + last_lines(reports[-1].get("message", ""), 3))
                        kill_tree(proc)
                        break
                    if not stage and now - started > self.helper_timeout:
                        fatal = (f"Cinema 4D was started but did not respond within "
                                 f"{self.helper_timeout:.0f}s. If it is showing a dialog (login, "
                                 "license, missing files), close it and try again; otherwise "
                                 "switch “How to render” in Settings to render in the background.")
                        kill_tree(proc)
                        break
                    if stage == "done" and done_at is None:
                        done_at = now
                    complete = set(chunk) <= current
                    complete_at = (complete_at or now) if complete else None
                    if (done_at is not None and (complete or now - done_at > self.done_grace)) or \
                            (complete and now - complete_at > self.settle):
                        kill_tree(proc)              # the render is over: close Cinema 4D
                        break
                elif job.phase != "Rendering":
                    job.phase = "Rendering"
                    self.store.save(job)

                timeout = self._timeout(job)
                if now - last_progress > timeout:
                    reason = f"no new frame for {timeout:.0f}s, Cinema 4D appears hung"
                    log.warning("[%s] %s, killing it", job.id, reason)
                    kill_tree(proc)
                    break
                time.sleep(self.poll)
        finally:
            logfile.close()

        if reason in ("stopped", J.PAUSED, "removed"):
            # Keep active_chunk semantics: clean up a possibly partial frame now.
            self._discard_suspect_frame(job, job.active_chunk)
            job.active_chunk, job.phase = None, ""
            if reason == "stopped":
                job.status = J.QUEUED
            if reason != "removed":
                self.store.save(job)
            return True

        missing = [f for f in self._remaining(job) if f in chunk]
        if not missing:
            for f in chunk:
                job.attempts.pop(str(f), None)
            job.active_chunk = None
            self.store.save(job)
            return True
        output = c4d_output(log_path, log_offset)
        if visible and not reason and not fatal:
            # Cinema 4D said the render ended, yet frames are missing.
            fatal = (f"Cinema 4D finished rendering but frame {missing[0]} was not saved. "
                     "Check the Picture Viewer / console in Cinema 4D for a render error "
                     "(missing renderer or plugin, license of a third-party renderer)."
                     + (f" Note: {helper_note}." if helper_note else ""))
        if not reason:
            reason = f"Cinema 4D exited before rendering frame {missing[0]}"
        self._discard_suspect_frame(job, job.active_chunk)
        job.phase = ""
        if fatal:
            job.status, job.last_error, job.active_chunk = J.FAILED, fatal, None
            self.store.save(job)
            log.error("[%s] %s", job.id, fatal)
            return True

        # Some failures will never fix themselves by restarting: stop right away
        # and say what Cinema 4D reported instead of retrying 5 times.
        fatal = self._diagnose(job, chunk, started, code, output)
        if fatal:
            job.status, job.last_error, job.active_chunk = J.FAILED, fatal, None
            self.store.save(job)
            log.error("[%s] %s", job.id, fatal)
            return True
        if output:
            reason += f". Cinema 4D said: {last_lines(output, 3)}"
        return self._chunk_failed(job, chunk, reason)

    def _diagnose(self, job: J.Job, chunk: list[int], started: float, code,
                  output: str) -> str | None:
        """Return an error message if retrying is pointless, else None."""
        said = f" Cinema 4D said: {last_lines(output, 4)}" if output else ""
        bad = ("no ", "not ", "fail", "error", "invalid", "expired", "missing", "denied")
        license_lines = [ln for ln in output.lower().splitlines()
                         if "licens" in ln and any(w in ln for w in bad)]
        if license_lines:
            return ("Cinema 4D's Commandline has no valid license. Log in with the Maxon App "
                    "and make sure a Commandline/Team Render license is available." + said)
        rendered = [f for f in framelib.scan(job.output) if f in chunk]
        if rendered:
            return None                     # it was making progress: a real crash
        known = set(framelib.scan(job.output).values())
        stray = [p for p in new_images(Path(job.output).parent, started) if p not in known]
        if stray:
            return (f"Cinema 4D saved images, but not with the expected names "
                    f"(expected {Path(job.output).name}0000.ext, found {stray[0].name}). "
                    "Check the file name settings in Render Settings → Save." + said)
        if code == 0 and job.mode != paths.APP:
            return ("Cinema 4D finished without saving any images. In the project's Render "
                    "Settings, make sure Save → Regular Image is switched on (the renderer "
                    "only saves when it is), and that the project renders in Cinema 4D." + said)
        return None

    def _chunk_failed(self, job: J.Job, chunk: list[int], reason: str) -> bool:
        missing = [f for f in self._remaining(job) if f in chunk] or chunk
        key = str(missing[0])
        job.attempts[key] = job.attempts.get(key, 0) + 1
        job.restarts += 1
        job.last_error = reason
        job.active_chunk = None
        self.store.save(job)
        log.warning("[%s] render crashed at frame %s (attempt %d/%d): %s",
                    job.id, key, job.attempts[key], job.max_attempts, reason)
        return False


IMAGE_SUFFIXES = {"." + e for e in framelib.IMAGE_EXTS}


def c4d_output(path: Path, offset: int) -> str:
    """What Cinema 4D printed during one launch."""
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            return fh.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


def last_lines(text: str, n: int) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return " | ".join(lines[-n:])[:500]


def new_images(directory: Path, since: float) -> list[Path]:
    """Image files written in `directory` since `since` (any name)."""
    try:
        return sorted(p for p in directory.iterdir()
                      if p.suffix.lower() in IMAGE_SUFFIXES and p.stat().st_mtime >= since - 2)
    except OSError:
        return []


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
