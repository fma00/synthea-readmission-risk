#!/usr/bin/env python
"""Thin CLI wrapper around readmission_risk.pipeline.big_join.build_big_join.

Deliberately not unit-tested: all real logic lives in big_join.py, which is fully unit tested.
This script is exercised only by the manual/integration verification run described in
notes/eg-new-feature/pyspark-big-join-2026-09-19.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from readmission_risk.pipeline.big_join import BigJoinConfig, build_big_join
from readmission_risk.pipeline.gold_metadata import read_gold_metadata
from readmission_risk.pipeline.spark_session import make_spark_session


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the PySpark Big Join over Synthea CSV output.")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reference-date", required=True)
    parser.add_argument("--lookback-years", type=int, default=1)
    parser.add_argument("--readmission-window-days", type=int, default=30)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = BigJoinConfig(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        reference_date=args.reference_date,
        lookback_years=args.lookback_years,
        readmission_window_days=args.readmission_window_days,
    )

    spark = make_spark_session()
    try:
        try:
            build_big_join(spark, config)
            metadata = read_gold_metadata(config.output_dir)
        except (FileNotFoundError, FileExistsError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

        rate = f"{100 * metadata.n_positive / metadata.n_rows:.2f}%" if metadata.n_rows else "n/a"
        print(f"Big Join succeeded -> {config.output_dir}")
        print(f"Rows: {metadata.n_rows}")
        print(f"Positives: {metadata.n_positive} ({rate})")
        print(
            f"Inpatient stays: {metadata.n_inpatient_stays} (planned: {metadata.n_planned_stays}, "
            f"continuation: {metadata.n_continuation_stays}, non-terminal: {metadata.n_nonterminal_stays})"
        )
        return 0
    finally:
        spark.stop()


if __name__ == "__main__":
    raise SystemExit(main())
