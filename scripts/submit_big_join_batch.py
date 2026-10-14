#!/usr/bin/env python
"""Runs the Big Join for one pre-registered population on Dataproc Serverless runtime 3.0
(readmission_risk.ops.dataproc_batch.submit): refuses unless prereg.yaml is committed and pushed, the raw run is complete and
matches the prereg, and no generation VM is up; waits for IAM propagation if a role or binding changed in the last 8 minutes (audit log); submits with maxExecutors=7; waits; records wall-clock and DCU-hours under
data/pilot/<run>/. COSTS MONEY: confirm with the owner first. `make bigjoin-batch RUN=pilot_20k`. Exit codes: 0 succeeded,
1 refused, 2 failed or monitoring failed (the batch may still be running: see the message), 130 interrupted (see
ops/dataproc_batch.py). See notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md."""

from __future__ import annotations

import argparse
from pathlib import Path

from readmission_risk.ops.dataproc_batch import SubmitOptions, submit
from readmission_risk.ops.generation_vm import LaunchContext

REPO = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True, help="a population name from prereg.yaml (pilot_20k)")
    parser.add_argument("--allow-main-run", action="store_true", help="required for main_200k (slice 6)")
    parser.add_argument("--dry-run", action="store_true", help="local checks only; print the submit command; no gcloud call")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)
    options = SubmitOptions(run=args.run, allow_main_run=args.allow_main_run, dry_run=args.dry_run, poll_seconds=args.poll_seconds)
    return submit(options, LaunchContext(repo_root=REPO))


if __name__ == "__main__":
    raise SystemExit(main())
