"""Ink module execution entry point (python -m ink)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
