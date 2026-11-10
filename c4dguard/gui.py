"""Drag-and-drop front end.

Drop .c4d files on the window (or on the app icon) and they start rendering.
Rendering runs in a separate background process (`c4dguard run`), so closing
this window never stops a render.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, autostart, paths, project, quick
from .quick import inbox, send_to_open_window
from . import frames as framelib
from . import jobs as J
from . import settings as S
from .cli import progress, setup_logging, spawn_background_runner
from .runner import RunnerLock, is_runner_active

TITLE = "C4D Render Guard"


# ------------------------------------------------------------ drag & drop lib
def _load_dnd():
    """tkinterdnd2 provides real drag & drop for Tk. Install it on first use."""
    try:
        import tkinterdnd2
        return tkinterdnd2
    except ImportError:
        if paths.is_frozen():          # the packaged app bundles it; nothing to install
            return None
    marker = paths.app_home() / "dnd-install-attempted"
    if marker.exists() and time.time() - marker.stat().st_mtime < 86400:
        return None
    marker.write_text("x")
    py = sys.executable
    if py.lower().endswith("pythonw.exe"):
        py = py[:-len("pythonw.exe")] + "python.exe"
    kwargs: dict = {"capture_output": True, "timeout": 180}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        subprocess.run([py, "-m", "pip", "install", "--user", "--quiet", "tkinterdnd2"], **kwargs)
        import site
        import importlib
        site.addsitedir(site.getusersitepackages())
        importlib.invalidate_caches()
        import tkinterdnd2
        return tkinterdnd2
    except Exception:
        return None


# ------------------------------------------------------------------ helpers
def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def status_text(job: J.Job, done: int, total: int, runner: bool) -> str:
    if job.status == J.DONE:
        return "Done ✓"
    if job.status == J.PAUSED:
        return "Paused"
    if job.status == J.FAILED:
        return "Failed – double-click for details"
    if job.status == J.PREPARING:
        return "Waiting for frame range…"
    if job.status == J.RENDERING and runner:
        extra = f"  (recovered from {job.restarts} crash{'es' if job.restarts != 1 else ''})" \
            if job.restarts else ""
        return "Rendering" + extra
    return "Waiting" if runner else "Queued (starts automatically)"


class RangeDialog(tk.Toplevel):
    """Asks which frames to render and where to save them."""

    def __init__(self, master, scene: Path, values: tuple[int, int, int], output: str):
        super().__init__(master)
        self.title(TITLE)
        self.resizable(False, False)
        self.result = None
        self.scene = scene
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both")
        ttk.Label(f, text=f"Render “{scene.name}”", font=("Helvetica", 13, "bold")).grid(
            row=0, column=0, columnspan=7, sticky="w", pady=(0, 10))
        self.v = [tk.StringVar(value=str(x)) for x in values]
        entries = []
        for i, label in enumerate(("From frame", "To frame", "Step")):
            ttk.Label(f, text=label).grid(row=1, column=i * 2, sticky="w", padx=(0 if i == 0 else 12, 4))
            e = ttk.Entry(f, textvariable=self.v[i], width=8)
            e.grid(row=1, column=i * 2 + 1, sticky="w")
            entries.append(e)
        ttk.Label(f, text="Save frames to").grid(row=2, column=0, sticky="w", pady=(12, 0))
        self.v_dir = tk.StringVar(value=str(Path(output).parent))
        self.name = Path(output).name
        ttk.Entry(f, textvariable=self.v_dir, width=48).grid(row=2, column=1, columnspan=5,
                                                             sticky="ew", pady=(12, 0))
        ttk.Button(f, text="Change…", command=self._pick_dir).grid(row=2, column=6, padx=(6, 0),
                                                                   pady=(12, 0))
        ttk.Label(f, foreground="#667", text=f"Files are named {self.name}0000, {self.name}0001, … "
                  "in the format set in the project's Render Settings.").grid(
            row=3, column=1, columnspan=6, sticky="w")
        b = ttk.Frame(f)
        b.grid(row=4, column=0, columnspan=7, sticky="e", pady=(14, 0))
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(b, text="Render", command=self._ok).pack(side="right", padx=6)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.transient(master)
        self.grab_set()
        entries[0].focus_set()
        entries[0].select_range(0, "end")
        self.wait_window()

    def _pick_dir(self):
        d = filedialog.askdirectory(parent=self, initialdir=self.v_dir.get(),
                                    title="Where should the frames be saved?")
        if d:
            self.v_dir.set(d)

    def _ok(self):
        try:
            a, b, c = (int(v.get().strip()) for v in self.v)
        except ValueError:
            messagebox.showerror(TITLE, "Please enter whole frame numbers.", parent=self)
            return
        if b < a or c < 1:
            messagebox.showerror(TITLE, "“To frame” must be at least “From frame”.", parent=self)
            return
        folder = self.v_dir.get().strip()
        if not folder:
            messagebox.showerror(TITLE, "Choose where to save the frames.", parent=self)
            return
        self.result = (a, b, c, str(Path(folder).expanduser() / self.name))
        self.destroy()


class DetailsDialog(tk.Toplevel):
    """Why a job failed, plus Cinema 4D's own output - easy to copy and send."""

    def __init__(self, master, job: J.Job):
        super().__init__(master)
        self.title(f"{TITLE} – {Path(job.scene).name}")
        self.geometry("760x460")
        log_path = paths.logs_dir() / f"{job.id}.c4d.log"
        try:
            c4d = log_path.read_text(encoding="utf-8", errors="replace")[-6000:]
        except OSError:
            c4d = "(Cinema 4D has not printed anything yet)"
        text = (f"Project:  {job.scene}\nFrames:   {job.start}-{job.end} (step {job.step})\n"
                f"Saved to: {Path(job.output).parent}\nStatus:   {job.status}\n"
                f"Problem:  {job.last_error or '-'}\n\n"
                f"----- Cinema 4D output (last part of {log_path.name}) -----\n{c4d}")
        b = ttk.Frame(self, padding=10)
        b.pack(side="bottom", fill="x")          # buttons first so they always stay visible
        box = tk.Text(self, wrap="word", font=("Courier", 10))
        box.insert("1.0", text)
        box.see("end")
        box.config(state="disabled")
        box.pack(fill="both", expand=True, padx=10, pady=(10, 0))

        def copy():
            self.clipboard_clear()
            self.clipboard_append(text)
        ttk.Button(b, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(b, text="Copy to clipboard", command=copy).pack(side="right", padx=6)
        self.transient(master)


class SettingsDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title(TITLE + " – Settings")
        self.resizable(False, False)
        s = S.load()
        self.v_exe = tk.StringVar(value=s.commandline or (paths.find_commandline() or ""))
        self.v_chunk = tk.StringVar(value=str(s.chunk_size))
        self.v_timeout = tk.StringVar(value=str(s.frame_timeout))
        self.v_auto = tk.BooleanVar(value=s.auto_resume)
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both")
        ttk.Label(f, text="Cinema 4D Commandline").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_exe, width=55).grid(row=0, column=1, padx=6)
        ttk.Button(f, text="Browse…", command=self._browse).grid(row=0, column=2)
        ttk.Label(f, text="Frames per Cinema 4D launch").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(f, textvariable=self.v_chunk, width=8).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(f, text="Hang timeout, seconds (0 = automatic)").grid(row=2, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_timeout, width=8).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Checkbutton(f, text="Resume rendering automatically after the computer restarts",
                        variable=self.v_auto).grid(row=3, column=0, columnspan=3, sticky="w", pady=8)
        b = ttk.Frame(f)
        b.grid(row=4, column=0, columnspan=3, sticky="e")
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(b, text="Save", command=self._save).pack(side="right", padx=6)
        self.transient(master)
        self.grab_set()

    def _browse(self):
        p = filedialog.askopenfilename(parent=self, title="Locate Cinema 4D's Commandline")
        if p:
            self.v_exe.set(p)

    def _save(self):
        try:
            chunk, timeout = int(self.v_chunk.get()), int(self.v_timeout.get())
        except ValueError:
            messagebox.showerror(TITLE, "Please enter whole numbers.", parent=self)
            return
        exe = self.v_exe.get().strip()
        s = S.load()
        s.commandline = "" if exe == (paths.find_commandline() or "") else exe
        s.chunk_size, s.frame_timeout = max(1, chunk), max(0, timeout)
        s.auto_resume = self.v_auto.get()
        S.save(s)
        try:
            autostart.install() if s.auto_resume else autostart.uninstall()
        except OSError as exc:
            messagebox.showerror(TITLE, f"Could not change resume-after-reboot: {exc}", parent=self)
        self.destroy()


