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
from . import jobs as J
from . import paths

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
    cmd = [exe, "-nogui", "-render", job.scene,
           "-frame", str(chunk[0]), str(chunk[-1]), str(job.step),
           "-oimage", job.output]
    if job.take:
        cmd += ["-take", job.take]
    cmd += list(job.extra_args)
    return cmd


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, check=False)
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
            if len(chunk) >= max(1, job.chunk_size) or f != chunk[-1] + job.step:
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
            done_ids: set[str] = set()
            while not self.stop.is_set():
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
        exe = job.commandline or paths.find_commandline()
        if not exe or not Path(exe).exists():
            job.status, job.last_error = J.FAILED, f"Cinema 4D Commandline not found ({exe or 'auto-detect'})"
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

        while not self.stop.is_set():
            user = self._user_status(job)
            if user:
                log.info("[%s] %s by user", job.id, user)
                return
            remaining = self._remaining(job)
            if not remaining:
                job.status, job.finished, job.last_error = J.DONE, time.time(), ""
                job.active_chunk = None
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
            if not ok and not self.stop.is_set():
                delay = min(self.restart_delay * max(1, job.attempts.get(str(chunk[0]), 1)),
                            max(self.restart_delay, 120))
                log.info("[%s] restarting Cinema 4D in %.0fs", job.id, delay)
                self.stop.wait(delay)

    def _render_chunk(self, job: J.Job, exe: str, chunk: list[int]) -> bool:
        cmd = build_command(job, exe, chunk)
        started = time.time()
        job.status = J.RENDERING
        job.active_chunk = {"frames": chunk, "started": started}
        self.store.save(job)
        log.info("[%s] launching Cinema 4D for frames %d-%d", job.id, chunk[0], chunk[-1])

        logfile = open(paths.logs_dir() / f"{job.id}.c4d.log", "ab")
        logfile.write(f"\n===== {time.ctime()} :: {subprocess.list2cmdline(cmd)}\n".encode())
        logfile.flush()
        kwargs: dict = {"stdout": logfile, "stderr": subprocess.STDOUT,
                        "stdin": subprocess.DEVNULL, "cwd": str(Path(job.scene).parent)}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except OSError as exc:
            logfile.close()
            return self._chunk_failed(job, chunk, f"could not start Cinema 4D: {exc}")

        seen = {f for f in framelib.scan(job.output) if f in chunk}
        last_progress = started
        reason = ""
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
                    break
                if self.stop.is_set():
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
            job.active_chunk = None
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
        if not reason:
            reason = f"Cinema 4D exited before rendering frame {missing[0]}"
        self._discard_suspect_frame(job, job.active_chunk)
        return self._chunk_failed(job, chunk, reason)

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


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
