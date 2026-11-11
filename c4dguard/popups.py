"""Close pop-ups that stop Cinema 4D from rendering (Windows).

Only windows of the Cinema 4D process the guard started are considered, and
only in situations where a window is really in the way:

* a standard system message box (window class "#32770"): always dismissed;
* a modal dialog: Cinema 4D's main window is disabled while one is open;
* Cinema 4D has been up for a while but the render has not started
  (`stuck=True`): whatever extra windows it shows are dismissed.

Dismissing means: click the button that lets work continue (Continue / Yes /
OK / Ignore ...) if the window has ordinary buttons, otherwise press Enter
(the dialog's default button), and if it is still there on the next round,
close it. A window that survives three tries is left alone.

On other systems `Watcher.check()` does nothing.
"""

from __future__ import annotations

import logging
import sys
import time

log = logging.getLogger("c4dguard")

# Button captions in order of preference (lower case, '&' removed).
PREFERRED = ("continue", "yes", "ok", "ignore", "skip", "close", "don't send", "dont send",
             "later", "not now", "cancel", "no",
             "weiter", "fortfahren", "ja", "ignorieren", "schließen", "abbrechen", "nein")
MAX_TRIES = 3
INTERVAL = 2.0          # seconds between looks


class Popup:
    def __init__(self, hwnd, title, cls, text, buttons):
        self.hwnd, self.title, self.cls, self.text, self.buttons = hwnd, title, cls, text, buttons

    def describe(self) -> str:
        parts = [self.title or "(untitled window)"]
        if self.text:
            parts.append(self.text)
        return ": ".join(parts)[:300]


def available() -> bool:
    return sys.platform == "win32"


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowEnabled.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

    GW_OWNER = 4
    WM_CLOSE, WM_KEYDOWN, WM_KEYUP, BM_CLICK, VK_RETURN = 0x0010, 0x0100, 0x0101, 0x00F5, 0x0D
    TH32CS_SNAPPROCESS = 0x2

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def process_tree(root_pid: int) -> set[int]:
        """root_pid and all its descendants."""
        children: dict[int, list[int]] = {}
        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap in (None, wintypes.HANDLE(-1).value):
            return {root_pid}
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
                ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snap)
        tree, todo = {root_pid}, [root_pid]
        while todo:
            for child in children.get(todo.pop(), []):
                if child not in tree:
                    tree.add(child)
                    todo.append(child)
        return tree

    def _text(hwnd) -> str:
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        return buf.value.strip()

    def _class(hwnd) -> str:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def _area(hwnd) -> int:
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        return max(0, r.right - r.left) * max(0, r.bottom - r.top)

    def _children(hwnd) -> list:
        found = []

        def cb(child, _):
            found.append(child)
            return True
        user32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
        return found

    def _windows(pids: set[int]) -> list:
        found = []

        def cb(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd) and _area(hwnd) > 0:
                found.append(hwnd)
            return True
        user32.EnumWindows(WNDENUMPROC(cb), 0)
        return found

    def find(root_pid: int, stuck: bool = False) -> list[Popup]:
        """Windows of this process tree that are in the way right now."""
        wins = _windows(process_tree(root_pid))
        if not wins:
            return []
        unowned = [h for h in wins if not user32.GetWindow(h, GW_OWNER) and _class(h) != "#32770"]
        main = max(unowned, key=_area) if unowned else None
        modal = main is not None and not user32.IsWindowEnabled(main)
        out = []
        for h in wins:
            if h == main:
                continue
            cls = _class(h)
            if not (cls == "#32770" or ((modal or stuck) and user32.IsWindowEnabled(h))):
                continue
            buttons, texts = [], []
            for c in _children(h):
                ccls = _class(c).lower()
                if ccls == "button" and user32.IsWindowVisible(c):
                    buttons.append((c, _text(c)))
                elif ccls == "static" and _text(c):
                    texts.append(_text(c))
            out.append(Popup(h, _text(h), cls, " ".join(texts), buttons))
        return out

    def dismiss(popup: Popup, attempt: int) -> str:
        """Try to get rid of the window; returns what was done."""
        named = {t.replace("&", "").strip().lower(): h for h, t in popup.buttons if t}
        for want in PREFERRED:
            if want in named:
                user32.PostMessageW(named[want], BM_CLICK, 0, 0)
                return f"clicked “{want}”"
        if popup.buttons and attempt == 0:
            user32.PostMessageW(popup.buttons[0][0], BM_CLICK, 0, 0)
            return f"clicked “{popup.buttons[0][1]}”"
        if attempt == 0:
            user32.PostMessageW(popup.hwnd, WM_KEYDOWN, VK_RETURN, 0)
            user32.PostMessageW(popup.hwnd, WM_KEYUP, VK_RETURN, 0)
            return "pressed Enter"
        user32.PostMessageW(popup.hwnd, WM_CLOSE, 0, 0)
        return "closed it"

else:
    def find(root_pid: int, stuck: bool = False) -> list[Popup]:
        return []

    def dismiss(popup: Popup, attempt: int) -> str:
        return ""


class Watcher:
    """Call check() regularly while Cinema 4D runs."""

    def __init__(self, root_pid: int, interval: float = INTERVAL):
        self.pid = root_pid
        self.interval = interval
        self._last = 0.0
        self._tries: dict[int, int] = {}

    def check(self, stuck: bool = False) -> list[str]:
        """Dismiss what is in the way; returns a description of each pop-up handled."""
        if not available() or time.time() - self._last < self.interval:
            return []
        self._last = time.time()
        handled = []
        try:
            for popup in find(self.pid, stuck):
                tries = self._tries.get(popup.hwnd, 0)
                if tries >= MAX_TRIES:
                    continue
                self._tries[popup.hwnd] = tries + 1
                action = dismiss(popup, tries)
                if tries == 0:                       # report each pop-up once
                    handled.append(f"{popup.describe()}  → {action}")
                log.info("pop-up “%s”: %s", popup.describe(), action)
        except Exception as exc:  # noqa: BLE001 - never let this break a render
            log.warning("pop-up check failed: %s", exc)
        return handled
