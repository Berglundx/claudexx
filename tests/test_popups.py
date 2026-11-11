"""The pop-up closer, tested against real windows (Windows only)."""
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from c4dguard import popups  # noqa: E402

MESSAGE_BOX = """
import ctypes, sys
# MB_YESNO: Yes = 6, No = 7
sys.exit(ctypes.windll.user32.MessageBoxW(0, 'Asset Error: texture missing. Continue?', 'Cinema 4D', 4))
"""

TK_APP = """
import os, sys, tkinter as tk
root = tk.Tk(); root.title('Cinema 4D'); root.geometry('700x500+50+50')
top = tk.Toplevel(root); top.title('Quick Start'); top.geometry('260x120+120+120')
top.transient(root)
bye = lambda *a: os._exit(7)
top.protocol('WM_DELETE_WINDOW', bye); top.bind('<Return>', bye)
if sys.argv[1] == 'modal':
    root.attributes('-disabled', True)       # what a modal dialog does to its main window
top.focus_force()
root.mainloop()
"""


@unittest.skipUnless(sys.platform == "win32", "window handling is Windows-only")
class PopupTest(unittest.TestCase):
    def start(self, code, *args):
        proc = subprocess.Popen([sys.executable, "-c", code, *args])
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    def watch(self, proc, seconds, stuck=False):
        watcher = popups.Watcher(proc.pid, interval=0.5)
        handled, end = [], time.time() + seconds
        while time.time() < end and proc.poll() is None:
            handled += watcher.check(stuck)
            time.sleep(0.2)
        return handled

    def test_system_message_box_is_answered_with_yes(self):
        proc = self.start(MESSAGE_BOX)
        handled = self.watch(proc, 30)
        self.assertEqual(proc.poll(), 6, f"expected 'Yes' to be clicked; handled={handled}")
        self.assertIn("Asset Error", handled[0])
        self.assertIn("Cinema 4D", handled[0])

    def test_dialog_blocking_the_main_window_is_closed(self):
        proc = self.start(TK_APP, "modal")
        handled = self.watch(proc, 30)
        self.assertEqual(proc.poll(), 7, f"dialog should have been dismissed; handled={handled}")
        self.assertIn("Quick Start", handled[0])

    def test_harmless_windows_are_left_alone_unless_startup_is_stuck(self):
        proc = self.start(TK_APP, "free")
        self.assertEqual(self.watch(proc, 6), [])
        self.assertIsNone(proc.poll(), "a normal extra window must not be touched")
        handled = self.watch(proc, 30, stuck=True)
        self.assertEqual(proc.poll(), 7, f"handled={handled}")


class EverywhereTest(unittest.TestCase):
    def test_watcher_is_harmless_on_other_systems(self):
        if sys.platform != "win32":
            self.assertEqual(popups.Watcher(1, interval=0).check(stuck=True), [])


if __name__ == "__main__":
    unittest.main()
