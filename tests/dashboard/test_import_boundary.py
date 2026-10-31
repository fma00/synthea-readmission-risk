"""The dashboard's import boundary (PRD FR18, Verification V-B; the tests/scoring/test_pipeline.py pattern). cloudpickle is not
in the sys.modules list: `import pandas` loads it through pyarrow (measured 2026-10-31), so "never unpickles" rests on the
source ban below and the trap in conftest.py."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SOURCES = sorted((_REPO / "src/readmission_risk/dashboard").rglob("*.py"))
_BANNED_MODULES = (
    "mlflow", "pyspark", "sklearn", "xgboost", "readmission_risk.bundles", "readmission_risk.scoring.pipeline",
    "readmission_risk.models",
)  # fmt: skip


def test_dashboard_modules_import_nothing_heavy():
    code = (
        "import importlib, pkgutil, sys\n"
        "import readmission_risk.dashboard as dashboard\n"
        "for m in pkgutil.iter_modules(dashboard.__path__):\n"
        "    importlib.import_module(f'readmission_risk.dashboard.{m.name}')\n"
        "import readmission_risk.warehouse.dashboard_views\n"
        f"banned = {_BANNED_MODULES!r}\n"
        "loaded = sorted(m for m in sys.modules if any(m == b or m.startswith(b + '.') for b in banned))\n"
        "assert not loaded, loaded\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def test_dashboard_source_bans():
    assert len(_SOURCES) >= 2  # __init__.py and queries.py
    patterns = {
        "pickle import": re.compile(r"^\s*(import|from)\s+(cloud)?pickle\b", re.MULTILINE),
        "frame download (would build a BigQuery Storage client unless create_bqstorage_client=False)": re.compile(r"\.to_(dataframe|arrow)\("),
        "file uploader": re.compile(r"file_uploader"),
    }  # fmt: skip
    for label, pattern in patterns.items():
        assert [str(p) for p in _SOURCES if pattern.search(p.read_text())] == [], label
