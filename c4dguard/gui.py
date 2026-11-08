"""Minimal Tkinter front end. Rendering itself runs in a separate background
process (`c4dguard run`), so closing this window never stops a render."""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import autostart, paths
from . import jobs as J
from .cli import progress, resolve_output, spawn_background_runner
from .runner import is_runner_active


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("C4D Render Guard")
        self.geometry("900x560")
        self.store = J.JobStore()
        self._build_form()
        self._build_list()
        self.refresh()

    # ---------------------------------------------------------------- layout
    def _build_form(self):
        f = ttk.LabelFrame(self, text="New render job", padding=8)
        f.pack(fill="x", padx=10, pady=8)
        self.v_scene = tk.StringVar()
        self.v_output = tk.StringVar()
        self.v_exe = tk.StringVar(value=paths.find_commandline() or "")
        self.v_start, self.v_end, self.v_step = tk.StringVar(value="0"), tk.StringVar(value="100"), tk.StringVar(value="1")
        self.v_chunk, self.v_timeout = tk.StringVar(value="10"), tk.StringVar(value="0")
        self.v_take, self.v_extra = tk.StringVar(), tk.StringVar()

        def row(r, label, var, browse=None):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Entry(f, textvariable=var).grid(row=r, column=1, columnspan=5, sticky="ew", padx=4)
            if browse:
                ttk.Button(f, text="Browse…", command=browse).grid(row=r, column=6)

        row(0, "Project (.c4d)", self.v_scene, self._pick_scene)
        row(1, "Output (folder/name)", self.v_output, self._pick_output)
        row(2, "Cinema 4D Commandline", self.v_exe, self._pick_exe)
        small = [("Start", self.v_start), ("End", self.v_end), ("Step", self.v_step),
                 ("Frames per launch", self.v_chunk), ("Hang timeout s (0=auto)", self.v_timeout)]
        g = ttk.Frame(f)
        g.grid(row=3, column=0, columnspan=7, sticky="w", pady=4)
        for i, (label, var) in enumerate(small):
            ttk.Label(g, text=label).grid(row=0, column=i * 2, padx=(0 if i == 0 else 10, 2))
            ttk.Entry(g, textvariable=var, width=7).grid(row=0, column=i * 2 + 1)
        row(4, "Take (optional)", self.v_take)
        row(5, "Extra arguments", self.v_extra)
        ttk.Button(f, text="Add job and render", command=self._add).grid(row=6, column=1, sticky="w", pady=6)
        f.columnconfigure(1, weight=1)

    def _build_list(self):
        f = ttk.Frame(self, padding=(10, 0))
        f.pack(fill="both", expand=True)
        cols = ("status", "progress", "restarts", "scene", "problem")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=8)
        for c, w in zip(cols, (90, 100, 70, 220, 360)):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True)

        b = ttk.Frame(self, padding=10)
        b.pack(fill="x")
        ttk.Button(b, text="Start / resume rendering", command=self._start).pack(side="left")
        ttk.Button(b, text="Pause", command=lambda: self._set(J.PAUSED)).pack(side="left", padx=4)
        ttk.Button(b, text="Resume / Retry", command=lambda: self._set(J.QUEUED, start=True)).pack(side="left")
        ttk.Button(b, text="Remove", command=self._remove).pack(side="left", padx=4)
        ttk.Button(b, text="Open log folder", command=self._open_logs).pack(side="left")
        self.v_auto = tk.BooleanVar(value=autostart.is_installed())
        ttk.Checkbutton(b, text="Resume automatically after reboot", variable=self.v_auto,
                        command=self._toggle_autostart).pack(side="right")
        self.v_runner = tk.StringVar()
        ttk.Label(self, textvariable=self.v_runner, padding=(10, 0, 10, 8)).pack(anchor="w")

    # --------------------------------------------------------------- actions
    def _pick_scene(self):
        p = filedialog.askopenfilename(filetypes=[("Cinema 4D", "*.c4d"), ("All files", "*")])
        if p:
            self.v_scene.set(p)
            if not self.v_output.get():
                self.v_output.set(resolve_output(Path(p), None))

    def _pick_output(self):
        p = filedialog.askdirectory()
        if p:
            name = Path(self.v_scene.get()).stem or "render"
            self.v_output.set(str(Path(p) / name))

    def _pick_exe(self):
        p = filedialog.askopenfilename()
        if p:
            self.v_exe.set(p)

    def _add(self):
        scene = Path(self.v_scene.get())
        if not scene.is_file():
            messagebox.showerror("C4D Render Guard", "Choose an existing .c4d project.")
            return
        try:
            start, end, step = int(self.v_start.get()), int(self.v_end.get()), int(self.v_step.get())
            chunk, timeout = int(self.v_chunk.get()), int(self.v_timeout.get())
        except ValueError:
            messagebox.showerror("C4D Render Guard", "Frame numbers must be whole numbers.")
            return
        if end < start or step < 1:
            messagebox.showerror("C4D Render Guard", "Invalid frame range.")
            return
        if not self.v_exe.get() or not Path(self.v_exe.get()).exists():
            messagebox.showerror("C4D Render Guard", "Point to Cinema 4D's Commandline executable.")
            return
        import shlex
        job = J.Job(id=J.new_job_id(), scene=str(scene.resolve()),
                    output=resolve_output(scene, self.v_output.get() or None),
                    start=start, end=end, step=step, commandline=self.v_exe.get(),
                    chunk_size=chunk, frame_timeout=timeout, take=self.v_take.get(),
                    extra_args=shlex.split(self.v_extra.get(), posix=sys.platform != "win32"))
        self.store.save(job)
        self._start()

    def _selected(self) -> list[str]:
        return list(self.tree.selection())

    def _set(self, status, start=False):
        for jid in self._selected():
            job = self.store.load(jid)
            if job:
                job.status = status
                if status == J.QUEUED:
                    job.attempts = {}
                self.store.save(job)
        if start:
            self._start()
        self.refresh(reschedule=False)

    def _remove(self):
        ids = self._selected()
        if ids and messagebox.askyesno("C4D Render Guard", "Remove selected job(s)? Rendered frames are kept."):
            for jid in ids:
                self.store.delete(jid)
        self.refresh(reschedule=False)

    def _start(self):
        if not is_runner_active():
            spawn_background_runner()
        self.after(1500, lambda: self.refresh(reschedule=False))

    def _toggle_autostart(self):
        try:
            autostart.install() if self.v_auto.get() else autostart.uninstall()
        except OSError as exc:
            messagebox.showerror("C4D Render Guard", str(exc))
        self.v_auto.set(autostart.is_installed())

    def _open_logs(self):
        import os
        import subprocess
        d = str(paths.logs_dir())
        if sys.platform == "win32":
            os.startfile(d)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", d])

    def refresh(self, reschedule=True):
        selected = set(self._selected())
        self.tree.delete(*self.tree.get_children())
        for job in self.store.all():
            done, total = progress(job)
            problem = job.last_error if job.status != J.DONE else ""
            self.tree.insert("", "end", iid=job.id, values=(
                job.status, f"{done}/{total}", job.restarts, Path(job.scene).name, problem))
        self.tree.selection_set([i for i in selected if self.tree.exists(i)])
        self.v_runner.set("Renderer: running in background" if is_runner_active()
                          else "Renderer: idle")
        if reschedule:
            self.after(3000, self.refresh)


def main() -> int:
    App().mainloop()
    return 0
