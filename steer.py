#!/usr/bin/env python3
"""steer.py: the commands live in keytap.cli.steer."""
import sys

from keytap._venv import ensure_venv

ensure_venv()

from keytap.cli.steer import main

if __name__ == "__main__":
    sys.exit(main())
