"""Tests for drop-a-file usage: reading the project and creating the job."""
import json
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
        for k in ("FAKE_CRASH", "FAKE_HANG", "FAKE_TRUNCATE", "FAKE_ALWAYS_CRASH", "FAKE_C4DPY_INFO"):
            os.environ.pop(k, None)
        # A fake "Cinema 4D install": Commandline + c4dpy side by side.
        inst = self.tmp / "Maxon Cinema 4D"
        inst.mkdir()
        self.exe = fakes.install("fake_commandline.py", inst / "Commandline")
        c4dpy = fakes.install("fake_c4dpy.py", inst / "c4dpy")
        if sys.platform == "win32":
            os.environ["C4DPY"] = str(c4dpy)    # a .cmd isn't found by name next to it
        os.environ["C4D_COMMANDLINE"] = str(self.exe)
        if sys.platform != "linux":
            # Don't touch the real registry / LaunchAgents of the test machine.
            S.save(S.Settings(auto_resume=False))
        self.proj = self.tmp / "My Project"
        self.proj.mkdir()
        self.scene = self.proj / "shot 010.c4d"
        self.scene.write_text("fake")
        self.store = J.JobStore()

    def set_info(self, **kw):
        info = {"start": 5, "end": 16, "step": 1, "save_image": True, "path": "", "fps": 25}
        info.update(kw)
        os.environ["FAKE_C4DPY_INFO"] = json.dumps(info)

    def test_c4dpy_script_is_valid_python(self):
        compile(project.C4DPY_SCRIPT, "c4dguard_info.py", "exec")

    def test_reads_frame_range_from_project(self):
        self.set_info(start=5, end=16, step=2)
        info = project.read_project(self.scene, str(self.exe))
        self.assertEqual((info.start, info.end, info.step), (5, 16, 2))

    def test_unreadable_project_returns_none(self):
        self.assertIsNone(project.read_project(self.scene, str(self.exe)))

    def test_output_paths(self):
        default = str(self.proj / "renders" / "shot 010" / "shot 010")
        self.assertEqual(project.output_prefix(self.scene, None), default)
        mk = lambda path, save=True: project.ProjectInfo(0, 1, 1, path, save)  # noqa: E731
        self.assertEqual(project.output_prefix(self.scene, mk("$prj/$take/x")), default)
        self.assertEqual(project.output_prefix(self.scene, mk("/r/out", save=False)), default)
        self.assertEqual(project.output_prefix(self.scene, mk("frames/beauty.png")),
                         str(self.proj / "frames" / "beauty"))
        final = str(self.tmp / "final") + os.sep
        self.assertEqual(project.output_prefix(self.scene, mk(final)),
                         str((self.tmp / "final" / "shot 010").resolve()))

    def test_dropped_file_renders_with_project_settings(self):
        self.set_info(start=5, end=16, path="out/img")
        job = quick.new_pending_job(self.store, self.scene, quick.S.load())
        self.assertEqual(self.store.load(job.id).status, J.PREPARING)
        self.assertTrue(quick.read_and_finalize(self.store, job))
        job = self.store.load(job.id)
        self.assertEqual((job.start, job.end, job.status), (5, 16, J.QUEUED))
        self.assertEqual(job.output, str(self.proj / "out" / "img"))
        if sys.platform == "linux":
            self.assertTrue((self.tmp / "cfg" / "systemd" / "user" / "c4dguard.service").exists(),
                            "resume-after-reboot should be switched on automatically")
        Runner(self.store, restart_delay=0.05, poll_seconds=0.05).run_all()
        self.assertEqual(sorted(framelib.scan(job.output)), list(range(5, 17)))
        self.assertEqual(self.store.load(job.id).status, J.DONE)
        self.assertEqual(quick.existing_job(self.store, self.scene).id, job.id)

    def test_cli_add_without_frame_range(self):
        self.set_info(start=1, end=3)
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        res = subprocess.run([sys.executable, "-m", "c4dguard", "add", str(self.scene)],
                             env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        (job,) = self.store.all()
        self.assertEqual((job.start, job.end), (1, 3))

    def test_drop_on_icon_hands_files_to_open_window(self):
        quick.send_to_open_window([str(self.scene)])
        (item,) = quick.inbox().glob("*.txt")
        self.assertEqual(item.read_text(), str(self.scene.resolve()))


if __name__ == "__main__":
    unittest.main()
