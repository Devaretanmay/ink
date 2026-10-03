"""Microloop module execution entry point (python -m microloop)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
