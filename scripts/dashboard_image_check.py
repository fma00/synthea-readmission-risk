"""Checks the dashboard image from inside (v3 slice 4, Interfaces §3 in
notes/eg-new-feature/v3-slice4-cloud-run-deploy-2026-11-03.md): `docker run --rm IMAGE python -I scripts/dashboard_image_check.py`.
It proves the image holds exactly the hash-locked dashboard set (no pyspark/mlflow/sklearn: PRD FR18 at the package level), runs
as non-root on Python 3.14, and imports the app together with every module the app imports lazily at run time (the google
tree), so a module missing from the lockfile fails the build rather than the first live render. Standard library only; one line
per check, exit 0 iff all pass. `--allow-missing-commit` (only `make dashboard-image-check` of a dirty working tree passes it)
accepts an image that records no commit.
"""

from __future__ import annotations

import importlib.metadata
import os
import re
import subprocess
import sys
from pathlib import Path

LOCKFILE = Path("requirements-dashboard.txt")
# A pin line of a pip-compile lockfile; extras are stripped (google-api-core[grpc]==...), as tests/test_lockfiles.py's PIN_RE.
PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^\s;\\]+)")
BANNED = ("pyspark", "mlflow", "scikit-learn", "xgboost", "cloudpickle", "scipy", "matplotlib", "typer", "google-cloud-storage")
BANNED_MODULES = ("pyspark", "mlflow", "sklearn", "xgboost", "cloudpickle")
# Modules the dashboard imports inside functions (gcp.credentials/bigquery_client, queries, errors); tests keep this complete.
LAZY_IMPORTS = (
    "google.auth",
    "google.auth.impersonated_credentials",
    "google.auth.exceptions",
    "google.oauth2.credentials",
    "google.cloud.bigquery",
    "google.cloud.bigquery.retry",
    "google.api_core.exceptions",
)
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_pins(text: str) -> dict[str, str]:
    """Canonical name -> version for every pin line."""
    return {canonical(m.group(1)): m.group(2) for line in text.split("\n") if (m := PIN_RE.match(line))}


def compare(installed: dict[str, str], pins: dict[str, str], *, ignore: tuple[str, ...] = ("pip",)) -> list[str]:
    """Every difference between the installed distributions and the lockfile's pins, as messages; empty when equal."""
    have = {k: v for k, v in installed.items() if k not in ignore}
    problems = [f"missing {name}=={pins[name]}" for name in sorted(pins.keys() - have.keys())]
    problems += [f"extra {name}=={have[name]}" for name in sorted(have.keys() - pins.keys())]
    problems += [
        f"{name}: installed {have[name]}, pinned {pins[name]}"
        for name in sorted(have.keys() & pins.keys())
        if have[name] != pins[name]
    ]
    return problems


def _installed() -> dict[str, str]:
    return {canonical(d.metadata["Name"]): d.version for d in importlib.metadata.distributions()}


def _child_import_code() -> str:
    imports = "; ".join(f"import {m}" for m in ("readmission_risk.dashboard.app", *LAZY_IMPORTS))
    return f"import json, sys; {imports}; print(json.dumps(sorted(m for m in {BANNED_MODULES!r} if m in sys.modules)))"


def commit_ok(commit: str, *, allow_missing: bool) -> bool:
    """A full 40-hex commit; empty only when allowed (`make dashboard-image-check` of a dirty tree records no commit)."""
    return bool(COMMIT_RE.fullmatch(commit)) or (allow_missing and commit == "")


def checks(*, allow_missing_commit: bool = False) -> list[tuple[str, bool, str]]:
    results = []
    results.append(("IC1", sys.version_info[:2] == (3, 14), f"python {sys.version.split()[0]}"))
    results.append(("IC2", os.getuid() != 0, f"uid {os.getuid()}"))
    installed = _installed()
    problems = compare(installed, lock_pins(LOCKFILE.read_text()))
    results.append(("IC3", not problems, "; ".join(problems) or f"{len(installed) - 1} distributions = the lockfile"))
    present = sorted(name for name in BANNED if name in installed)
    results.append(("IC4", not present, f"banned installed: {present}" if present else "none of the banned packages"))
    pip = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True, check=False)
    results.append(("IC5", pip.returncode == 0, (pip.stdout or pip.stderr).strip()[:300]))
    # Not -I: the child must see PYTHONPATH=/app/src.
    child = subprocess.run([sys.executable, "-c", _child_import_code()], capture_output=True, text=True, check=False)
    ok = child.returncode == 0 and child.stdout.strip() == "[]"
    if ok:
        detail = f"app + {len(LAZY_IMPORTS)} lazy modules import; banned modules loaded: []"
    else:
        stderr_tail = child.stderr.strip().splitlines()[-1:]
        detail = (child.stdout.strip() or (stderr_tail[0] if stderr_tail else "no output"))[:300]
    results.append(("IC6", ok, detail))
    commit = os.environ.get("READMISSION_RISK_GIT_COMMIT", "")
    has_config = Path(".streamlit/config.toml").is_file()
    ok = has_config and commit_ok(commit, allow_missing=allow_missing_commit)
    results.append(("IC7", ok, f"config.toml {has_config}; commit {commit!r}"))
    return results


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in ([], ["--allow-missing-commit"]):
        print("usage: dashboard_image_check.py [--allow-missing-commit]", file=sys.stderr)
        return 2
    results = checks(allow_missing_commit=bool(args))
    for name, ok, detail in results:
        print(f"{name} {'pass' if ok else 'fail'} {detail}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
