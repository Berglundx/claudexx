# C4D Render Guard

Opens Cinema 4D, loads your project and renders it to the Picture Viewer,
just like you would by hand, and keeps the render going on its own:

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

## Install

Download the latest build from this repository's **Releases** page (the
release called *"latest"*).

**Windows:** download and run **`C4DRenderGuard-Setup-<version>.exe`**. No admin rights
and no Python needed. Windows may warn that the app is from an unknown
publisher because it isn't code-signed; click **More info → Run anyway**. You
then get:

- a Start menu entry and a desktop icon (drop `.c4d` files on it),
- a **tray icon** next to the clock. Closing the window keeps the app there;
  hover it for progress, double-click it to reopen the window, right-click →
  *Quit* to stop rendering and exit,
- **Render with C4D Render Guard** when you right-click a `.c4d` file,
- a normal uninstaller under *Settings → Apps*.

(`C4DRenderGuard.exe` on the same page is the same program without an
installer, if you'd rather just run it.)

**Mac (Apple Silicon):** download **`C4DRenderGuard-mac.zip`**, unzip it and
move **C4D Render Guard** to *Applications*. The first time, **right-click →
Open**, because the app isn't notarized by Apple.

## Use it

Drag one or more `.c4d` files onto the app icon, into its window, or (on
Windows) right-click them and choose **Render with C4D Render Guard**.

A small window asks **which frames to render** (it remembers your last
range) and **where to save them**. The default is
`<project folder>/renders/<project name>/`. Click **Render** and that's it.
The program also:

- switches on **resume after restart** automatically,
- renders dropped projects **one after another**,
- keeps rendering in the background, so you can close the window. Open the
  app again to check progress, and **double-click a job** to open its frames.

**Frames…** changes the range of a paused or finished job. **Settings** holds
the advanced options.

> **Unattended render machines:** rendering resumes when the user **logs in**,
> because Cinema 4D's license needs a user session. On a machine that should
> recover without anyone there, turn on automatic login.

## How it renders

By default the app **starts Cinema 4D with its normal window**, opens the
project, sets the frame range and save folder you chose, and runs *Render to
Picture Viewer*. You can watch it work. When the frames are done it closes
Cinema 4D again. This uses your normal Cinema 4D license and whatever renderer
the project uses, exactly as when you render by hand.

- The **image format** comes from the project's Render Settings. A movie
  format (MP4, MOV) is switched to a PNG sequence for the render, because a
  movie file can't be resumed after a crash. Your project file isn't changed.
- Don't close that Cinema 4D window while it renders. If you do, the guard
  opens it again and continues.

### Pop-ups

Pop-ups that would stop an unattended render are handled automatically (you
can switch this off in Settings):

- The project is opened in a way that makes Cinema 4D **skip its own
  warnings** (missing fonts, missing plugins, saved with a newer version).
- The render is told **not to stop on missing textures**. Those objects then
  render without the texture, so check the result if assets may be missing.
- On Windows the guard also **watches the windows of the Cinema 4D it
  started** and dismisses the ones in the way: system message boxes, any
  dialog that freezes Cinema 4D's main window, and whatever is still open if
  the render hasn't started a minute after launch. It clicks *Continue / Yes /
  OK* where there are such buttons, otherwise presses Enter, and closes the
  window if that didn't help. The Picture Viewer and normal panels are left
  alone while a render is running.
- Every pop-up that was closed is listed, with its text, under **Details…**.

It can't answer a **login or license** window for you. If Cinema 4D needs you
to sign in, do that once by hand. On Mac only the first two points apply.

Under **Settings → How to render** there are two alternatives without a
window, the way render farms like Deadline run Cinema 4D:

- **Cinema 4D in the background** (`Cinema 4D.exe -nogui -render …`), which
  uses the normal Cinema 4D license.
- **Commandline renderer** (`Commandline.exe -nogui -render …`), which needs
  its own Command Line license.

## If nothing renders

The job shows **Failed** instead of retrying forever. **Double-click it** (or
select it and click **Details…**) to see why, including everything Cinema 4D
printed. Use **Copy to clipboard** to send it to someone. The usual causes:

- **Cinema 4D didn't respond after starting.** It is probably showing a
  dialog; look at its window. If it never works, try *Cinema 4D in the
  background* in Settings.
- **Cinema 4D wasn't found.** Set the **Cinema 4D folder** in Settings.
- **The render itself failed** (missing plugin, textures, or a third-party
  renderer's license). The Picture Viewer and Cinema 4D's console show the
  error. Open the project in Cinema 4D and render one frame by hand to check.
- **Background modes only:** saving must be switched on in *Render Settings →
  Save*, and the Commandline renderer needs its own license.

## Running from source (instead of the installer)

Install Python 3 from python.org, then:

- **Windows:** drag `.c4d` files onto `C4D Render Guard.bat`, or double-click
  it.
- **Mac:** double-click `Create Mac App.command` once to get a droplet app.
- **Anywhere:** `python launcher.py` (window) or `python launcher.py <command>`.

To build the installers yourself, run `pip install pyinstaller tkinterdnd2 pystray Pillow`,
then `python packaging/build.py`. Then, on Windows, run
`ISCC packaging\installer.iss` (Inno Setup 6). GitHub Actions
(`.github/workflows/build.yml`) does all of this automatically on every push
and publishes the result as the *latest* release.

## Command line

The installed program also works from a terminal (`C4DRenderGuard.exe <command>`
on Windows; `python launcher.py <command>` from source):

```bash
# render frames 0-250 (Ctrl+C stops; it resumes later)
c4dguard render "D:/proj/shot010.c4d" -s 0 -e 250 -o "D:/renders/shot010/"

# or queue several, then render the queue
c4dguard add  shot010.c4d -s 0 -e 250
c4dguard add  shot020.c4d -s 0 -e 120 --take "Final" --extra "-threads 16"
c4dguard run

c4dguard status            # progress, restarts, last problem
c4dguard pause  <job-id>   # also: resume, retry, remove (ids can be shortened)
c4dguard autostart install # resume after reboot (uninstall / status)
```

Useful options for `add` / `render`:

| option | meaning |
|---|---|
| `-o / --output` | Output folder, or a folder plus file name prefix. Default: `<project folder>/renders/<name>/<name>`. Frames are saved as `<name>0000.png` and so on, using the format set in the project's render settings. |
| `--mode` | `app` (default: open Cinema 4D and render in the Picture Viewer), `background` or `commandline`. |
| `--commandline` | Run exactly this program headless. Cinema 4D is found automatically, or set `C4D_APP` / `C4D_COMMANDLINE`. |
| `--chunk N` | Background modes only: frames rendered per Cinema 4D launch (default 10). Smaller numbers lose less work in a crash but load the scene more often. |
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
  folder, deletes broken frames and renders the missing ones. In the default
  mode it starts Cinema 4D with `g_additionalModulePath` pointing at a small
  helper plugin (`c4dguard/helper.py`); the helper opens the project, applies
  the frame range and save path, runs *Render to Picture Viewer* and reports
  each step back. The background modes run
  `-nogui -render <scene> -frame a b step -oimage <output>` in chunks.
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

The tests use a fake Cinema 4D app (`tests/fake_c4d_app.py`, which runs the
real helper plugin against a stand-in `c4d` module) and a fake `Commandline`
(`tests/fake_commandline.py`) that crashes,
hangs or writes truncated frames on demand. One test kills the guard and
Cinema 4D outright in the middle of a render, like a power cut, then checks
that the render finishes without redoing finished frames.
