#!/usr/bin/env python3
"""Stand-in for c4dpy: prints the render settings from $FAKE_C4DPY_INFO."""
import os
import sys

assert sys.argv[1].endswith(".py") and os.path.exists(sys.argv[1])
assert os.path.exists(sys.argv[-1]), sys.argv[-1]
import time

print("Cinema 4D banner noise...", flush=True)
hang = os.environ.get("FAKE_C4DPY_HANG", "")
if hang == "before":            # e.g. waiting on a license prompt: never answers
    while True:
        time.sleep(1)
info = os.environ.get("FAKE_C4DPY_INFO")
if not info:
    print("license error")
    sys.exit(1)
print("C4DGUARD_INFO:" + info, flush=True)
if hang == "after":             # answers, then doesn't quit (known c4dpy behaviour)
    while True:
        time.sleep(1)
