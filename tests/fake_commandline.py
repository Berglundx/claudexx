#!/usr/bin/env python3
"""Stand-in for Cinema 4D's Commandline, used by the tests.

Accepts: -nogui -render scene -frame start end step -oimage prefix
Env controls (each failure happens once, tracked with marker files in FAKE_STATE):
  FAKE_FRAME_SECONDS  render time per frame (default 0.1)
  FAKE_CRASH          comma list of frames at which to crash
  FAKE_HANG           comma list of frames at which to hang forever
  FAKE_TRUNCATE       comma list of frames to write truncated, then crash
  FAKE_ALWAYS_CRASH   frame that crashes on every attempt
"""
import os
import sys
import time
import zlib
import struct
from pathlib import Path


def png_bytes() -> bytes:
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def once(kind: str, frame: int) -> bool:
    frames = {int(x) for x in os.environ.get(kind, "").split(",") if x.strip()}
    if frame not in frames:
        return False
    marker = Path(os.environ["FAKE_STATE"]) / f"{kind}-{frame}"
    if marker.exists():
        return False
    marker.write_text("x")
    return True


def main():
    a = sys.argv[1:]
    scene = a[a.index("-render") + 1]
    i = a.index("-frame")
    start, end, step = int(a[i + 1]), int(a[i + 2]), int(a[i + 3])
    prefix = a[a.index("-oimage") + 1]
    assert Path(scene).exists(), scene
    print(f"Rendering {scene} {start}-{end}", flush=True)
    secs = float(os.environ.get("FAKE_FRAME_SECONDS", "0.1"))
    always = os.environ.get("FAKE_ALWAYS_CRASH")
    for f in range(start, end + 1, step):
        time.sleep(secs)
        if always and int(always) == f:
            os._exit(3)
        if once("FAKE_CRASH", f):
            print(f"CRASH at {f}", flush=True)
            os._exit(139)
        if once("FAKE_HANG", f):
            print(f"HANG at {f}", flush=True)
            while True:
                time.sleep(1)
        out = Path(f"{prefix}{f:04d}.png")
        if once("FAKE_TRUNCATE", f):
            out.write_bytes(png_bytes()[:20])
            os._exit(139)
        tmp = out.with_suffix(".part")
        tmp.write_bytes(png_bytes())
        os.replace(tmp, out)
        print(f"Frame {f} done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
