"""Ink CLI entry point."""

from __future__ import annotations

import sys

from . import __version__
from .decision_cli import main as decision_main


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv in (["--help"], ["-h"]):
        print(
            "Ink — turns proven, verified decisions into local Fast Paths.\n\n"
            "Start by finding repeated behavior: ink discover TRACES.jsonl\n"
            "Commands: discover TRACES, sites, inspect SITE, compile SITE, evaluate SITE,\n"
            "          maintenance, export, retain, value, model-install, model-train.\n"
            "          Use COMMAND --help for options."
        )
        return 0
    if argv in (["--version"], ["-V"]):
        print(f"ink {__version__}")
        return 0
    return decision_main(argv)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
