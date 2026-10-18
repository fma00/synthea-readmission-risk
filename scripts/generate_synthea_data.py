#!/usr/bin/env python
"""Thin CLI wrapper around readmission_risk.pipeline.generation.run_synthea_generation.

Deliberately not unit-tested: all real logic lives in generation.py, which is fully unit
tested. This script is exercised only by the manual/integration verification run described in
notes/eg-new-feature/local-synthea-generation-2026-09-16.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from readmission_risk.pipeline.generation import (
    EXPECTED_CSV_TABLES,
    SyntheaGenerationConfig,
    SyntheaGenerationError,
    SyntheaValidationError,
    parse_provenance_args,
    run_synthea_generation,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a local Synthea patient population.")
    parser.add_argument("--population", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-date", default="20260916")
    parser.add_argument(
        "--jar-path", type=Path, default=Path("tools/synthea/synthea-with-dependencies.jar")
    )
    parser.add_argument("--state", default="Massachusetts")
    parser.add_argument("--timeout-seconds", type=float, default=None)
    parser.add_argument("--years-of-history", type=int, default=10)
    # v2 slice 5 (the generation VM passes all of these; see notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md)
    parser.add_argument("--clinician-seed", type=int, default=None, help="Synthea -cs")
    parser.add_argument("--end-date", default=None, help="Synthea -e (YYYYMMDD, after --reference-date)")
    parser.add_argument("--jvm-timezone", default=None, help="-Duser.timezone for the JVM (e.g. UTC)")
    parser.add_argument("--included-files", default=None, help="comma-separated CSV names, or 'default' for the 10 joined tables")
    parser.add_argument("--java-max-heap", default=None, help="-Xmx value, e.g. 96g")
    parser.add_argument("--require-alive-population", action="store_true")
    parser.add_argument("--provenance", action="append", metavar="KEY=VALUE", help="written into generation_summary.json")
    args = parser.parse_args(argv)
    try:
        args.provenance = parse_provenance_args(args.provenance) if args.provenance is not None else None
    except ValueError as exc:
        parser.error(str(exc))
    if args.included_files == "default":
        args.included_files = EXPECTED_CSV_TABLES
    elif args.included_files is not None:
        args.included_files = tuple(name.strip() for name in args.included_files.split(","))
    return args


def _config(args: argparse.Namespace) -> SyntheaGenerationConfig:
    return SyntheaGenerationConfig(
        population_size=args.population,
        seed=args.seed,
        reference_date=args.reference_date,
        output_dir=args.output,
        synthea_jar_path=args.jar_path,
        state=args.state,
        timeout_seconds=args.timeout_seconds,
        years_of_history=args.years_of_history,
        clinician_seed=args.clinician_seed,
        end_date=args.end_date,
        jvm_timezone=args.jvm_timezone,
        included_files=args.included_files,
        java_max_heap=args.java_max_heap,
        require_alive_population=args.require_alive_population,
        provenance=args.provenance,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = _config(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        result = run_synthea_generation(config)
    except (FileExistsError, SyntheaGenerationError, SyntheaValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Generation succeeded in {result.duration_seconds:.1f}s -> {result.output_dir}")
    print(f"Row counts: {result.row_counts}")
    if result.warnings:
        print(f"WARNING: these tables came back empty: {result.warnings}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
