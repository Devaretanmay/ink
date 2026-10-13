#!/usr/bin/env python3
"""Reject retired product names outside explicit migration/history inputs."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ALLOWLIST = (
    # Migration compatibility: the legacy on-disk database directory name is
    # read once to relocate an existing install, then never written again.
    ".gitignore",
    "python/ink/ink/client.py",
    "python/ink/ink/decision_api.py",
    "python/ink/ink/internal/model/registry.py",
    "python/ink/tests/test_integrated_model.py",
    "python/ink/tests/test_migration_and_failures.py",
    "scripts/check-brand-hygiene.py",
)
RETIRED = re.compile(r"microloop|issuway|multica", re.IGNORECASE)
GENERIC = re.compile(r"multicast", re.IGNORECASE)
BINARY = re.compile(r"\.(?:png|jpe?g|gif|webp|mp4|zip|dmg|pdf|woff2?|ttf|ico|icns)$", re.IGNORECASE)


def allowed(path: str) -> bool:
    return any(path == item or path.startswith(item) for item in ALLOWLIST)


def main() -> int:
    paths = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
        )
        .decode()
        .split("\0")
    )
    failures: list[str] = []
    for raw in filter(None, paths):
        path = Path(raw)
        if allowed(raw) or BINARY.search(raw):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        content = GENERIC.sub("network-group", data.decode("utf-8", errors="replace"))
        if RETIRED.search(raw) or RETIRED.search(content):
            failures.append(raw)
    if failures:
        print("Retired branding outside migration/history allowlist:", file=sys.stderr)
        print("\n".join(f"  {item}" for item in sorted(set(failures))), file=sys.stderr)
        return 1
    print(f"Brand hygiene passed ({len(paths) - 1} tracked/unignored files; allowlist applied).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
