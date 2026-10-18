#!/usr/bin/env python
"""Thin CLI wrapper around readmission_risk.pipeline.big_join.build_big_join.

All real logic lives in big_join.py, which is fully unit tested; tests/pipeline/test_big_join.py also drives main() through the
gs:// code path on file:// URIs. --input-dir and --output-dir accept a local path or a gs:// URI (v2 slice 5, FR11); on Dataproc
Serverless the batch passes --spark-master external (the runtime supplies the master) and --provenance KEY=VALUE pairs recorded
as the gold metadata's build_provenance. See notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md.
--record-runtime (passed only by the Dataproc submitter, v2 slice 6a, FR14) adds best-effort `runtime.*` facts about the runtime
to build_provenance: notes/eg-new-feature/v2-slice6a-main-200k-and-checks-2026-10-14.md.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from collections.abc import Callable

from readmission_risk.pipeline.big_join import BigJoinConfig, build_big_join
from readmission_risk.pipeline.generation import parse_provenance_args
from readmission_risk.pipeline.gold_metadata import (
    GOLD_METADATA_FILENAME,
    parse_gold_metadata,
)
from readmission_risk.pipeline.runtime_env import (
    JAR_DIRS_DEFAULT,
    RUNTIME_KEY_PREFIX,
    collect_runtime_facts,
)
from readmission_risk.pipeline.spark_session import make_spark_session
from readmission_risk.pipeline.storage import storage_for

EXTERNAL_MASTER = "external"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the PySpark Big Join over Synthea CSV output.")
    parser.add_argument("--input-dir", required=True, help="local path or gs:// URI")
    parser.add_argument("--output-dir", required=True, help="local path or gs:// URI; must not exist")
    parser.add_argument("--reference-date", required=True)
    parser.add_argument("--lookback-years", type=int, default=1)
    parser.add_argument("--readmission-window-days", type=int, default=30)
    parser.add_argument(
        "--spark-master",
        default="local[*]",
        help=f"Spark master URL; '{EXTERNAL_MASTER}' sets none (Dataproc Serverless supplies it)",
    )
    parser.add_argument("--provenance", action="append", metavar="KEY=VALUE", help="recorded as build_provenance (repeatable)")
    parser.add_argument(
        "--record-runtime",
        action="store_true",
        help=f"also record best-effort facts about the runtime as {RUNTIME_KEY_PREFIX}* build_provenance keys (Dataproc)",
    )
    args = parser.parse_args(argv)
    try:
        args.provenance = parse_provenance_args(args.provenance)
    except ValueError as exc:
        parser.error(str(exc))
    reserved = sorted(key for key in args.provenance if key.startswith(RUNTIME_KEY_PREFIX))
    if reserved:
        parser.error(f"--provenance keys starting with {RUNTIME_KEY_PREFIX!r} are reserved for --record-runtime: {reserved}")
    return args


def with_runtime_facts(config: BigJoinConfig, spark, enabled: bool, collect: Callable | None = None) -> BigJoinConfig:
    """`config` unchanged when not enabled; otherwise a copy whose provenance also holds the runtime facts. `collect` defaults
    to the module's collect_runtime_facts, looked up at call time (tests replace it)."""
    if not enabled:
        return config
    facts = (collect or collect_runtime_facts)(spark, os.environ, JAR_DIRS_DEFAULT)
    return dataclasses.replace(config, provenance={**(config.provenance or {}), **facts})


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = BigJoinConfig(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            reference_date=args.reference_date,
            lookback_years=args.lookback_years,
            readmission_window_days=args.readmission_window_days,
            provenance=args.provenance,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    master = None if args.spark_master == EXTERNAL_MASTER else args.spark_master
    spark = make_spark_session(master=master)
    try:
        try:
            config = with_runtime_facts(config, spark, args.record_runtime)
            build_big_join(spark, config)
            storage, output_dir = storage_for(config.output_dir, spark)
            metadata_path = storage.join(output_dir, GOLD_METADATA_FILENAME)
            metadata = parse_gold_metadata(storage.read_text(metadata_path), metadata_path)
        except (FileNotFoundError, FileExistsError, ValueError, OSError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

        rate = f"{100 * metadata.n_positive / metadata.n_rows:.2f}%" if metadata.n_rows else "n/a"
        print(f"Big Join succeeded -> {output_dir}")
        print(f"Rows: {metadata.n_rows}")
        print(f"Positives: {metadata.n_positive} ({rate})")
        print(
            f"Inpatient stays: {metadata.n_inpatient_stays} (planned: {metadata.n_planned_stays}, "
            f"continuation: {metadata.n_continuation_stays}, non-terminal: {metadata.n_nonterminal_stays})"
        )
        print(f"Fingerprint: {metadata.gold_fingerprint} ({metadata.gold_fingerprint_definition})")
        return 0
    finally:
        spark.stop()


if __name__ == "__main__":
    raise SystemExit(main())
