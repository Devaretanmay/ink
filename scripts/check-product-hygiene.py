#!/usr/bin/env python3
"""Reject retired product positioning outside explicit history/legacy."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Files where old-product references are acceptable (historical/legacy)
ALLOWLIST = (
    "docs/legacy/",
    "docs/evidence/",
    "CHANGELOG.md",
    "scripts/check-product-hygiene.py",
    "scripts/check-brand-hygiene.py",
    ".gitignore",
    "python/ink/ink/decision_api.py",  # _migrate_legacy_default_database
)

# Patterns for the retired product identity
RETIRED_POSITIONING = re.compile(
    r"loop.detect(?:ion|or)|trajectory.monitor|progress.detect(?:ion|or)|"
    r"run progress|autonomous agent progress|still advancing|stuck agent|"
    r"agent reliability(?:\s+monitor)",
    re.IGNORECASE,
)

# Patterns for incorrect install command (bare "pip install ink" without -jit)
BAD_INSTALL = re.compile(r"pip install ink(?!\-)", re.IGNORECASE)
BAD_INSTALL_ALLOWLIST = (
    "CHANGELOG.md",
    "docs/legacy/",
    "scripts/check-product-hygiene.py",
    ".github/workflows/ci.yml",
)

BINARY = re.compile(
    r"\.(?:png|jpe?g|gif|webp|mp4|zip|dmg|pdf|woff2?|ttf|ico|icns|db|so|dylib)$",
    re.IGNORECASE,
)

# Internal commercial files that should not be in the public repo
INTERNAL_FILES = (
    "outbound_leads.csv",
    "outbound_messages.md",
    "pricing_notes.md",
    "top_20_outbound.md",
    "opportunity_report_template.md",
    "pilot_report_template.md",
    "design_partners.csv",
    "design_partner_playbook.md",
    "COMPANY_READINESS.md",
)


def allowed(path: str, allowlist: tuple) -> bool:
    return any(path == item or path.startswith(item) for item in allowlist)


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
        # Check for internal commercial files
        basename = Path(raw).name
        if basename in INTERNAL_FILES:
            failures.append(f"INTERNAL FILE in public repo: {raw}")
            continue

        if BINARY.search(raw):
            continue

        try:
            data = Path(raw).read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        content = data.decode("utf-8", errors="replace")

        # Check for retired positioning
        if not allowed(raw, ALLOWLIST):
            for m in RETIRED_POSITIONING.finditer(content):
                failures.append(f"RETIRED POSITIONING in {raw}: '{m.group()}'")

        # Check for bad install command
        if not allowed(raw, BAD_INSTALL_ALLOWLIST):
            for m in BAD_INSTALL.finditer(content):
                # Allow "pip install ink-jit" but not bare "pip install ink"
                pos = m.end()
                if pos < len(content) and content[pos:pos+1] not in (" ", "\n", "\r", '"', "'", "`", ")", "]", ""):
                    continue
                failures.append(f"BAD INSTALL CMD in {raw}: '{m.group()}'")

    if failures:
        print("Product hygiene failures:", file=sys.stderr)
        print("\n".join(f"  {item}" for item in sorted(set(failures))), file=sys.stderr)
        return 1
    print(f"Product hygiene passed ({len(paths) - 1} files checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
