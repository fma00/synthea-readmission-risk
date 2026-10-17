"""Best-effort facts about the runtime a Big Join ran on (v2 PRD FR14), recorded under `runtime.` keys in the gold metadata's
build_provenance. Dataproc's batch description reports only the runtime's minor version ("3.0"), so the sub-version and the
connector jars must be read inside the job. Which environment variables or Spark conf keys carry the sub-version on runtime 3.0
is not verified: everything matching is recorded, and the population check (ops/population_checks.py, C8) reports what was found.

Stdlib + the SparkSession passed in only (PRD FR3: this module ships in the Spark job's zip). Design:
notes/eg-new-feature/v2-slice6a-main-200k-and-checks-2026-10-14.md (Interfaces > S2).
"""

from __future__ import annotations

import os
import platform
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

RUNTIME_KEY_PREFIX = "runtime."
JAR_DIRS_DEFAULT: tuple[str, ...] = (
    "/usr/local/share/google/dataproc/lib",
    "/usr/lib/spark/jars",
    "/usr/lib/spark/connector",
    "/opt/spark/jars",
)
_ENV_NAME_RE = re.compile(r"DATAPROC_[A-Z0-9_]+")
_SECRET_MARKERS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "CREDENTIAL")
# fullmatch on the basename: the BigQuery connector (spark-bigquery-…, spark-4.0-bigquery-…) and the GCS connector, NOT the
# google-cloud-bigquerystorage / grpc / proto client jars an image also ships
_JAR_RE = re.compile(r"(spark-(?:[0-9.]+-)?bigquery|gcs-connector)[^/]*\.jar")
CLASSPATH_CONF_KEYS = ("spark.jars", "spark.driver.extraClassPath")
MAX_VALUE_CHARS = 200


def collect_runtime_facts(spark, environ: Mapping[str, str], jar_dirs: Sequence[str]) -> dict[str, str]:
    """`runtime.*` -> non-empty str, values cut to MAX_VALUE_CHARS (keys never). Never raises: each step runs on its own, and a
    failed step is named in `runtime.collection_errors` ("<step>: <ExceptionType>", "; "-joined in step order)."""
    facts: dict[str, str] = {}
    errors: list[str] = []
    jars: dict[str, str] = {}

    def python_version() -> None:
        facts["python_version"] = platform.python_version()

    def java_version() -> None:
        value = spark.sparkContext._jvm.java.lang.System.getProperty("java.version")
        if value:
            facts["java_version"] = str(value)

    def env() -> None:
        for name, value in environ.items():
            if _ENV_NAME_RE.fullmatch(name) and not any(marker in name for marker in _SECRET_MARKERS) and value:
                facts[f"env.{name}"] = value

    def conf() -> None:
        for key, value in spark.sparkContext.getConf().getAll():
            if key.startswith("spark.dataproc.") and "version" in key.lower() and value:
                facts[f"conf.{key}"] = value

    def jar_files() -> None:
        dirs = list(jar_dirs)
        if environ.get("SPARK_HOME"):
            dirs.append(str(Path(environ["SPARK_HOME"]) / "jars"))
        for directory in dirs:
            path = Path(directory)
            if not path.is_dir():
                continue
            for entry in sorted(path.iterdir()):
                if entry.is_file() and _JAR_RE.fullmatch(entry.name):
                    jars.setdefault(entry.name, str(path))

    def classpath() -> None:
        entries = str(spark.sparkContext._jvm.java.lang.System.getProperty("java.class.path") or "").split(os.pathsep)
        spark_conf = spark.sparkContext.getConf()
        for key in CLASSPATH_CONF_KEYS:
            entries += re.split(r"[,:]", spark_conf.get(key, "") or "")
        for entry in entries:
            name = entry.rstrip("/").rsplit("/", 1)[-1]
            if _JAR_RE.fullmatch(name):
                jars.setdefault(name, "classpath")

    steps: list[tuple[str, Callable[[], None]]] = [
        ("python_version", python_version),
        ("java_version", java_version),
        ("env", env),
        ("conf", conf),
        ("jars", jar_files),
        ("classpath", classpath),
    ]
    for name, step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 -- best-effort: a collector failure must never fail the one-shot batch
            errors.append(f"{name}: {type(exc).__name__}")

    for name, where in jars.items():
        facts[f"jar.{name}"] = where
    if errors:
        facts["collection_errors"] = "; ".join(errors)
    return {
        f"{RUNTIME_KEY_PREFIX}{key}": value[:MAX_VALUE_CHARS]
        for key, value in facts.items()
        if isinstance(value, str) and value
    }
