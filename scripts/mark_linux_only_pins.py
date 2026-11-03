"""Appends `; platform_system == "Linux"` to the named pins of a pip-compile lockfile (v3 slice 4, F10 in
notes/eg-new-feature/v3-slice4-cloud-run-deploy-2026-11-03.md). `make lock-dashboard` runs it inside the Linux lock container
right after pip-compile: a Linux compile writes watchdog unmarked, and watchdog 6.0.0 has no macOS wheel for Python 3.14, so an
unmarked pin would make `make setup` on macOS build it from source. Standard library only.

Usage: python scripts/mark_linux_only_pins.py requirements-dashboard.txt watchdog [name ...]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MARKER = '; platform_system == "Linux"'


def mark(text: str, names: list[str]) -> str:
    """Each named pin line (`name==version \\`) gains MARKER before its continuation backslash. ValueError if a name has no
    pin line; an already-marked line is left as it is (idempotent)."""
    lines = text.split("\n")
    for name in names:
        pattern = re.compile(rf"^({re.escape(name)}==\S+)( {re.escape(MARKER)})? \\$", re.IGNORECASE)
        hits = [i for i, line in enumerate(lines) if pattern.match(line)]
        if len(hits) != 1:
            raise ValueError(f"{name}: expected exactly one pin line, found {len(hits)}")
        lines[hits[0]] = f"{pattern.match(lines[hits[0]]).group(1)} {MARKER} \\"
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    path = Path(argv[0])
    try:
        path.write_text(mark(path.read_text(), argv[1:]))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
