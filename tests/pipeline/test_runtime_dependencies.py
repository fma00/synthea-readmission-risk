"""The Spark job's dependencies stay within what Dataproc Serverless runtime 3.0 bundles (v2 PRD FR3).

Three kinds of check (design: notes/eg-new-feature/v2-slice1-tooling-and-dataproc-runtime-leg-2026-10-11.md):
- Imports: every file that ships with the Spark job imports only the stdlib, readmission_risk.pipeline, and the
  runtime-bundled packages in RUNTIME_BUNDLED_IMPORTS_ALLOWED (an AST walk, so nested imports count too).
- Lockfile: requirements-dataproc-3.0.txt, the hash-locked test mirror of the runtime, pins those packages at the
  runtime's versions and the same pytest as requirements-dev.txt.
- Environment: when READMISSION_RISK_EXPECT_DATAPROC_RUNTIME=3.0 (set only by CI's pipeline-dataproc-runtime job), the
  running interpreter, libraries and JVM really are the runtime 3.0 stack.

The import and lockfile checks read files only, so they run in every environment.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

# PRD FR3: the Spark job may import only the stdlib, readmission_risk.pipeline, and libraries Dataproc runtime 3.0
# bundles. Keys are dotted import prefixes (matched as `m == key or m.startswith(key + ".")`), values the distribution
# name that must be hash-pinned in requirements-dataproc-3.0.txt, so the pipeline-dataproc-runtime CI job tests it.
# Widening: confirm the package is on the runtime-3.0 page, add it here AND to requirements-dataproc-3.0.in, recompile
# (e.g. a future {"google.cloud.storage": "google-cloud-storage"}; never a bare "google").
RUNTIME_BUNDLED_IMPORTS_ALLOWED: dict[str, str] = {
    "pyspark": "pyspark",
    "pandas": "pandas",
    "numpy": "numpy",
    "pyarrow": "pyarrow",
    # v2 slice 5: pipeline/storage.py catches py4j.protocol.Py4JJavaError from Hadoop FileSystem calls. py4j is PySpark's own hard
    # dependency (every PySpark install ships it, Dataproc's included) and is already hash-pinned in both lockfiles.
    "py4j": "py4j",
}
# The code that ships to Dataproc: the package root (executed on import), the whole pipeline subpackage (recursive),
# and its CLI entry point.
SPARK_JOB_SOURCES = [
    SRC / "readmission_risk" / "__init__.py",
    *sorted((SRC / "readmission_risk" / "pipeline").rglob("*.py")),
    ROOT / "scripts" / "run_big_join.py",
]
# Runtime 3.0.14 per the page cited in requirements-dataproc-3.0.in, checked 2026-10-11.
RUNTIME_3_0 = {"python": (3, 12), "java_major": 21, "pyspark": "4.0.1", "pandas": "2.3", "pyarrow": "19.0", "numpy": "2.1"}
LOCKFILE = ROOT / "requirements-dataproc-3.0.txt"
DEV_LOCKFILE = ROOT / "requirements-dev.txt"
PIPELINE = ("readmission_risk", "pipeline")
EXPECT_RUNTIME_ENV = "READMISSION_RISK_EXPECT_DATAPROC_RUNTIME"


def source_package(path: Path) -> tuple[str, ...] | None:
    """The package a file under src/ belongs to: src/readmission_risk/pipeline/big_join.py -> ("readmission_risk",
    "pipeline"); src/readmission_risk/__init__.py -> ("readmission_risk",). None for a file outside src/ (scripts)."""
    try:
        return path.relative_to(SRC).with_suffix("").parts[:-1]
    except ValueError:
        return None


def _absolute_import_allowed(module: str) -> bool:
    if module.split(".")[0] in sys.stdlib_module_names:
        return True
    if any(module == prefix or module.startswith(prefix + ".") for prefix in RUNTIME_BUNDLED_IMPORTS_ALLOWED):
        return True
    return module == "readmission_risk.pipeline" or module.startswith("readmission_risk.pipeline.")


def _relative_import_allowed(node: ast.ImportFrom, package: tuple[str, ...] | None) -> bool:
    if package is None or node.level - 1 > len(package) - 1:
        return False
    base = package[: len(package) - (node.level - 1)]
    target = base + (tuple(node.module.split(".")) if node.module else ())
    return target[:2] == PIPELINE


def forbidden_imports(source: str, *, filename: str, package: tuple[str, ...] | None) -> list[str]:
    """Return 'filename:lineno: <module as written>' for every import the Spark job may not ship with."""
    found = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            found += [f"{filename}:{node.lineno}: {a.name}" for a in node.names if not _absolute_import_allowed(a.name)]
        elif isinstance(node, ast.ImportFrom):
            allowed = _relative_import_allowed(node, package) if node.level else _absolute_import_allowed(node.module)
            if not allowed:
                found.append(f"{filename}:{node.lineno}: {'.' * node.level}{node.module or ''}")
    return found


def _pins(path: Path) -> dict[str, str]:
    """`name==version` lines of a pip-compile lockfile, by the name as written."""
    return dict(re.findall(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)", path.read_text(), flags=re.MULTILINE))


def test_spark_job_imports_only_stdlib_the_pipeline_and_runtime_bundled_packages():
    assert all(path.is_file() for path in SPARK_JOB_SOURCES), SPARK_JOB_SOURCES
    offenders = [
        hit
        for path in SPARK_JOB_SOURCES
        for hit in forbidden_imports(path.read_text(), filename=str(path.relative_to(ROOT)), package=source_package(path))
    ]
    assert offenders == [], "the Spark job may only import what Dataproc runtime 3.0 bundles (PRD FR3):\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    ("source", "package"),
    [
        ("import mlflow", PIPELINE),
        ("from sklearn.base import X", PIPELINE),
        ("def f():\n    import typer", PIPELINE),
        ("import google.cloud.storage", PIPELINE),
        ("from readmission_risk.models import split", PIPELINE),
        ("import readmission_risk.scoring", PIPELINE),
        ("from readmission_risk import pipeline", PIPELINE),
        ("from ..models import split", PIPELINE),
        ("from .schemas import X", None),
        ("from . import x", ("readmission_risk",)),
    ],
)
def test_forbidden_imports_flags_what_cannot_ship(source, package):
    assert len(forbidden_imports(source, filename="f.py", package=package)) == 1


@pytest.mark.parametrize(
    ("source", "package"),
    [
        ("import json", PIPELINE),
        ("from __future__ import annotations", PIPELINE),
        ("import pyspark.sql.functions as F", PIPELINE),
        ("from pyspark.sql import Window", PIPELINE),
        ("import pandas as pd", PIPELINE),
        ("from readmission_risk.pipeline.schemas import TABLE_SCHEMAS", PIPELINE),
        ("import readmission_risk.pipeline.big_join", PIPELINE),
        ("from .schemas import X", PIPELINE),
        ("from . import schemas", PIPELINE),
        ("from ..x import y", (*PIPELINE, "sub")),
    ],
)
def test_forbidden_imports_allows_what_can_ship(source, package):
    assert forbidden_imports(source, filename="f.py", package=package) == []


def test_source_package_maps_files_to_their_package():
    assert source_package(SRC / "readmission_risk" / "pipeline" / "big_join.py") == PIPELINE
    assert source_package(SRC / "readmission_risk" / "__init__.py") == ("readmission_risk",)
    assert source_package(ROOT / "scripts" / "run_big_join.py") is None


def test_every_allowed_runtime_import_is_hash_pinned_in_the_runtime_lockfile():
    lines = LOCKFILE.read_text().split("\n")
    for dist in RUNTIME_BUNDLED_IMPORTS_ALLOWED.values():
        at = next((i for i, line in enumerate(lines) if line.startswith(f"{dist}==")), None)
        assert at is not None, f"{dist} is not pinned in {LOCKFILE.name}"
        assert lines[at + 1].strip().startswith("--hash=sha256:"), f"{dist} is not hash-pinned in {LOCKFILE.name}"


def test_runtime_lockfile_mirrors_runtime_3_0():
    text = LOCKFILE.read_text()
    assert "autogenerated by pip-compile with Python 3.12" in text
    pins = _pins(LOCKFILE)
    assert pins["pyspark"] == RUNTIME_3_0["pyspark"]
    for dist in ("pandas", "pyarrow", "numpy"):
        assert pins[dist].startswith(RUNTIME_3_0[dist] + "."), (dist, pins[dist])
    assert {"mlflow", "scikit-learn", "xgboost"} & pins.keys() == set()


def test_runtime_lockfile_pins_the_dev_lockfiles_pytest():
    assert _pins(LOCKFILE)["pytest"] == _pins(DEV_LOCKFILE)["pytest"]


def test_environment_is_dataproc_runtime_3_0_when_requested(spark):
    expected = os.environ.get(EXPECT_RUNTIME_ENV, "")
    if not expected:
        pytest.skip(f"{EXPECT_RUNTIME_ENV} is not set (only CI's pipeline-dataproc-runtime job sets it)")
    if expected != "3.0":
        pytest.fail(f"{EXPECT_RUNTIME_ENV}={expected!r}: only '3.0' is supported")
    import numpy
    import pandas
    import pyarrow
    import pyspark

    mismatches = []
    if sys.version_info[:2] != RUNTIME_3_0["python"]:
        mismatches.append(f"python {sys.version_info[:2]} != {RUNTIME_3_0['python']}")
    if pyspark.__version__ != RUNTIME_3_0["pyspark"]:
        mismatches.append(f"pyspark {pyspark.__version__} != {RUNTIME_3_0['pyspark']}")
    for module in (pandas, pyarrow, numpy):
        if not module.__version__.startswith(RUNTIME_3_0[module.__name__] + "."):
            mismatches.append(f"{module.__name__} {module.__version__} is not {RUNTIME_3_0[module.__name__]}.x")
    # _jvm is a private PySpark API; it works in classic local mode, which is what the session fixture creates.
    java_version = spark.sparkContext._jvm.java.lang.System.getProperty("java.version")
    if int(java_version.split(".")[0]) != RUNTIME_3_0["java_major"]:
        mismatches.append(f"java {java_version} is not {RUNTIME_3_0['java_major']}")
    assert mismatches == []
