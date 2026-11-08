"""Entry point for the packaged app (PyInstaller) and for autostart entries.

Running this file works from any folder, unlike `python -m c4dguard`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from c4dguard.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
