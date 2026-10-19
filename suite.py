#!/usr/bin/env python3
"""suite.py: the commands live in keytap.cli.suite."""
import sys

from keytap._venv import ensure_venv

ensure_venv()

from keytap.cli.suite import main

if __name__ == "__main__":
    sys.exit(main())
