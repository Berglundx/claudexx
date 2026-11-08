"""Find rendered frames on disk and check that they are complete."""

from __future__ import annotations

import re
from pathlib import Path

PNG_END = b"IEND\xaeB`\x82"
JPEG_END = b"\xff\xd9"

# Formats whose files we can check for truncation.
VERIFIABLE = {".png", ".jpg", ".jpeg"}


def _pattern(base: str) -> re.Pattern:
    # shot0001.png, shot_0001.png, shot.0001.exr ... but not shot_diffuse0001.png
    return re.compile(r"^" + re.escape(base) + r"[._-]?(\d+)\.[A-Za-z0-9]+$")


def scan(output_prefix: str) -> dict[int, Path]:
    """Map frame number -> file for every main-pass image of this output prefix."""
    prefix = Path(output_prefix)
    directory, base = prefix.parent, prefix.name
    if not directory.is_dir():
        return {}
    pat = _pattern(base)
    found: dict[int, Path] = {}
    for entry in directory.iterdir():
        m = pat.match(entry.name)
        if m and entry.is_file():
            found[int(m.group(1))] = entry
    return found


def is_complete(path: Path) -> bool:
    """False if the file is empty or (for PNG/JPEG) visibly truncated."""
    try:
        size = path.stat().st_size
        if size == 0:
            return False
        ext = path.suffix.lower()
        if ext not in VERIFIABLE:
            return True
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 64))
            tail = fh.read().rstrip(b"\x00")
        if ext == ".png":
            return tail.endswith(PNG_END)
        return tail.endswith(JPEG_END)
    except OSError:
        return False


def verifiable(path: Path) -> bool:
    return path.suffix.lower() in VERIFIABLE
