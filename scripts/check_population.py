#!/usr/bin/env python
"""Pre-training checks C1-C9 on a generated v2 population (v2 slice 6a): `make check-population RUN=<run>`.

LIVE but read-only: downloads the run's raw patients.csv and encounters.csv (and, for main_200k, the pilot's patients.csv) into
--work-dir, downloads the gold, and reads v1's local raw and gold. Uses the operator's own gcloud identity and ADC, no
impersonation. Counts only; never loads or fits a model. Exit 0 = no FAIL, 1 = at least one FAIL, 2 = could not run.
Design: notes/eg-new-feature/v2-slice6a-main-200k-and-checks-2026-10-14.md (Interfaces > S3).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from readmission_risk.ops.gcloud import PROJECT, Runner
from readmission_risk.ops.population_checks import (
    RUNS,
    GatherError,
    evaluate_checks,
    exit_code,
    format_result,
    gather_inputs,
    write_report,
)
from readmission_risk.ops.prereg import PreregError, parse_prereg
from readmission_risk.warehouse.gold_source import materialized_gold_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_NAME = "population_checks.json"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the slice-6a pre-training checks C1-C9 on a v2 population.")
    parser.add_argument("--run", required=True, choices=RUNS)
    parser.add_argument("--v1-raw-dir", default="data/raw/full_run")
    parser.add_argument("--v1-gold-dir", default="data/gold/full_run_2b")
    parser.add_argument("--work-dir", default="data/checks")
    return parser.parse_args(argv)


def _git(run_git: Callable, repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return run_git(["git", "-C", str(repo_root), *args], capture_output=True, check=False)


def _resolve(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def main(
    argv: list[str] | None = None,
    *,
    runner: Runner | None = None,
    gold_opener=materialized_gold_dir,
    run_git: Callable = subprocess.run,
    repo_root: Path = REPO_ROOT,
    environ: Mapping[str, str] = os.environ,
) -> int:
    args = parse_args(argv)

    def refuse(message: str) -> int:
        print(f"ERROR: {message}", file=sys.stderr)
        return 2

    # 1. preconditions: any failure exits 2 without a report
    if environ.get("CLOUDSDK_CORE_PROJECT") != PROJECT:
        return refuse(f"export CLOUDSDK_CORE_PROJECT={PROJECT} first")
    head = _git(run_git, repo_root, "rev-parse", "HEAD")
    if head.returncode != 0:
        return refuse("git rev-parse HEAD failed")
    head_commit = head.stdout.decode().strip() if isinstance(head.stdout, bytes) else str(head.stdout).strip()
    shown = _git(run_git, repo_root, "show", "HEAD:prereg.yaml")
    if shown.returncode != 0:
        return refuse("prereg.yaml is not committed at HEAD")
    head_blob = shown.stdout if isinstance(shown.stdout, bytes) else str(shown.stdout).encode()
    working = repo_root / "prereg.yaml"
    if not working.is_file() or working.read_bytes() != head_blob:
        return refuse("the working-tree prereg.yaml differs from HEAD's: the checks use the committed values; commit or revert it")
    try:
        prereg = parse_prereg(head_blob.decode("utf-8"))
    except (PreregError, UnicodeDecodeError) as exc:
        return refuse(f"prereg.yaml at HEAD is invalid: {exc}")
    status = _git(run_git, repo_root, "status", "--porcelain")
    if status.returncode != 0:
        return refuse("git status failed")
    tree_dirty = bool(status.stdout.strip())
    prereg_sha256 = hashlib.sha256(head_blob).hexdigest()

    def prereg_blob_at(commit: str) -> bytes:
        result = _git(run_git, repo_root, "show", "--end-of-options", f"{commit}:prereg.yaml")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args)
        return result.stdout if isinstance(result.stdout, bytes) else str(result.stdout).encode()

    report_path = _resolve(repo_root, args.work_dir) / args.run / REPORT_NAME
    common = {"run": args.run, "head_commit": head_commit, "tree_dirty": tree_dirty, "prereg_sha256": prereg_sha256}

    def now() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 2-3. gather (exit 2 with a report on failure), evaluate, report
    try:
        inputs = gather_inputs(
            args.run,
            runner=runner,
            gold_opener=gold_opener,
            work_dir=_resolve(repo_root, args.work_dir),
            v1_raw_dir=_resolve(repo_root, args.v1_raw_dir),
            v1_gold_dir=_resolve(repo_root, args.v1_gold_dir),
        )
    except GatherError as exc:
        write_report(report_path, **common, generated_at_utc=now(), inputs=exc.inputs, results=[], error=str(exc))
        print(f"ERROR: {exc}\n  report: {report_path}", file=sys.stderr)
        return 2
    try:
        results = evaluate_checks(inputs, prereg, prereg_blob_at=prereg_blob_at)
    except Exception as exc:  # noqa: BLE001 -- an unexpected evaluation error must read as "could not run" (2), never as a check FAIL (1)
        error = f"evaluation failed: {type(exc).__name__}: {exc}"
        write_report(report_path, **common, generated_at_utc=now(), inputs=inputs.inputs, results=[], error=error)
        print(f"ERROR: {error}\n  report: {report_path}", file=sys.stderr)
        return 2
    for result in results:
        print(format_result(result))
    write_report(report_path, **common, generated_at_utc=now(), inputs=inputs.inputs, results=results, error=None)
    code = exit_code(results)
    print(f"{'PASS' if code == 0 else 'FAIL'}: report {report_path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
