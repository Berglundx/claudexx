import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from c4dguard import frames as framelib  # noqa: E402
from c4dguard import jobs as J  # noqa: E402
from c4dguard.runner import Runner  # noqa: E402

FAKE = ROOT / "tests" / "fake_commandline.py"
FAKE_ENV = ("FAKE_CRASH", "FAKE_HANG", "FAKE_TRUNCATE", "FAKE_ALWAYS_CRASH", "FAKE_FRAME_SECONDS")


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["C4DGUARD_HOME"] = str(self.tmp / "home")
        os.environ["FAKE_STATE"] = str(self.tmp)
        for k in FAKE_ENV:
            os.environ.pop(k, None)
        FAKE.chmod(0o755)
        self.scene = self.tmp / "shot.c4d"
        self.scene.write_text("fake scene")
        self.store = J.JobStore()

    def make_job(self, **kw) -> J.Job:
        params = dict(id=J.new_job_id(), scene=str(self.scene), output=str(self.tmp / "out" / "shot"),
                      start=0, end=19, commandline=str(FAKE), chunk_size=6)
        params.update(kw)
        job = J.Job(**params)
        self.store.save(job)
        return job

    def run_guard(self):
        Runner(self.store, restart_delay=0.05, poll_seconds=0.05).run_all()

    def assert_complete(self, job):
        found = framelib.scan(job.output)
        self.assertEqual(sorted(found), job.frames())
        for p in found.values():
            self.assertTrue(framelib.is_complete(p), p)
        self.assertEqual(self.store.load(job.id).status, J.DONE)

    def test_clean_render(self):
        job = self.make_job()
        self.run_guard()
        self.assert_complete(job)
        self.assertEqual(self.store.load(job.id).restarts, 0)

    def test_crashes_are_restarted_and_resumed(self):
        os.environ["FAKE_CRASH"] = "3,11,12"
        job = self.make_job()
        self.run_guard()
        self.assert_complete(job)
        self.assertEqual(self.store.load(job.id).restarts, 3)

    def test_truncated_frame_is_rerendered(self):
        os.environ["FAKE_TRUNCATE"] = "7"
        job = self.make_job()
        self.run_guard()
        self.assert_complete(job)

    def test_hang_is_killed(self):
        os.environ["FAKE_HANG"] = "5"
        job = self.make_job(frame_timeout=2)
        t = time.time()
        self.run_guard()
        self.assert_complete(job)
        self.assertLess(time.time() - t, 30)

    def test_frame_that_always_crashes_fails_job(self):
        os.environ["FAKE_ALWAYS_CRASH"] = "4"
        job = self.make_job(max_attempts=3)
        self.run_guard()
        j = self.store.load(job.id)
        self.assertEqual(j.status, J.FAILED)
        self.assertEqual(j.attempts["4"], 3)
        self.assertEqual(sorted(framelib.scan(job.output)), [0, 1, 2, 3])

    def test_existing_frames_are_skipped(self):
        job = self.make_job()
        self.run_guard()
        first = {f: p.stat().st_mtime_ns for f, p in framelib.scan(job.output).items()}
        framelib.scan(job.output)[10].unlink()
        j = self.store.load(job.id)
        j.status = J.QUEUED
        self.store.save(j)
        self.run_guard()
        after = {f: p.stat().st_mtime_ns for f, p in framelib.scan(job.output).items()}
        self.assertEqual(sorted(after), job.frames())
        changed = [f for f in after if after[f] != first.get(f)]
        self.assertEqual(changed, [10])

    def test_resume_after_power_loss(self):
        """Kill the whole guard + C4D process group mid-render (like pulling the
        plug), leave a half-written frame behind, then start the guard again."""
        os.environ["FAKE_FRAME_SECONDS"] = "0.3"
        job = self.make_job(end=29, chunk_size=10)
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        cmd = [sys.executable, "-m", "c4dguard", "run", "--restart-delay", "0.1"]
        proc = subprocess.Popen(cmd, env=env, start_new_session=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 30
        while len(framelib.scan(job.output)) < 13 and time.time() < deadline:
            time.sleep(0.05)
        # "Power loss": kill guard and every C4D child instantly.
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        subprocess.run(["pkill", "-9", "-f", str(FAKE)], check=False)
        state = self.store.load(job.id)
        self.assertEqual(state.status, J.RENDERING)
        self.assertIsNotNone(state.active_chunk)
        done_before = framelib.scan(job.output)
        self.assertLess(len(done_before), 30)
        # Simulate the frame that was being written when power died.
        partial = Path(f"{job.output}{max(done_before) + 1:04d}.png")
        partial.write_bytes(b"\x89PNG\r\n\x1a\n trunc")
        kept = {f: p.stat().st_mtime_ns for f, p in done_before.items()}

        os.environ["FAKE_FRAME_SECONDS"] = "0.05"
        self.run_guard()
        self.assert_complete(job)
        after = framelib.scan(job.output)
        rerendered = [f for f in kept if after[f].stat().st_mtime_ns != kept[f]]
        self.assertEqual(rerendered, [], "finished frames must not be rendered again")
        self.assertGreaterEqual(self.store.load(job.id).restarts, 1)

    def test_scan_ignores_multipass_and_other_files(self):
        out = self.tmp / "o"
        out.mkdir()
        for name in ("shot0001.png", "shot_0002.png", "shot.0003.exr", "shot_diffuse0004.png",
                     "other0005.png", "shot0006.png.part"):
            (out / name).write_bytes(b"x")
        self.assertEqual(sorted(framelib.scan(str(out / "shot"))), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
