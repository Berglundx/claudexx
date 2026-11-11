"""The app icon, drawn in code (needs Pillow; everything works without it too)."""

from __future__ import annotations


def make_image(size: int = 64):
    """A rounded blue square with a white 'play' triangle. Returns a PIL image or None."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    scale = 4                                        # draw big, shrink for smooth edges
    n = size * scale
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([n * 0.04, n * 0.04, n * 0.96, n * 0.96], radius=n * 0.22,
                        fill=(28, 96, 214, 255))
    d.polygon([(n * 0.36, n * 0.26), (n * 0.36, n * 0.74), (n * 0.76, n * 0.50)],
              fill=(255, 255, 255, 255))
    return img.resize((size, size), Image.LANCZOS)


def save_ico(path) -> bool:
    """Write a multi-size .ico (used for the exe and the installer)."""
    img = make_image(256)
    if img is None:
        return False
    img.save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    return True
