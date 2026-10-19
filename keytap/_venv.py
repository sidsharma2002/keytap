"""Re-run the current script under the keytap virtualenv created by install.sh.

Standard library only, so entry scripts can call it before importing anything that
needs the installed dependencies.
"""
import os
import sys

VENV = os.environ.get("KEYTAP_VENV") or os.path.join(os.path.expanduser("~"), ".keytap", "venv")


def venv_python() -> str:
    return os.path.join(VENV, "bin", "python")


def ensure_venv() -> None:
    """Replace this process with the same command under the venv python, if it exists
    and is not already the running interpreter. Silently does nothing otherwise, so a
    checkout without an install (or KEYTAP_NO_VENV=1) still runs on the current python."""
    if os.environ.get("KEYTAP_NO_VENV"):
        return
    py = venv_python()
    if not os.access(py, os.X_OK):
        return
    if os.path.realpath(sys.prefix) == os.path.realpath(VENV):
        return
    try:
        os.execv(py, [py] + sys.argv)
    except OSError:
        pass
