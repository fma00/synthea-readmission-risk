#!/usr/bin/env python
"""The promotion gate (v2 PRD FR15) and frozen-v1 external validation (FR16): `make gate RUN=<run> GATE_ARGS=...`.

LIVE but read-only: downloads the run's gold from GCS (the operator's own ADC), reads the local MLflow store, the local champion
bundle copy, the challenger bundle and the local v1 gold. Exit 0 = PROMOTE, 3 = REFUSE, 2 = could not run. Writes
data/gate/<run>/gate_report.json + gate_table.md (negative_control_report.json + negative_control_table.md with
--negative-control). Through `make`, any non-zero exit shows as "make: *** [gate] Error <n>"; judge the outcome from the report.
Design: notes/eg-new-feature/v2-slice6b-promotion-gate-frozen-v1-2026-10-16.md (Interfaces > S3; live runbook).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from readmission_risk.ops import gate_run as gr
from readmission_risk.ops.gcloud import PROJECT
from readmission_risk.ops.prereg import PreregError, parse_prereg
from readmission_risk.warehouse.gold_source import materialized_gold_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
EXIT_PROMOTE, EXIT_REFUSE, EXIT_ERROR = 0, 3, 2


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the slice-6b promotion gate and the frozen-v1 external validation.")
    parser.add_argument("--run", required=True, choices=(gr.PILOT_RUN, "main_200k"))
    parser.add_argument("--challenger-experiment", required=True)
    parser.add_argument("--challenger-bundle", required=True, help="local bundles/<bundle_id> of the challenger LR run")
    parser.add_argument("--rehearsal", action="store_true", help=f"pipeline sanity on {gr.PILOT_RUN}; ignores the analysis lock")
    parser.add_argument("--negative-control", action="store_true", help="replace the challenger with the train prevalence")
    parser.add_argument("--tracking-dir", default="mlflow")
    parser.add_argument("--out-dir", default="data/gate")
    return parser.parse_args(argv)


def _git(run_git: Callable, repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return run_git(["git", "-C", str(repo_root), *args], capture_output=True, check=False)


def _out(value) -> bytes:
    return value if isinstance(value, bytes) else str(value or "").encode("utf-8")


def _write_json(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, allow_nan=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main(
    argv: list[str] | None = None,
    *,
    run_gate: Callable = gr.run_gate,
    run_git: Callable = subprocess.run,
    repo_root: Path = REPO_ROOT,
    environ: Mapping[str, str] = os.environ,
    gold_opener=materialized_gold_dir,
) -> int:
    args = parse_args(argv)

    def refuse(message: str) -> int:
        print(f"ERROR: {message}", file=sys.stderr)
        return EXIT_ERROR

    # preconditions: any failure exits 2 without a report
    if args.rehearsal != (args.run == gr.PILOT_RUN):
        return refuse(f"--rehearsal goes with --run {gr.PILOT_RUN} and only with it; a real gate runs on main_200k")
    if environ.get("CLOUDSDK_CORE_PROJECT") != PROJECT:
        return refuse(f"export CLOUDSDK_CORE_PROJECT={PROJECT} first")
    head = _git(run_git, repo_root, "rev-parse", "HEAD")
    if head.returncode != 0:
        return refuse("git rev-parse HEAD failed")
    head_commit = _out(head.stdout).decode().strip()
    shown = _git(run_git, repo_root, "show", "HEAD:prereg.yaml")
    if shown.returncode != 0:
        return refuse("prereg.yaml is not committed at HEAD")
    head_blob = _out(shown.stdout)
    working = repo_root / "prereg.yaml"
    if not working.is_file() or working.read_bytes() != head_blob:
        return refuse("the working-tree prereg.yaml differs from HEAD's: the gate uses the committed values")
    try:
        prereg = parse_prereg(head_blob.decode("utf-8"))
    except (PreregError, UnicodeDecodeError) as exc:
        return refuse(f"prereg.yaml at HEAD is invalid: {exc}")
    if not args.rehearsal:
        status = _git(run_git, repo_root, "status", "--porcelain")
        if status.returncode != 0 or _out(status.stdout).strip():
            return refuse("the working tree is not clean (untracked files count): the gate runs only from committed code")

    stem = "negative_control" if args.negative_control else "gate"
    out = Path(args.out_dir) if Path(args.out_dir).is_absolute() else repo_root / args.out_dir
    report_path, table_path = out / args.run / f"{stem}_report.json", out / args.run / f"{stem}_table.md"
    if report_path.is_file():
        try:
            previous = json.loads(report_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}  # unreadable or not an object: treated as successful, so it is refused (fails closed)
        if previous.get("error") is None and not previous.get("rehearsal", False):
            return refuse(f"{report_path} already holds a successful result; it is never overwritten (delete it deliberately)")

    mode = "negative_control" if args.negative_control else "main"
    common = {
        "head_commit": head_commit,
        "prereg_sha256": hashlib.sha256(head_blob).hexdigest(),
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    config = gr.GateRunConfig(
        run=args.run,
        challenger_experiment=args.challenger_experiment,
        challenger_bundle=Path(args.challenger_bundle),
        repo_root=repo_root,
        tracking_dir=Path(args.tracking_dir),
        rehearsal=args.rehearsal,
        negative_control=args.negative_control,
    )
    try:
        body = run_gate(config, prereg=prereg, run_git=run_git, gold_opener=gold_opener)
    except Exception as exc:  # noqa: BLE001 -- any failure after the preconditions is "could not run" (2), never REFUSE
        error = {"run": args.run, "mode": mode, "rehearsal": args.rehearsal, **common, "error": f"{type(exc).__name__}: {exc}"}
        _write_json(report_path, error)
        table_path.unlink(missing_ok=True)
        print(f"ERROR: {error['error']}\n  report: {report_path}", file=sys.stderr)
        return EXIT_ERROR

    report = {**body, **common, "error": None}
    _write_json(report_path, report)
    table = gr.render_gate_table(report, ci_level=prereg.bootstrap["ci_level"], frozen_models=list(prereg.frozen_v1["models"]))
    table_path.write_text(table, encoding="utf-8")
    print(table, end="")
    print(f"report: {report_path}")
    return EXIT_PROMOTE if report["gate"]["decision"] == "PROMOTE" else EXIT_REFUSE


if __name__ == "__main__":
    raise SystemExit(main())
