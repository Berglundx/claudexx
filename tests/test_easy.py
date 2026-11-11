"""Tests for drop-a-file usage: job creation, defaults, hand-off, CLI."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from c4dguard import jobs as J, project, quick  # noqa: E402
from c4dguard import frames as framelib  # noqa: E402
from c4dguard import settings as S  # noqa: E402
from c4dguard.runner import Runner  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
import fakes  # noqa: E402


class EasyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        os.environ["C4DGUARD_HOME"] = str(self.tmp / "home")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "cfg")   # autostart file goes here
        os.environ["FAKE_STATE"] = str(self.tmp)
        for k in ("FAKE_CRASH", "FAKE_HANG", "FAKE_TRUNCATE", "FAKE_ALWAYS_CRASH", "FAKE_MODE",
                  "FAKE_APP", "FAKE_MOVIE", "FAKE_FRAME_SECONDS"):
            os.environ.pop(k, None)
        inst = self.tmp / "Maxon Cinema 4D"
        inst.mkdir()
        self.exe = fakes.install("fake_commandline.py", inst / "Commandline")
        self.app = fakes.install("fake_c4d_app.py", inst / "Cinema 4D")
        os.environ["C4D_COMMANDLINE"] = str(self.exe)
        os.environ["C4D_APP"] = str(self.app)
        if sys.platform != "linux":
            # Don't touch the real registry / LaunchAgents of the test machine.
            S.save(S.Settings(auto_resume=False))
        self.proj = self.tmp / "My Project"
        self.proj.mkdir()
        self.scene = self.proj / "shot 010.c4d"
        self.scene.write_text("fake")
        self.store = J.JobStore()

    def test_default_output(self):
        self.assertEqual(project.default_output(self.scene),
                         str(self.proj / "renders" / "shot 010" / "shot 010"))

    def test_settings_remember_last_range_and_ignore_old_keys(self):
        home = Path(os.environ["C4DGUARD_HOME"])
        home.mkdir(parents=True, exist_ok=True)
        (home / "settings.json").write_text('{"read_from_project": true, "chunk_size": 4}')
        s = S.load()
        self.assertEqual((s.chunk_size, s.last_range), (4, [0, 90, 1]))
        s.last_range = [10, 20, 2]
        S.save(s)
        self.assertEqual(S.load().last_range, [10, 20, 2])

    def test_dropped_file_with_typed_range_renders(self):
        out = str(self.tmp / "elsewhere" / "shot 010")
        job = quick.create_job(self.store, self.scene, S.load(), 5, 16, 1, out)
        job.mode = "commandline"
        self.store.save(job)
        self.assertEqual(self.store.load(job.id).status, J.QUEUED)
        if sys.platform == "linux":
            self.assertTrue((self.tmp / "cfg" / "systemd" / "user" / "c4dguard.service").exists(),
                            "resume-after-reboot should be switched on automatically")
        Runner(self.store, restart_delay=0.05, poll_seconds=0.05).run_all()
        self.assertEqual(sorted(framelib.scan(out)), list(range(5, 17)))
        self.assertEqual(self.store.load(job.id).status, J.DONE)
        self.assertEqual(quick.existing_job(self.store, self.scene).id, job.id)

    # ---- opening Cinema 4D visibly and pressing render (the default) ----
    def run_guard(self, **tune):
        r = Runner(self.store, restart_delay=0.05, poll_seconds=0.05)
        r.done_grace, r.settle = 3.0, 2.0
        for k, v in tune.items():
            setattr(r, k, v)
        r.run_all()

    def app_job(self, **kw):
        job = quick.create_job(self.store, self.scene, S.load(), 0, 14, 1,
                               str(self.tmp / "out" / "shot 010"))
        for k, v in kw.items():
            setattr(job, k, v)
        self.store.save(job)
        return job

    def assert_app_closed(self):
        time.sleep(0.5)
        if sys.platform != "win32":
            res = subprocess.run(["pgrep", "-f", "fake_c4d_app.py"], capture_output=True)
            self.assertNotEqual(res.returncode, 0, "Cinema 4D should be closed after the render")

    def test_default_mode_opens_cinema4d_and_renders(self):
        job = self.app_job()
        self.assertEqual(job.mode, "app")
        self.run_guard()
        j = self.store.load(job.id)
        self.assertEqual(j.status, J.DONE, j.last_error)
        self.assertEqual(sorted(framelib.scan(job.output)), list(range(0, 15)))
        self.assertEqual(j.restarts, 0)
        log = (Path(os.environ["C4DGUARD_HOME"]) / "logs" / f"{job.id}.c4d.log").read_text()
        self.assertIn("g_additionalModulePath=", log)
        self.assertNotIn("-nogui", log)
        self.assert_app_closed()

    def test_app_mode_movie_format_becomes_image_sequence(self):
        os.environ["FAKE_MOVIE"] = "1"
        job = self.app_job()
        self.run_guard()
        self.assertEqual(self.store.load(job.id).status, J.DONE)

    def test_app_mode_crash_is_restarted_and_resumed(self):
        os.environ["FAKE_CRASH"] = "6"
        job = self.app_job()
        self.run_guard()
        j = self.store.load(job.id)
        self.assertEqual((j.status, j.restarts), (J.DONE, 1))
        self.assertEqual(sorted(framelib.scan(job.output)), list(range(0, 15)))
        self.assert_app_closed()

    def test_app_mode_hang_is_killed_and_resumed(self):
        os.environ["FAKE_HANG"] = "4"
        job = self.app_job(frame_timeout=2)
        self.run_guard()
        j = self.store.load(job.id)
        self.assertEqual((j.status, j.restarts), (J.DONE, 1))
        self.assert_app_closed()

    def test_app_mode_closed_by_user_is_reopened(self):
        os.environ["FAKE_APP"] = "exit"
        os.environ["FAKE_CRASH"] = "3"          # first launch dies; second runs to the end
        job = self.app_job()
        self.run_guard()
        self.assertEqual(self.store.load(job.id).status, J.DONE)

    def test_app_mode_helper_never_reports(self):
        os.environ["FAKE_APP"] = "noplugin"
        job = self.app_job()
        self.run_guard(helper_timeout=2.0)
        j = self.store.load(job.id)
        self.assertEqual((j.status, j.restarts), (J.FAILED, 0))
        self.assertIn("did not respond", j.last_error)
        self.assert_app_closed()

    def test_app_mode_project_cannot_be_opened(self):
        os.environ["FAKE_APP"] = "noload"
        job = self.app_job()
        self.run_guard()
        j = self.store.load(job.id)
        self.assertEqual(j.status, J.FAILED)
        self.assertIn("could not open", j.last_error)

    def test_app_mode_render_saves_nothing(self):
        os.environ["FAKE_APP"] = "nosave"
        job = self.app_job()
        self.run_guard()
        j = self.store.load(job.id)
        self.assertEqual((j.status, j.restarts), (J.FAILED, 0))
        self.assertIn("was not saved", j.last_error)

    def test_background_mode_uses_cinema4d_itself_headless(self):
        job = self.app_job(mode="background")
        self.run_guard()
        self.assertEqual(self.store.load(job.id).status, J.DONE)
        log = (Path(os.environ["C4DGUARD_HOME"]) / "logs" / f"{job.id}.c4d.log").read_text()
        self.assertIn("-nogui -render", log)
        self.assertIn("Cinema 4D", log.splitlines()[1])

    def test_cli_add_needs_frame_range(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        cmd = [sys.executable, "-m", "c4dguard", "add", str(self.scene)]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        res = subprocess.run(cmd + ["-s", "1", "-e", "3"], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        (job,) = self.store.all()
        self.assertEqual((job.start, job.end), (1, 3))

    def test_drop_on_icon_hands_files_to_open_window(self):
        quick.send_to_open_window([str(self.scene)])
        (item,) = quick.inbox().glob("*.txt")
        self.assertEqual(item.read_text(), str(self.scene.resolve()))


if __name__ == "__main__":
    unittest.main()
