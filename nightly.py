#!/usr/bin/env python3
"""nightly.py: the command lives in keytap.cli.nightly."""
import sys

from keytap._venv import ensure_venv

ensure_venv()

from keytap.cli.nightly import main

if __name__ == "__main__":
    sys.exit(main())
