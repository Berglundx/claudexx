"""System tray icon (Windows). Menu clicks are queued for the Tk thread."""

from __future__ import annotations

import logging
import queue
import sys

from . import icon as iconlib

log = logging.getLogger("c4dguard")
TITLE = "C4D Render Guard"


class Tray:
    def __init__(self):
        self.events: queue.Queue = queue.Queue()   # "open" | "logs" | "quit"
        self.icon = None

    def start(self) -> bool:
        """Show the tray icon. Returns False where it is not available."""
        if sys.platform != "win32":
            return False        # macOS needs the main thread, which Tk owns; Linux trays vary
        try:
            import pystray
            image = iconlib.make_image(64)
            if image is None:
                return False
            item = pystray.MenuItem
            menu = pystray.Menu(
                item("Open C4D Render Guard", lambda: self.events.put("open"), default=True),
                item("Open log folder", lambda: self.events.put("logs")),
                pystray.Menu.SEPARATOR,
                item("Quit (stops rendering)", lambda: self.events.put("quit")),
            )
            self.icon = pystray.Icon("c4drenderguard", image, TITLE, menu)
            self.icon.run_detached()
            log.info("tray icon active")
            return True
        except Exception as exc:  # noqa: BLE001 - the app must work without a tray
            log.warning("no tray icon: %s", exc)
            self.icon = None
            return False

    @property
    def active(self) -> bool:
        return self.icon is not None

    def set_tooltip(self, text: str) -> None:
        if self.icon is not None:
            try:
                self.icon.title = text[:120]        # Windows limits tooltip length
            except Exception:  # noqa: BLE001
                pass

    def notify(self, message: str) -> None:
        if self.icon is not None:
            try:
                self.icon.notify(message, TITLE)
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001
                pass
            self.icon = None
