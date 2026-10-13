#!/usr/bin/env python3
"""Reject internal files, absolute local paths, and stale artifacts in the public repo.

The public Ink repository should contain only things that help a user install,
evaluate, trust, or contribute to Ink. This check fails the build if internal
material, machine-specific paths, or build/state artifacts are tracked (or about
to be tracked).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Paths that must never be tracked: internal operations, sales, legacy, or build output.
FORBIDDEN_PREFIXES = (
    "pilots/",
    "integrations/",
    "launch-film/",
    "doodle-launch-film/",
    "agent/",
    "runtime/",
    "examples/demo/",
    "docs/evidence/",
    "docs/legacy/",
    "docs/branding/",
    "dist/",
    "build/",
    ".freebuff/",
)

# Individual files that must never be tracked.
FORBIDDEN_FILES = (
    ".DS_Store",
    "RECORDING_SCRIPT.md",
    "COMPANY_READINESS.md",
)

# Content that reveals an internal machine or private checkout.
ABSOLUTE_PATH = re.compile(
    r"/Users/[^/\s\"']+|/home/[^/\s\"']+|[A-Za-z]:\\\\Users\\\\",
)
# Files that legitimately contain these patterns (the check itself).
ABSOLUTE_PATH_ALLOWLIST = ("scripts/check-repo-hygiene.py",)

# Artifacts that must never be tracked.
FORBIDDEN_SUFFIXES = (".pyc", ".whl", ".db")

BINARY_SUFFIX = re.compile(
    r"\.(?:png|jpe?g|gif|webp|mp4|zip|dmg|pdf|woff2?|ttf|ico|icns|db|so|dylib|whl|pyc)$",
    re.IGNORECASE,
)


def tracked_paths() -> list[str]:
    out = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    )
    return [p for p in out.decode().split("\0") if p]


def main() -> int:
    paths = tracked_paths()
    failures: list[str] = []

    for raw in paths:
        for prefix in FORBIDDEN_PREFIXES:
            if raw.startswith(prefix):
                failures.append(f"FORBIDDEN PATH tracked: {raw}")
                break

        if Path(raw).name in FORBIDDEN_FILES:
            failures.append(f"FORBIDDEN FILE tracked: {raw}")

        if "__pycache__/" in raw or raw.endswith(FORBIDDEN_SUFFIXES):
            failures.append(f"BUILD/STATE ARTIFACT tracked: {raw}")

        if BINARY_SUFFIX.search(raw):
            continue
        if raw in ABSOLUTE_PATH_ALLOWLIST:
            continue
        try:
            data = Path(raw).read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        content = data.decode("utf-8", errors="replace")
        match = ABSOLUTE_PATH.search(content)
        if match:
            failures.append(f"ABSOLUTE LOCAL PATH in {raw}: '{match.group()}'")

    if failures:
        print("Repo hygiene failures:", file=sys.stderr)
        print("\n".join(f"  {item}" for item in sorted(set(failures))), file=sys.stderr)
        return 1
    print(f"Repo hygiene passed ({len(paths)} files checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
