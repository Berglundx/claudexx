"""Defaults for a project dropped into the app."""

from __future__ import annotations

from pathlib import Path


def default_output(scene: Path) -> str:
    """Frames go to <project folder>/renders/<project>/<project>0000.ext by default."""
    return str(scene.parent / "renders" / scene.stem / scene.stem)
