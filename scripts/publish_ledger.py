#!/usr/bin/env python3
"""Commits the rendered run ledger to the `ledger` branch through GitHub's contents API (job B of monitor.yml; v2 slice 7).

Standard library only and self-contained: job B runs it on the runner's system Python with nothing installed, holding
contents: write and no cloud credentials. Uses `gh api` (GH_TOKEN and GITHUB_REPOSITORY from the environment), never
creates the branch, skips unchanged files. Exit 0, or 1 on any error. Design:
notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md (CLIs > publish_ledger.py).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

FILES = ("docs/run-ledger.md", "docs/last-tick.json")


class PublishError(RuntimeError):
    pass


def gh_api(args: list[str], *, run=subprocess.run) -> tuple[int, dict | list | None]:
    """(HTTP status, parsed body) from `gh api --include`; gh exits non-zero on any HTTP error, so the status line decides."""
    done = run(["gh", "api", "--include", *args], capture_output=True, text=True, check=False)
    head, _, body = done.stdout.replace("\r\n", "\n").partition("\n\n")
    first = head.split("\n", 1)[0]
    parts = first.split()
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        raise PublishError(f"gh api {args[0]} gave no HTTP status: {done.stderr.strip() or first!r}")
    status = int(parts[1])
    try:
        parsed = json.loads(body) if body.strip() else None
    except ValueError:
        parsed = None
    return status, parsed


def publish(in_dir: Path, branch: str, repository: str, message: str, *, run=subprocess.run) -> int:
    """Returns how many files were committed."""
    status, _ = gh_api([f"repos/{repository}/branches/{branch}"], run=run)
    if status == 404:
        raise PublishError(f"branch {branch!r} does not exist; create it once by hand (docs/setup.md), never from CI")
    if status != 200:
        raise PublishError(f"reading branch {branch!r} returned HTTP {status}")
    committed = 0
    for path in FILES:
        new = (in_dir / path).read_bytes()
        status, current = gh_api([f"repos/{repository}/contents/{path}?ref={branch}"], run=run)
        sha = None
        if status == 200 and isinstance(current, dict):
            sha = current.get("sha")
            if current.get("encoding") == "base64":
                old = base64.b64decode("".join((current.get("content") or "").split()))
                if old == new:
                    continue
            # encoding "none" (files over 1 MB): treated as changed; the PUT still uses the returned sha
        elif status != 404:
            raise PublishError(f"reading {path} on {branch} returned HTTP {status}")
        payload = {"message": message, "branch": branch, "content": base64.b64encode(new).decode("ascii")}
        if sha:
            payload["sha"] = sha
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
            json.dump(payload, handle)
        try:
            status, _ = gh_api(["-X", "PUT", f"repos/{repository}/contents/{path}", "--input", handle.name], run=run)
        finally:
            os.unlink(handle.name)
        if status not in (200, 201):
            raise PublishError(f"writing {path} to {branch} returned HTTP {status}")
        committed += 1
    return committed


def _repository() -> str:
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository or not os.environ.get("GH_TOKEN"):
        raise PublishError("GITHUB_REPOSITORY and GH_TOKEN must be set")
    return repository


def _summary(line: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def main(argv: list[str] | None = None, *, run=subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--in-dir", type=Path, required=True)
    parser.add_argument("--branch", default="ledger")
    args = parser.parse_args(argv)
    try:
        repository = _repository()
        meta = json.loads((args.in_dir / "meta.json").read_text(encoding="utf-8"))
        message = f"Update run ledger (sim {meta.get('sim_date') or '-'}, run {meta.get('run_id') or '-'})"
        committed = publish(args.in_dir, args.branch, repository, message, run=run)
    except (PublishError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        _summary(f"Ledger: failed: {exc}")
        return 1
    print(f"Ledger: {committed} of {len(FILES)} file(s) committed to `{args.branch}`")
    _summary(f"Ledger: {committed} of {len(FILES)} file(s) committed to `{args.branch}`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
