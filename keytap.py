#!/usr/bin/env python3
from keytap._venv import ensure_venv

ensure_venv()

from keytap.ui.app import main

if __name__ == "__main__":
    main()
