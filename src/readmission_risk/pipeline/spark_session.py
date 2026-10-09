"""SparkSession factory. Local mode only in this slice; the scale-up phase swaps this one
function for Dataproc Serverless session construction without touching any read/write call
elsewhere in the pipeline, per the architecture PRD's environment-agnostic-code-path requirement.
"""

from __future__ import annotations

import os
import sys

from pyspark.sql import SparkSession


def make_spark_session(app_name: str = "big-join") -> SparkSession:
    """Every Synthea timestamp this pipeline reads is UTC ("...Z"-suffixed); the leakage/
    censoring/window logic depends on exact timestamp arithmetic, so the session's timezone is
    pinned explicitly rather than left to the JVM's local default (which would otherwise make
    results depend on which machine ran the job).
    """
    # A pip-installed PySpark starts its Python workers with $PYSPARK_PYTHON, falling back to whatever `python3` is on
    # PATH (pyspark/core/context.py; the spark.pyspark.python setting is only read by spark-submit). With pyenv or a venv
    # that can be a different minor version from the driver, and every Python UDF/RDD task then fails with
    # PYTHON_VERSION_MISMATCH. Default the workers to the driver's own interpreter; an explicit setting still wins.
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    spark = SparkSession.builder.master("local[*]").appName(app_name).getOrCreate()
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    return spark
