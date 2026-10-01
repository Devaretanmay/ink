"""Microloop CLI entry point."""

from __future__ import annotations

import sys

from .decision_cli import main as decision_main


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv in (["--help"], ["-h"]):
        print(
            "Microloop — verified fast paths for repeated agent decisions.\n\n"
            "Commands: discover TRACES, sites, inspect SITE, compile SITE, evaluate SITE,\n"
            "          maintenance, export, retain, value, model-install, model-train.\n"
            "          Use COMMAND --help for options."
        )
        return 0
    if argv in (["--version"], ["-V"]):
        from . import __version__  # Deferred to avoid loading the Rust extension for --help.

        print(f"microloop {__version__}")
        return 0
    return decision_main(argv)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
