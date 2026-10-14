"""Every live GCP action of the slice-5 operations CLIs is a `gcloud` subprocess, built as an argv list by pure functions and
run through a Runner, so tests substitute a fake (tests/conftest.py makes the real one raise). Stdlib plus
readmission_risk.gcp only: conftest imports this module in every environment, CI's Dataproc-runtime job included (enforced by
tests/ops/test_gcloud.py). See notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md, Interfaces section 11.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime

from readmission_risk.gcp import PROJECT_ID as PROJECT

Runner = Callable[[list[str]], subprocess.CompletedProcess]
GCLOUD_ENV = {
    "CLOUDSDK_CORE_PROJECT": PROJECT,
    # parallel composite uploads create component objects and then DELETE them; the create-only writers lack objects.delete
    "CLOUDSDK_STORAGE_PARALLEL_COMPOSITE_UPLOAD_ENABLED": "False",
}
_NO_MATCH = "matched no objects"


class GcloudError(RuntimeError):
    def __init__(self, argv: list[str], result: subprocess.CompletedProcess) -> None:
        tail = "\n".join((result.stderr or "").strip().splitlines()[-15:])
        super().__init__(f"{' '.join(argv[:4])} ... exited {result.returncode}:\n{tail}")
        self.returncode = result.returncode


def default_runner(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=False, capture_output=True, text=True, env={**os.environ, **GCLOUD_ENV})


def resolve_runner(run: Runner | None) -> Runner:
    """`run`, or this module's default_runner looked up NOW (so tests/conftest.py's monkeypatch of default_runner applies)."""
    return run if run is not None else sys.modules[__name__].default_runner


def run_ok(run: Runner | None, argv: list[str]) -> str:
    result = resolve_runner(run)(argv)
    if result.returncode != 0:
        raise GcloudError(argv, result)
    return result.stdout


def run_json(run: Runner | None, argv: list[str]) -> object:
    text = run_ok(run, [*argv, "--format=json"])
    return json.loads(text) if text.strip() else None


def parse_gcloud_time(text: str | None) -> datetime | None:
    """gcloud's RFC 3339 timestamps ("...Z", up to nanoseconds; Python 3.11+ fromisoformat truncates to microseconds)."""
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def list_objects(run: Runner | None, prefix_uri: str) -> list[str]:
    """Every object URL under `prefix_uri` (recursively). [] if none; any other failure raises GcloudError."""
    argv = ["gcloud", "storage", "ls", "--recursive", prefix_uri.rstrip("/") + "/**"]
    result = resolve_runner(run)(argv)
    if result.returncode == 0:
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode == 1 and _NO_MATCH in (result.stderr or ""):
        return []
    raise GcloudError(argv, result)


def object_exists(run: Runner | None, uri: str) -> bool:
    argv = ["gcloud", "storage", "ls", uri]
    result = resolve_runner(run)(argv)
    if result.returncode == 0:
        return True
    if result.returncode == 1 and _NO_MATCH in (result.stderr or ""):
        return False
    raise GcloudError(argv, result)


GENERATION_LABEL = ("purpose", "synthea-generation")


def list_labelled(run: Runner | None, kind: str) -> list[dict]:
    """Compute instances or disks (any zone) labelled purpose=synthea-generation."""
    if kind not in ("instances", "disks"):
        raise ValueError(f"kind must be 'instances' or 'disks'; got {kind!r}")
    key, value = GENERATION_LABEL
    found = run_json(run, ["gcloud", "compute", kind, "list", f"--filter=labels.{key}={value}"])
    return list(found or [])
