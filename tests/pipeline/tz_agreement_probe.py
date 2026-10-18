"""Run as a SCRIPT by test_big_join.py::test_fingerprint_agreement_under_non_utc_process_tz, in a subprocess whose TZ is not UTC
(not collected by pytest: no test_ prefix). Builds its own SparkSession, writes the agreement fixture to the directory given as
argv[1], and prints one JSON line {"spark": <Spark-side fingerprint>, "python": <pandas-side fingerprint>}. CI runners and
Dataproc run in UTC, so this is the only place a driver- or worker-local-zone dependence of the mapInPandas path could show.
"""

from __future__ import annotations

import json
import sys

import pandas as pd

from readmission_risk.pipeline.big_join import spark_rowhash_fingerprint
from readmission_risk.pipeline.fingerprint import rowhash_fingerprint
from readmission_risk.pipeline.spark_session import make_spark_session
from tests.pipeline.agreement_fixture import build_agreement_frame


def main(out_dir: str) -> None:
    spark = make_spark_session("tz-agreement-probe")
    spark.sparkContext.setLogLevel("ERROR")
    try:
        build_agreement_frame(spark).write.parquet(out_dir)
        spark_fp, _ = spark_rowhash_fingerprint(spark, spark.read.parquet(out_dir))
    finally:
        spark.stop()
    print(json.dumps({"spark": spark_fp, "python": rowhash_fingerprint(pd.read_parquet(out_dir))}))


if __name__ == "__main__":
    main(sys.argv[1])
