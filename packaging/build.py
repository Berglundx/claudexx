"""Build the standalone app with PyInstaller.

    python packaging/build.py

Windows -> dist/C4DRenderGuard.exe        (single file, no Python needed)
macOS   -> dist/C4D Render Guard.app  + dist/C4DRenderGuard-mac.zip
Linux   -> dist/c4dguard                  (single file)
"""
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))
from c4dguard import __version__  # noqa: E402


def pyinstaller(*args: str) -> None:
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--distpath", str(DIST), "--workpath", str(ROOT / "build"),
           "--specpath", str(ROOT / "build"),
           "--collect-all", "tkinterdnd2", *args, str(ROOT / "launcher.py")]
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def main() -> None:
    shutil.rmtree(DIST, ignore_errors=True)
    if sys.platform == "win32":
        pyinstaller("--onefile", "--windowed", "--name", "C4DRenderGuard")
    elif sys.platform == "darwin":
        name = "C4D Render Guard"
        pyinstaller("--windowed", "--name", name,
                    "--osx-bundle-identifier", "com.c4drenderguard.app")
        app = DIST / f"{name}.app"
        # Let Finder/Dock accept .c4d files dropped on the app icon.
        plist_path = app / "Contents" / "Info.plist"
        with open(plist_path, "rb") as fh:
            plist = plistlib.load(fh)
        plist.update({
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Cinema 4D Project",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "CFBundleTypeExtensions": ["c4d"],
                "LSItemContentTypes": ["public.data"],
            }],
        })
        with open(plist_path, "wb") as fh:
            plistlib.dump(plist, fh)
        # Editing Info.plist breaks the ad-hoc signature; sign again.
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app),
                        str(DIST / "C4DRenderGuard-mac.zip")], check=True)
        shutil.rmtree(DIST / name, ignore_errors=True)   # leftover onedir copy
    else:
        pyinstaller("--onefile", "--name", "c4dguard")
    print("Built:", *sorted(p.name for p in DIST.iterdir()), sep="\n  ")


if __name__ == "__main__":
    main()
