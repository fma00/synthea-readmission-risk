"""SparkSession factory. The caller chooses the master and extra config (v2 PRD FR11): local[*] by default (laptop, tests), or
no master at all on Dataproc Serverless, which supplies it. Every read/write call elsewhere in the pipeline stays the same.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass

from pyspark.sql import SparkSession

_TIMEZONE_KEY = "spark.sql.session.timeZone"


@dataclass(frozen=True)
class BuilderSettings:
    master: str | None  # None: the builder gets no .master() call
    conf: dict[str, str]  # applied with builder.config(k, v) in sorted key order; always holds the UTC time zone
    env_defaults: dict[str, str]  # os.environ.setdefault for each


def builder_settings(master: str | None, conf: Mapping[str, str] | None) -> BuilderSettings:
    """Pure. ValueError if `conf` sets the session time zone to anything but UTC.

    Every Synthea timestamp this pipeline reads is UTC ("...Z"-suffixed); the leakage/censoring/window logic depends on exact
    timestamp arithmetic, so the session's timezone is pinned explicitly rather than left to the JVM's local default.

    A pip-installed PySpark starts its Python workers with $PYSPARK_PYTHON, falling back to whatever `python3` is on PATH
    (pyspark/core/context.py; the spark.pyspark.python setting is only read by spark-submit). With pyenv or a venv that can be a
    different minor version from the driver, and every Python UDF/RDD task then fails with PYTHON_VERSION_MISMATCH. So for a
    local master the workers default to the driver's own interpreter (an explicit setting still wins); on Dataproc the runtime
    configures its workers itself."""
    merged = dict(conf or {})
    if merged.get(_TIMEZONE_KEY, "UTC") != "UTC":
        raise ValueError(f"{_TIMEZONE_KEY} must be UTC; got {merged[_TIMEZONE_KEY]!r}")
    merged[_TIMEZONE_KEY] = "UTC"
    env_defaults = {"PYSPARK_PYTHON": sys.executable} if master is not None and master.startswith("local") else {}
    return BuilderSettings(master=master, conf=dict(sorted(merged.items())), env_defaults=env_defaults)


def make_spark_session(
    app_name: str = "big-join", *, master: str | None = "local[*]", conf: Mapping[str, str] | None = None
) -> SparkSession:
    settings = builder_settings(master, conf)
    for key, value in settings.env_defaults.items():
        os.environ.setdefault(key, value)
    builder = SparkSession.builder.appName(app_name)
    if settings.master is not None:
        builder = builder.master(settings.master)
    for key, value in settings.conf.items():
        builder = builder.config(key, value)
    spark = builder.getOrCreate()
    spark.conf.set(_TIMEZONE_KEY, "UTC")  # also when getOrCreate returned an existing session
    return spark