# --------------------------------------------------------------- the window
def make_app(dnd):
    base = dnd.TkinterDnD.Tk if dnd else tk.Tk

    class App(base):
        def __init__(self, files: list[str]):
            super().__init__()
            self.title(f"{TITLE} {__version__}")
            self.geometry("860x520")
            self.minsize(640, 400)
            self.store = J.JobStore()
            self._done_cache: dict[str, tuple[int, int]] = {}
            self._build()
            if sys.platform == "darwin":
                # Files dropped on the app's Dock/Finder icon arrive as Apple Events.
                self.createcommand("::tk::mac::OpenDocument",
                                   lambda *files: self.submit_files(list(files)))
            for job in self.store.all():   # left over from an older version: ask again
                if job.status == J.PREPARING:
                    self.store.delete(job.id)
                    files = list(files) + [job.scene]
            if any(j.status in J.RUNNABLE for j in self.store.all()):
                self._start_runner()                  # e.g. after an update
            self.after(200, lambda: self.submit_files(files))
            self.refresh()

        # ------------------------------------------------------------ layout
        def _build(self):
            self.drop = tk.Label(
                self, text=("⬇\n\nDrop Cinema 4D projects here to render them\n"
                            "or click to choose files"),
                font=("Helvetica", 15), bg="#eef2f7", fg="#334", relief="ridge", bd=2,
                cursor="hand2", height=7)
            self.drop.pack(fill="x", padx=12, pady=12)
            self.drop.bind("<Button-1>", lambda e: self._choose())
            if dnd:
                for w in (self, self.drop):
                    w.drop_target_register(dnd.DND_FILES)
                    w.dnd_bind("<<Drop>>", self._on_drop)
                self.drop.dnd_bind("<<DropEnter>>", lambda e: self._hover(True))
                self.drop.dnd_bind("<<DropLeave>>", lambda e: self._hover(False))
            else:
                self.drop.config(text="Click here to choose Cinema 4D projects to render\n\n"
                                      "(tip: you can also drop .c4d files on the app icon)")

            cols = ("project", "status", "frames", "output")
            self.tree = ttk.Treeview(self, columns=cols, show="headings", height=8)
            for c, title, w in zip(cols, ("Project", "Status", "Progress", "Saved to"),
                                   (170, 320, 120, 230)):
                self.tree.heading(c, text=title)
                self.tree.column(c, width=w, anchor="w")
            self.tree.pack(fill="both", expand=True, padx=12)
            self.tree.bind("<Double-1>", lambda e: self._double_click())
            if dnd:
                self.tree.drop_target_register(dnd.DND_FILES)
                self.tree.dnd_bind("<<Drop>>", self._on_drop)

            b = ttk.Frame(self, padding=12)
            b.pack(fill="x")
            ttk.Button(b, text="Open output folder", command=self._open_output).pack(side="left")
            ttk.Button(b, text="Pause", command=lambda: self._set(J.PAUSED)).pack(side="left", padx=4)
            ttk.Button(b, text="Resume", command=lambda: self._set(J.QUEUED)).pack(side="left")
            ttk.Button(b, text="Frames…", command=self._edit_frames).pack(side="left", padx=4)
            ttk.Button(b, text="Remove", command=self._remove).pack(side="left")
            ttk.Button(b, text="Details…", command=self._details).pack(side="left", padx=4)
            ttk.Button(b, text="Settings", command=lambda: SettingsDialog(self)).pack(side="right")
            ttk.Button(b, text="Logs", command=lambda: open_folder(paths.logs_dir())).pack(side="right", padx=4)
            self.v_info = tk.StringVar()
            ttk.Label(self, textvariable=self.v_info, padding=(12, 0, 12, 10), foreground="#556").pack(anchor="w")

        def _hover(self, on: bool):
            self.drop.config(bg="#d6e8ff" if on else "#eef2f7")

        # -------------------------------------------------------- submitting
        def _on_drop(self, event):
            self._hover(False)
            self.submit_files(list(self.tk.splitlist(event.data)))
            return event.action

        def _choose(self):
            files = filedialog.askopenfilenames(
                title="Choose Cinema 4D projects",
                filetypes=[("Cinema 4D projects", "*.c4d"), ("All files", "*")])
            self.submit_files(list(files))

        def _commandline(self) -> str | None:
            exe = S.commandline()
            if exe and Path(exe).exists():
                return exe
            messagebox.showinfo(TITLE, "Cinema 4D wasn't found automatically.\n\n"
                                "Please locate its “Commandline” program (it is in the "
                                "Cinema 4D installation folder).")
            p = filedialog.askopenfilename(title="Locate Cinema 4D's Commandline")
            if not p:
                return None
            s = S.load()
            s.commandline = p
            S.save(s)
            return p

        def submit_files(self, files: list[str]):
            scenes = []
            for f in files:
                p = Path(f)
                if p.suffix.lower() == ".c4d" and p.is_file():
                    scenes.append(p)
                elif f:
                    messagebox.showwarning(TITLE, f"“{p.name}” is not a Cinema 4D project (.c4d).")
            if not scenes or not self._commandline():
                return
            for scene in scenes:
                self._submit(scene)
            self.refresh(reschedule=False)

        def _submit(self, scene: Path):
            old = quick.existing_job(self.store, scene)
            if old and old.status in (J.QUEUED, J.RENDERING):
                messagebox.showinfo(TITLE, f"“{scene.name}” is already rendering or queued.")
                return
            if old and old.status in (J.PAUSED, J.FAILED):
                if messagebox.askyesno(TITLE, f"“{scene.name}” is already in the list "
                                       f"({old.status}, frames {old.start}-{old.end}).\n\n"
                                       "Continue that render?\n(Choose No to set it up again.)"):
                    old.status, old.attempts, old.last_error = J.QUEUED, {}, ""
                    self.store.save(old)
                    self._start_runner()
                    return
            prefs = S.load()
            output = old.output if old else project.default_output(scene)
            dlg = RangeDialog(self, scene, tuple(prefs.last_range), output)
            if not dlg.result:
                return
            start, end, step, output = dlg.result
            if old and old.status == J.DONE and not messagebox.askyesno(
                    TITLE, f"“{scene.name}” was already rendered.\n\n"
                    "Render it again? Existing frames in that range will be replaced."):
                return
            if old:
                if old.status == J.DONE:
                    # Re-render: remove the old frames in this range so they get redone.
                    wanted = set(range(start, end + 1, step))
                    for f, path in framelib.scan(output).items():
                        if f in wanted:
                            path.unlink(missing_ok=True)
                # Paused/failed set up again: frames already on disk are kept and skipped.
                self.store.delete(old.id)
            prefs = S.load()
            prefs.last_range = [start, end, step]
            S.save(prefs)
            quick.create_job(self.store, scene, prefs, start, end, step, output)
            self._start_runner()

        def _start_runner(self):
            if not is_runner_active():
                spawn_background_runner()

        # ----------------------------------------------------------- actions
        def _selected_jobs(self) -> list[J.Job]:
            return [j for j in (self.store.load(i) for i in self.tree.selection()) if j]

        def _set(self, status):
            for job in self._selected_jobs():
                if status == J.QUEUED and job.status == J.DONE:
                    continue
                job.status = status
                if status == J.QUEUED:
                    job.attempts = {}
                self.store.save(job)
            if status == J.QUEUED:
                self._start_runner()
            self.refresh(reschedule=False)

        def _edit_frames(self):
            jobs = self._selected_jobs()
            if len(jobs) != 1:
                messagebox.showinfo(TITLE, "Select one job first.")
                return
            job = jobs[0]
            if job.status in (J.RENDERING, J.QUEUED):
                messagebox.showinfo(TITLE, "Pause the job first, then change its frames.")
                return
            dlg = RangeDialog(self, Path(job.scene), (job.start, job.end, job.step), job.output)
            if dlg.result:
                job = self.store.load(job.id) or job
                job.start, job.end, job.step, job.output = dlg.result
                job.last_error = ""
                if job.status in (J.DONE, J.FAILED):
                    job.status = J.QUEUED
                job.attempts = {}
                self.store.save(job)
                self._done_cache.pop(job.id, None)
                self._start_runner()
                self.refresh(reschedule=False)

        def _remove(self):
            jobs = self._selected_jobs()
            if jobs and messagebox.askyesno(TITLE, "Remove the selected job(s) from the list?\n"
                                            "Frames that were already rendered are kept."):
                for job in jobs:
                    self.store.delete(job.id)
            self.refresh(reschedule=False)

        def _details(self):
            jobs = self._selected_jobs()
            if not jobs:
                messagebox.showinfo(TITLE, "Select a job first.")
                return
            DetailsDialog(self, jobs[0])

        def _double_click(self):
            jobs = self._selected_jobs()
            if jobs and jobs[0].status == J.FAILED:
                DetailsDialog(self, jobs[0])
            else:
                self._open_output()

        def _open_output(self):
            jobs = self._selected_jobs()
            if jobs:
                open_folder(Path(jobs[0].output).parent)

        # ----------------------------------------------------------- refresh
        def _progress(self, job: J.Job) -> tuple[int, int]:
            if job.status == J.DONE and job.id in self._done_cache:
                return self._done_cache[job.id]
            p = progress(job)
            if job.status == J.DONE:
                self._done_cache[job.id] = p
            return p

        def refresh(self, reschedule=True):
            for f in sorted(inbox().glob("*.txt")):     # files dropped on the app icon
                try:
                    path = f.read_text(encoding="utf-8").strip()
                    f.unlink()
                except OSError:
                    continue
                self.submit_files([path])
                self.deiconify()
                self.lift()
            runner = is_runner_active()
            selected = set(self.tree.selection())
            self.tree.delete(*self.tree.get_children())
            for job in self.store.all():
                done, total = self._progress(job)
                frames = f"{done} / {total}  ({100 * done // total}%)" if total else ""
                self.tree.insert("", "end", iid=job.id, values=(
                    Path(job.scene).name, status_text(job, done, total, runner), frames,
                    Path(job.output).parent))
            self.tree.selection_set([i for i in selected if self.tree.exists(i)])
            resume = "on" if autostart.is_installed() else "off"
            self.v_info.set(("Rendering in the background. You can close this window."
                             if runner else "Idle.") +
                            f"    Resume after restart: {resume}.    Double-click a job to open its frames.")
            if reschedule:
                self.after(2000, self.refresh)

    return App


def main(files: list[str] | None = None) -> int:
    files = files or []
    setup_logging()
    lock = RunnerLock(paths.app_home() / "gui.lock")
    if not lock.acquire():
        # A window is already open: hand the files over to it.
        if files:
            send_to_open_window(files)
        return 0
    try:
        app = make_app(_load_dnd())(files)
        app.mainloop()
    finally:
        lock.release()
    return 0
