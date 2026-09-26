import subprocess
import re

from config import ADB_PATH, ARGS


def adb(*cmd):
    full = [ADB_PATH]
    if ARGS.serial:
        full += ["-s", ARGS.serial]
    full += list(cmd)
    return subprocess.run(full, capture_output=True, timeout=15)


def get_device_size():
    out = adb("shell", "wm", "size").stdout.decode()
    m = re.search(r'(\d+)x(\d+)', out)
    if not m:
        return 1080, 2400
    w, h = int(m.group(1)), int(m.group(2))
    return (w, h) if w < h else (h, w)


def take_screencap():
    return adb("exec-out", "screencap", "-p").stdout
