#!/usr/bin/env python
"""Builds the Spark job's --py-files zip (readmission_risk.ops.spark_zip) into dist/ and prints its path and sha256.
`make spark-zip`. The generation launcher and the Big Join submitter build it themselves; this is for inspection."""

from __future__ import annotations

import argparse
from pathlib import Path

from readmission_risk.ops.spark_zip import build_spark_zip

REPO = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO / "dist")
    args = parser.parse_args(argv)
    path, digest = build_spark_zip(REPO, args.out)
    print(f"{path}\nsha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
