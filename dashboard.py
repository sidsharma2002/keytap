#!/usr/bin/env python3
"""dashboard.py: the command lives in keytap.cli.dashboard."""
import sys

from keytap._venv import ensure_venv

ensure_venv()

from keytap.cli.dashboard import main

if __name__ == "__main__":
    sys.exit(main())
