import argparse
import os
import shutil


def _find_adb():
    # 1. system PATH
    found = shutil.which("adb")
    if found:
        return found
    # 2. common macOS install location
    candidate = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
    if os.path.isfile(candidate):
        return candidate
    # 3. common Linux install location
    candidate = os.path.expanduser("~/Android/Sdk/platform-tools/adb")
    if os.path.isfile(candidate):
        return candidate
    return "adb"  # last resort: hope it's on PATH


ADB_PATH    = _find_adb()
CURSOR_MODE = True   # True = arrow-key cursor; False = 2-letter label typing
CURSOR_JUMP = 5      # Shift+arrow jumps this many cells at once
FIFO_PATH   = "/tmp/keytap_stream.fifo"
HAS_SCRCPY  = shutil.which("scrcpy") is not None
HAS_FFMPEG  = shutil.which("ffmpeg") is not None


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--serial", default=None, help="ADB serial (e.g. emulator-5554)")
    p.add_argument("--cols",   type=int, default=10)
    p.add_argument("--rows",   type=int, default=32)
    p.add_argument("--height", type=int, default=800, help="mirror window height px")
    return p.parse_args()


ARGS = parse_args()
