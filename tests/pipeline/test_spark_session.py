from __future__ import annotations

import sys


def test_python_workers_run_the_drivers_python_version(spark):
    """Regression: without an explicit PYSPARK_PYTHON the workers used `python3` from PATH, which under pyenv was a
    different minor version from the driver (PYTHON_VERSION_MISMATCH on every Python task)."""
    worker = spark.sparkContext.parallelize([0], 1).map(lambda _: tuple(__import__("sys").version_info[:2])).collect()
    assert worker == [tuple(sys.version_info[:2])]
