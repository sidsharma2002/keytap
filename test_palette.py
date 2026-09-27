"""
Standalone palette test launcher.
Runs palette_proc.py directly — no pygame needed.

Usage:
    python3 test_palette.py              # palette mode (default)
    python3 test_palette.py viewer       # viewer mode
    python3 test_palette.py vhraw        # dump raw uiautomator output and exit (debug)
"""
import json
import subprocess
import sys
import os

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
PROC = os.path.join(os.path.dirname(__file__), "palette_proc.py")

SAMPLE_PACKAGES = [
    "com.gojek.app",
    "com.gojek.app.staging",
    "com.google.android.chrome",
    "com.whatsapp",
    "com.instagram.android",
    "com.spotify.music",
]

SAMPLE_DEEPLINKS = [
    "gojek://home",
    "gojek://order/123",
]


def run_palette():
    data = {
        "mode":      "palette",
        "packages":  SAMPLE_PACKAGES,
        "clipboard": "Hello from clipboard",
        "deeplinks": SAMPLE_DEEPLINKS,
        "serial":    None,
        "adb_path":  ADB,
    }
    payload = (json.dumps(data) + "\n").encode()
    result = subprocess.run(
        [sys.executable, PROC],
        input=payload,
        capture_output=False,   # let stdout/stderr pass through
    )
    print(f"\n[test] exit code: {result.returncode}")


def run_viewer():
    data = {
        "mode":  "viewer",
        "title": "Test Viewer",
        "items": [
            ["key_one",   "value_one"],
            ["key_two",   "value_two"],
            ["key_three", "a longer value that should scroll"],
        ],
    }
    payload = (json.dumps(data) + "\n").encode()
    subprocess.run([sys.executable, PROC], input=payload, capture_output=False)


def run_vhraw():
    """Dump raw uiautomator output so you can see what adb actually returns."""
    import shlex
    cmd = [ADB, "shell", "uiautomator", "dump", "/dev/stdout"]
    print(f"[test] running: {shlex.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, timeout=20)
    print(f"[test] returncode: {result.returncode}")
    print(f"[test] stdout ({len(result.stdout)} bytes):")
    print(result.stdout.decode(errors="replace")[:500])
    print(f"[test] stderr ({len(result.stderr)} bytes):")
    print(result.stderr.decode(errors="replace")[:500])


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "palette"
    if mode == "viewer":
        run_viewer()
    elif mode == "vhraw":
        run_vhraw()
    else:
        run_palette()
