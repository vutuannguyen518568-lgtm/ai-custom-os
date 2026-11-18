#!/usr/bin/env python3
"""Session start-up: restore saved display mode, unblock radios, unmute audio and run the
`startup_commands` from os_config.json (skipping any that are missing or already running)."""
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import common


def running_names():
    try:
        import psutil
        return {p.info["name"] for p in psutil.process_iter(["name"])}
    except Exception:
        return set()


def quiet(argv, **kw):
    try:
        return subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, **kw)
    except (OSError, subprocess.SubprocessError):
        return None


def main():
    cfg = common.load_config()
    disp = cfg.get("display", {})
    if disp.get("output") and disp.get("mode"):
        argv = ["xrandr", "--output", disp["output"], "--mode", disp["mode"]]
        if disp.get("rate"):
            argv += ["--rate", disp["rate"]]
        quiet(argv)
    quiet(["xsetroot", "-cursor_name", "left_ptr"])
    quiet(["rfkill", "unblock", "all"])
    names = running_names()
    for cmd in cfg.get("startup_commands", []):
        exe = common.expand(cmd).split()[0]
        if not shutil.which(exe) and not os.path.exists(exe):
            continue
        if os.path.basename(exe) in names:
            continue
        common.run(cmd)
        time.sleep(0.3)
    time.sleep(2)                                  # let the sound server come up, then make sure it is audible
    quiet(["amixer", "-q", "sset", "Master", "unmute"])
    quiet(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])


if __name__ == "__main__":
    main()
