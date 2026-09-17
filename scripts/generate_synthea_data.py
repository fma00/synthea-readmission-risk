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
    SyntheaGenerationConfig,
    SyntheaGenerationError,
    SyntheaValidationError,
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = SyntheaGenerationConfig(
        population_size=args.population,
        seed=args.seed,
        reference_date=args.reference_date,
        output_dir=args.output,
        synthea_jar_path=args.jar_path,
        state=args.state,
        timeout_seconds=args.timeout_seconds,
    )

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
