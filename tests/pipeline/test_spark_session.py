from __future__ import annotations

import sys

import pytest

from readmission_risk.pipeline.spark_session import builder_settings


def test_python_workers_run_the_drivers_python_version(spark):
    """Regression: without an explicit PYSPARK_PYTHON the workers used `python3` from PATH, which under pyenv was a
    different minor version from the driver (PYTHON_VERSION_MISMATCH on every Python task)."""
    worker = spark.sparkContext.parallelize([0], 1).map(lambda _: tuple(__import__("sys").version_info[:2])).collect()
    assert worker == [tuple(sys.version_info[:2])]


# v2 slice 5 (FR11): the caller chooses master and config; builder_settings is pure, so no second SparkContext starts here.


def test_builder_settings_local_default():
    settings = builder_settings("local[*]", None)
    assert settings.master == "local[*]"
    assert settings.conf == {"spark.sql.session.timeZone": "UTC"}
    assert settings.env_defaults == {"PYSPARK_PYTHON": sys.executable}


def test_builder_settings_master_none():
    settings = builder_settings(None, None)
    assert settings.master is None
    assert settings.env_defaults == {}


def test_conf_cannot_override_utc():
    with pytest.raises(ValueError, match="must be UTC"):
        builder_settings("local[*]", {"spark.sql.session.timeZone": "Europe/Zurich"})
    assert builder_settings("local[*]", {"spark.sql.session.timeZone": "UTC"}).conf == {"spark.sql.session.timeZone": "UTC"}


def test_conf_entries_passed_through_sorted():
    settings = builder_settings(None, {"spark.sql.shuffle.partitions": "8", "spark.app.x": "1"})
    assert list(settings.conf) == ["spark.app.x", "spark.sql.session.timeZone", "spark.sql.shuffle.partitions"]


def test_session_fixture_time_zone_is_utc(spark):
    assert spark.conf.get("spark.sql.session.timeZone") == "UTC"
