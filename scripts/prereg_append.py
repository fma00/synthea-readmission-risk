#!/usr/bin/env python
"""Append the slice-6b blocks to prereg.yaml, append-only (Phase A untouched): `make prereg-lock` / `make prereg-phase-b`.

  lock     the analysis lock, BEFORE any model is trained on main_200k (refuses if any run in the local MLflow store, deleted
           or not, already used main_200k's gold, or the locked experiment exists).
  phase-b  Phase B from the two gate reports, AFTER the gate; copies the reports to notes/eg-new-feature/v2-slice6b-reports/.

Neither command commits. Design: notes/eg-new-feature/v2-slice6b-promotion-gate-frozen-v1-2026-10-16.md (Interfaces > S4).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

from readmission_risk.ops.prereg import (
    PHASE_B_REPORT_PATHS,
    PreregError,
    check_prereg_against_code,
    parse_prereg,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_200K_FINGERPRINT = "80edd13dcc967d69b6fa4cfa17b4d7fa40188f8e7e17b2031b833332a59c63f3"  # 6a Results, L5 C1
LOCK_HEADER = "\n# Slice 6b analysis lock (scripts/prereg_append.py); Phase A above is unchanged.\n"
PHASE_B_HEADER = "\n# Slice 6b Phase B: results against the rules above (scripts/prereg_append.py).\n"


class AppendError(RuntimeError):
    pass


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append the analysis lock or Phase B to prereg.yaml (append-only).")
    sub = parser.add_subparsers(dest="command", required=True)
    lock = sub.add_parser("lock")
    lock.add_argument("--experiment-name", required=True)
    lock.add_argument("--challenger-model", default="logistic_regression")
    lock.add_argument("--tracking-dir", default="mlflow")
    lock.add_argument("--gold-fingerprint", default=MAIN_200K_FINGERPRINT)
    phase_b = sub.add_parser("phase-b")
    phase_b.add_argument("--report", default="data/gate/main_200k/gate_report.json")
    phase_b.add_argument("--negative-control-report", default="data/gate/main_200k/negative_control_report.json")
    return parser.parse_args(argv)


def _git(run_git: Callable, repo_root: Path, *args: str) -> str:
    result = run_git(["git", "-C", str(repo_root), *args], capture_output=True, check=False)
    if result.returncode != 0:
        raise AppendError(f"git {' '.join(args)} failed")
    out = result.stdout
    return out.decode("utf-8") if isinstance(out, bytes) else str(out or "")


def _require_clean_tree(run_git: Callable, repo_root: Path) -> None:
    if _git(run_git, repo_root, "status", "--porcelain").strip():
        raise AppendError("the working tree is not clean (untracked files count)")


def _resolve(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def _validate(old: bytes, addition: str) -> bytes:
    new = old + addition.encode("utf-8")
    try:
        parse_prereg(new.decode("utf-8"))
    except PreregError as exc:
        raise AppendError(f"the appended prereg.yaml would not parse: {exc}") from exc
    return new


def _replace(prereg_path: Path, old: bytes, new: bytes) -> None:
    """Atomic replace, only if the file still holds exactly `old` (the bytes the new file extends)."""
    if prereg_path.read_bytes() != old or not new.startswith(old):
        raise AppendError("prereg.yaml changed while appending; nothing written")
    tmp = prereg_path.with_name(prereg_path.name + ".tmp")
    try:
        tmp.write_bytes(new)
        os.replace(tmp, prereg_path)
    except OSError:
        tmp.unlink(missing_ok=True)  # an untracked leftover would make every retry refuse "tree not clean"
        raise


def _lock_store_checks(tracking_dir: Path, experiment: str, fingerprint: str) -> None:
    if not (tracking_dir / "mlflow.db").is_file():
        return  # no store: no experiment and no run exists yet
    from mlflow.tracking import MlflowClient

    from readmission_risk.models.tracking import mlflow_tracking_uri
    from readmission_risk.ops.gate_run import runs_on_gold

    if MlflowClient(tracking_uri=mlflow_tracking_uri(tracking_dir)).get_experiment_by_name(experiment) is not None:
        raise AppendError(f"experiment {experiment!r} already exists (any stage): the lock must come before training")
    used = runs_on_gold(tracking_dir, fingerprint)
    if used:
        names = sorted({u["experiment"] for u in used})
        raise AppendError(f"{len(used)} run(s) in {names} were already trained on gold {fingerprint[:12]}… (deleted runs count)")


def cmd_lock(args, repo_root: Path, run_git: Callable, today: date) -> None:
    prereg_path = repo_root / "prereg.yaml"
    old = prereg_path.read_bytes()
    prereg = parse_prereg(old.decode("utf-8"))
    if prereg.analysis_lock is not None:
        raise AppendError("prereg.yaml already has an analysis_lock")
    check_prereg_against_code(prereg)
    _lock_store_checks(_resolve(repo_root, args.tracking_dir), args.experiment_name, args.gold_fingerprint)
    code_commit = _git(run_git, repo_root, "rev-parse", "HEAD").strip()
    block = {
        "locked_on": today.isoformat(),
        "challenger_model": args.challenger_model,
        "experiment_name": args.experiment_name,
        "frozen_v1_rows": "modeling_rows",
        "negative_control": "constant_train_prevalence",
        "code_commit": code_commit,
    }
    new = _validate(old, LOCK_HEADER + yaml.safe_dump({"analysis_lock": block}, sort_keys=False, default_flow_style=False))
    _replace(prereg_path, old, new)
    print(f"analysis_lock appended (code_commit {code_commit}). Next: commit prereg.yaml alone, open a PR into v2, merge.")


def _load_report(path: Path) -> dict:
    if not path.is_file():
        raise AppendError(f"no report at {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise AppendError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AppendError(f"{path} is not a JSON object report")
    return data


def _check_reports(main: dict, negative: dict, lock: dict) -> None:
    expectations = [
        (main, {"run": "main_200k", "mode": "main", "rehearsal": False, "error": None}, "main report"),
        (negative, {"run": "main_200k", "mode": "negative_control", "rehearsal": False, "error": None}, "negative-control report"),
    ]
    for report, wanted, who in expectations:
        for key, value in wanted.items():
            if report.get(key) != value:
                raise AppendError(f"{who}: {key} is {report.get(key)!r}, expected {value!r}")
        if report.get("analysis_lock") != lock:
            raise AppendError(f"{who}: its analysis_lock differs from prereg.yaml's")
    if negative["gate"]["decision"] != "REFUSE":
        raise AppendError(f"the negative control was not refused (decision {negative['gate']['decision']!r})")
    for key in ("head_commit", "gold_fingerprint"):
        if main.get(key) != negative.get(key):
            raise AppendError(f"the two reports differ in {key}")
    for key in ("run_id", "bundle_id", "git_commit"):
        if main["challenger"][key] != negative["challenger"][key]:
            raise AppendError(f"the two reports name different challengers ({key})")


def build_phase_b(main: dict, report_hashes: dict[str, str], today: date) -> dict:
    gate, frozen = main["gate"], main["frozen_v1"]
    return {
        "recorded_on": today.isoformat(),
        "reports": {name: {"path": PHASE_B_REPORT_PATHS[name], "sha256": report_hashes[name]} for name in PHASE_B_REPORT_PATHS},
        "decision": gate["decision"],
        "challenger": {k: main["challenger"][k] for k in ("run_id", "bundle_id", "git_commit")},
        "champion": {"run_id": main["champion"]["run_id"], "bundle_id": main["champion"]["bundle_id"]},
        "rules": [{k: r[k] for k in ("name", "value", "threshold", "passed")} for r in gate["rules"]],
        "lookup_still_wins": gate["lookup_still_wins"],
        "negative_control_decision": "REFUSE",
        "frozen_v1": {
            name: {
                **{m: [entry["point"][m], *entry["intervals"][m]] for m in ("roc_auc", "brier_score", "observed_expected_ratio")},
                "accepted": entry["accepted"],
            }
            for name, entry in frozen["models"].items()
        },
        "power": {k: main["power"][k] for k in ("n_test", "n_test_positive", "min_test_positives")},
    }


def cmd_phase_b(args, repo_root: Path, run_git: Callable, today: date) -> None:
    prereg_path = repo_root / "prereg.yaml"
    old = prereg_path.read_bytes()
    prereg = parse_prereg(old.decode("utf-8"))
    if prereg.analysis_lock is None:
        raise AppendError("prereg.yaml has no analysis_lock")
    if prereg.phase_b is not None:
        raise AppendError("prereg.yaml already has phase_b")
    sources = {"gate": _resolve(repo_root, args.report), "negative_control": _resolve(repo_root, args.negative_control_report)}
    main, negative = _load_report(sources["gate"]), _load_report(sources["negative_control"])
    try:
        _check_reports(main, negative, prereg.analysis_lock)
        block = build_phase_b(main, {name: "0" * 64 for name in sources}, today)  # shape check before any write
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise AppendError(f"a gate report is incomplete or malformed ({type(exc).__name__}: {exc})") from exc
    report_bytes = {name: source.read_bytes() for name, source in sources.items()}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in report_bytes.items()}
    block = build_phase_b(main, hashes, today)
    new = _validate(old, PHASE_B_HEADER + yaml.safe_dump({"phase_b": block}, sort_keys=False, default_flow_style=False))
    targets = {name: repo_root / PHASE_B_REPORT_PATHS[name] for name in report_bytes}
    try:
        for name, data in report_bytes.items():
            targets[name].parent.mkdir(parents=True, exist_ok=True)
            targets[name].write_bytes(data)
        _replace(prereg_path, old, new)
    except (AppendError, OSError):
        for target in targets.values():
            target.unlink(missing_ok=True)
        raise
    print(f"phase_b appended (decision {block['decision']}); reports copied to {Path(PHASE_B_REPORT_PATHS['gate']).parent}/. "
          "Next: commit prereg.yaml and the two reports, open a PR into v2.")


def main(argv: list[str] | None = None, *, repo_root: Path = REPO_ROOT, run_git: Callable = subprocess.run,
         today: date | None = None) -> int:  # fmt: skip
    args = parse_args(argv)
    today = today or datetime.now(UTC).date()
    try:
        _require_clean_tree(run_git, repo_root)
        (cmd_lock if args.command == "lock" else cmd_phase_b)(args, repo_root, run_git, today)
    except (AppendError, PreregError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
