#!/usr/bin/env python
"""Generates one pre-registered Synthea population on a spot VM (readmission_risk.ops.generation_vm.launch): refuses unless
prereg.yaml is committed and pushed and matches the code, uploads the pinned artifacts, waits for IAM
propagation if a role or binding changed in the last 8 minutes (audit log), creates the VM from the Terraform template,
waits for _DONE, deletes the VM and proves nothing labelled remains. COSTS MONEY: confirm with the owner first.
`make generate-vm RUN=pilot_20k JDK_TARBALL=<path>`. Exit codes: 0 done, 1 refused, 2 failed, 3 teardown proof failed,
130 interrupted (during the IAM wait: no VM created; see ops/generation_vm.py). See notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md."""

from __future__ import annotations

import argparse
from pathlib import Path

from readmission_risk.ops.generation_vm import (
    DEFAULT_ZONE,
    LaunchContext,
    LaunchOptions,
    launch,
)

REPO = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True, help="a population name from prereg.yaml (pilot_20k)")
    parser.add_argument("--jdk-tarball", type=Path, required=True, help="the Temurin tarball pinned in prereg.yaml")
    parser.add_argument("--jar-path", type=Path, default=Path("tools/synthea/synthea-with-dependencies.jar"))
    parser.add_argument("--zone", default=DEFAULT_ZONE)
    parser.add_argument("--java-max-heap", default="96g")
    parser.add_argument("--allow-main-run", action="store_true", help="required for main_200k (slice 6)")
    parser.add_argument("--dry-run", action="store_true", help="local checks only; print what would run; no gcloud call")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)
    options = LaunchOptions(
        run=args.run, jdk_tarball=args.jdk_tarball, jar_path=args.jar_path, zone=args.zone, java_max_heap=args.java_max_heap,
        allow_main_run=args.allow_main_run, dry_run=args.dry_run, poll_seconds=args.poll_seconds,
    )  # fmt: skip
    return launch(options, LaunchContext(repo_root=REPO))


if __name__ == "__main__":
    raise SystemExit(main())
