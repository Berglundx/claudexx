"""Tests for drop-a-file usage: job creation, defaults, hand-off, CLI."""
import os
import subprocess
import sys
import tempfile
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
        for k in ("FAKE_CRASH", "FAKE_HANG", "FAKE_TRUNCATE", "FAKE_ALWAYS_CRASH", "FAKE_MODE"):
            os.environ.pop(k, None)
        inst = self.tmp / "Maxon Cinema 4D"
        inst.mkdir()
        self.exe = fakes.install("fake_commandline.py", inst / "Commandline")
        os.environ["C4D_COMMANDLINE"] = str(self.exe)
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
        self.assertEqual(self.store.load(job.id).status, J.QUEUED)
        if sys.platform == "linux":
            self.assertTrue((self.tmp / "cfg" / "systemd" / "user" / "c4dguard.service").exists(),
                            "resume-after-reboot should be switched on automatically")
        Runner(self.store, restart_delay=0.05, poll_seconds=0.05).run_all()
        self.assertEqual(sorted(framelib.scan(out)), list(range(5, 17)))
        self.assertEqual(self.store.load(job.id).status, J.DONE)
        self.assertEqual(quick.existing_job(self.store, self.scene).id, job.id)

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
