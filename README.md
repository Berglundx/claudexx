# C4D Render Guard

Renders a Cinema 4D project with Cinema 4D's own **Commandline** renderer and
keeps it going on its own:

- **C4D crashes**: the guard notices, restarts Cinema 4D and continues from the
  first missing frame.
- **C4D hangs** (for example a frozen frame or a crash dialog): if no new frame
  appears for too long, Cinema 4D is killed and restarted.
- **The computer restarts** (power cut, Windows Update, a blue screen): the
  render picks up again automatically after logon, without re-rendering
  finished frames.
- **Half-written frames** are found and rendered again. PNG and JPEG files are
  checked for truncation. For other formats (EXR, TIFF and so on), the last
  frame written before a crash is thrown away to be safe.
- **A frame that crashes every time** doesn't loop forever. After 5 crashes in
  a row (you can change this) the job is marked *failed* and the next job in
  the queue starts.

It's plain Python 3.9+ and needs nothing else installed. It runs on Windows,
macOS and Linux.

## Quick start

1. Install **Python 3** from python.org. On Windows, tick *"Add python.exe to
   PATH"* during setup.
2. Put this folder somewhere permanent, for example `C:\Tools\C4DRenderGuard`.
3. Start rendering:
   - **Windows:** drag your `.c4d` file(s) onto **`C4D Render Guard.bat`**, or
     double-click it and drop files into the window.
   - **Mac:** double-click **`Create Mac App.command`** once. It creates
     **`C4D Render Guard.app`**. Drag `.c4d` files onto that app (or onto its
     Dock icon), or open it and drop files into the window.

That's all. The program:

- reads the **frame range** from the project's own Render Settings,
- saves frames to the **save path** set in the project's Render Settings. If
  none is set, or it uses tokens like `$prj`, frames go to
  `<project folder>/renders/<project name>/`,
- switches on **resume after restart** automatically,
- renders dropped projects **one after another**. Dropping a project that is
  already in the list resumes it instead of starting over.

The render runs in the background, so you can close the window. Open the app
again to check progress. **Double-click a job** to open its frames.

Only if the frame range can't be read (see below) does the program ask for it.
**Frames…** changes the range of a paused or finished job, and **Settings**
holds the advanced options.

> **How the frame range is read:** the program opens the project with
> `c4dpy`, the Python that comes with Cinema 4D, next to `Commandline`. Like
> `Commandline`, it needs a valid license. If it isn't licensed or can't be
> found, you're simply asked for the range.

> **Drag & drop into the window** uses the small `tkinterdnd2` package, which
> is installed automatically the first time (internet needed once). Without
> it, click the box to choose files, or drop files onto the app icon.

> **Unattended render machines:** rendering resumes when the user **logs in**,
> because Cinema 4D's license needs a user session. On a machine that should
> recover without anyone there, turn on automatic login.

## Command line

```bash
# render a project with its own frame range and output (Ctrl+C stops; it resumes later)
python -m c4dguard render "D:/proj/shot010.c4d"

# ...or override the frames / output
python -m c4dguard render "D:/proj/shot010.c4d" -s 0 -e 250 -o "D:/renders/shot010/"

# or queue several, then render the queue
python -m c4dguard add  shot010.c4d
python -m c4dguard add  shot020.c4d -s 0 -e 120 --take "Final" --extra "-threads 16"
python -m c4dguard run

python -m c4dguard status            # progress, restarts, last problem
python -m c4dguard pause  <job-id>   # also: resume, retry, remove (ids can be shortened)
python -m c4dguard autostart install # resume after reboot (uninstall / status)
```

Useful options for `add` / `render`:

| option | meaning |
|---|---|
| `-o / --output` | Output folder, or a folder plus file name prefix. Default: `<project folder>/renders/<name>/<name>`. Frames are saved as `<name>0000.png` and so on, using the format set in the project's render settings. |
| `--commandline` | Path to `Commandline.exe`. Auto-detected by default, or set the `C4D_COMMANDLINE` environment variable. |
| `--chunk N` | Frames rendered per Cinema 4D launch (default 10). Smaller numbers lose less work in a crash but load the scene more often. |
| `--timeout S` | Seconds without a new frame before Cinema 4D counts as hung. The default `0` is automatic: 2 h until the first frame is timed, then 4× the slowest frame (at least 30 min). Set it yourself if your frames are very long. |
| `--max-attempts N` | How many crashes in a row on the same frame before the job fails (default 5). |
| `--take`, `--extra` | Take to render, and any extra Commandline arguments (for example `-threads 16`). |

## How the recovery works

- Each job is a small JSON file, written atomically so a power cut can't
  corrupt it. Jobs are kept in `%LOCALAPPDATA%\C4DRenderGuard` (Windows),
  `~/Library/Application Support/C4DRenderGuard` (macOS) or
  `~/.local/share/c4dguard` (Linux). Set `C4DGUARD_HOME` to use a different
  folder.
- Progress comes from **the frames actually on disk**, so nothing is lost even
  if the guard itself is killed. Before each launch the guard scans the output
  folder, deletes broken frames and renders the next chunk of missing ones with
  `Commandline -nogui -render <scene> -frame a b step -oimage <output>`.
- While Cinema 4D runs, the job remembers which frames it is working on. If
  the guard starts and finds that record still there, the machine or the app
  died mid-render, so it cleans up the frame that may be partial before
  continuing.
- Cinema 4D runs in its own process group, and the whole tree is killed when
  it hangs.
- The startup entry is a Startup-folder script on Windows, a LaunchAgent on
  macOS, or a systemd user service on Linux. It waits 60 s after logon for
  things like the license and network drives, and restarts the guard if the
  guard itself ever crashes. Only one guard runs at a time, enforced by an OS
  file lock.
- Logs are in the `logs` folder: `guard.log` for the guard, and
  `<job>.c4d.log` with Cinema 4D's own output.

## Tests

```bash
python -m unittest discover tests -v
```

The tests use a fake `Commandline` (`tests/fake_commandline.py`) that crashes,
hangs or writes truncated frames on demand. One test kills the guard and
Cinema 4D outright in the middle of a render, like a power cut, then checks
that the render finishes without redoing finished frames.
