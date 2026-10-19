"""Headless smoke test: can this Python import everything keytap needs?

    python -m keytap.smoke

Needs no phone and no display. Checks the required third-party packages, the optional
ones (reported as warnings), then imports every keytap module so a missing dependency
or a syntax error shows up here instead of in the middle of a session. Exit code 0 means
all required imports worked.
"""
import importlib
import os
import pkgutil
import sys

# Import name -> pip package, for the packages keytap cannot run without.
REQUIRED = {
    "av": "av",
    "numpy": "numpy",
    "PIL": "Pillow",
    "pygame": "pygame",
    "uiautomator2": "uiautomator2",
    # Not pip-installable: it ships with the Python build (the command palette needs it).
    "tkinter": "tkinter",
}
HINTS = {
    "tkinter": "install Tk for this Python: macOS `brew install python-tk@%(v)s`, "
               "Debian/Ubuntu `sudo apt install python3-tk`, Fedora `sudo dnf install python3-tkinter`",
}
# Only used by network capture and the profiler, and imported lazily.
OPTIONAL = {
    "mitmproxy": "mitmproxy",
    "perfetto": "perfetto",
}


def _try_import(name: str):
    """Returns None on success, else (message, name of the missing module or None)."""
    try:
        importlib.import_module(name)
        return None
    except Exception as e:  # ImportError, but also broken native libs
        return f"{type(e).__name__}: {e}", getattr(e, "name", None)


def run() -> int:
    # No window, no audio: lets pygame import on a machine without a display.
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

    failed = 0
    print(f"python {sys.version.split()[0]} at {sys.executable}")

    missing = set()
    for mod, pkg in REQUIRED.items():
        err = _try_import(mod)
        if err:
            failed += 1
            missing.add(err[1] or mod)
            print(f"  FAIL  {pkg}: {err[0]}")
            if mod in HINTS:
                v = f"{sys.version_info.major}.{sys.version_info.minor}"
                print(f"        fix: {HINTS[mod] % {'v': v}}")
        else:
            print(f"  ok    {pkg}")

    for mod, pkg in OPTIONAL.items():
        err = _try_import(mod)
        if err:
            print(f"  warn  {pkg} (optional): {err[0]}")
        else:
            print(f"  ok    {pkg} (optional)")

    import keytap

    for info in pkgutil.walk_packages(keytap.__path__, "keytap."):
        err = _try_import(info.name)
        if err and err[1] not in missing:  # a missing required package is already reported
            failed += 1
            print(f"  FAIL  {info.name}: {err[0]}")
    if not failed:
        print("  ok    all keytap modules import")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
