#!/usr/bin/env python3
"""replay.py: the commands live in keytap.cli.replay."""
import sys

from keytap._venv import ensure_venv

ensure_venv()

from keytap.cli.replay import main

if __name__ == "__main__":
    sys.exit(main())
