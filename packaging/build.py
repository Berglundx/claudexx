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
           "--collect-all", "tkinterdnd2", "--hidden-import", "pystray._win32",
           "--hidden-import", "PIL.ImageTk", *args, str(ROOT / "launcher.py")]
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def windows_version_file() -> Path:
    """Version resource shown in the exe's Properties → Details."""
    nums = tuple(int(x) for x in (__version__.split(".") + ["0", "0", "0"])[:4])
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'C4D Render Guard'),
      StringStruct('FileDescription', 'C4D Render Guard'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'C4DRenderGuard'),
      StringStruct('OriginalFilename', 'C4DRenderGuard.exe'),
      StringStruct('ProductName', 'C4D Render Guard'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path = ROOT / "build" / "version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def main() -> None:
    shutil.rmtree(DIST, ignore_errors=True)
    print(f"Building C4D Render Guard {__version__}", flush=True)
    if sys.platform == "win32":
        from c4dguard import icon
        ico = ROOT / "build" / "icon.ico"
        ico.parent.mkdir(parents=True, exist_ok=True)
        extra = ["--icon", str(ico)] if icon.save_ico(ico) else []
        pyinstaller("--onefile", "--windowed", "--name", "C4DRenderGuard",
                    "--version-file", str(windows_version_file()), *extra)
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
