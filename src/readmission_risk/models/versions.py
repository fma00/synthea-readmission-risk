"""The Python-version format shared by training (the `python_version` run tag), the bundle exporter and the scorer's
guard 14, plus the library-version map guard 8 checks. Stdlib only (re, sys, importlib.metadata), so the scorer can use it
without importing tracking.py (which imports mlflow); v2 slice 6b's promotion gate applies the same two rules outside the
scorer. See notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md and
notes/eg-new-feature/v2-slice6b-promotion-gate-frozen-v1-2026-10-16.md.
"""

from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError, version

_PYTHON_VERSION_RE = re.compile(r"([0-9]+)\.([0-9]+)(?:\.([0-9]+))?")  # [0-9], not \d: \d also matches non-ASCII digits
# run/manifest tag -> installed distribution; guard 8 (scorer) and the promotion gate compare these
LIBRARY_VERSION_DISTRIBUTIONS = {
    "sklearn_version": "scikit-learn",
    "xgboost_version": "xgboost",
    "mlflow_version": "mlflow",
    "pandas_version": "pandas",
    "numpy_version": "numpy",
}


def current_python_version() -> str:
    """'X.Y.Z' of the running interpreter -- sys.version_info's major, minor and micro, the same format MLflow writes into
    a logged model's MLmodel (flavors.python_function.python_version). The single definition of the format: training
    records it as the run tag `python_version` and the scorer's guard 14 compares against it."""
    v = sys.version_info
    return f"{v.major}.{v.minor}.{v.micro}"


def installed_versions() -> dict[str, str]:
    """The five library versions guard 8 checks, read from installed distribution metadata (never imports mlflow)."""
    result = {}
    for key, distribution in LIBRARY_VERSION_DISTRIBUTIONS.items():
        try:
            result[key] = version(distribution)
        except PackageNotFoundError:
            result[key] = "not installed"
    return result


def parse_python_major_minor(value: object) -> tuple[int, int] | None:
    """(major, minor) of an 'X.Y' or 'X.Y.Z' string (re.fullmatch, ASCII digits 0-9 only); None for anything else,
    including non-strings (a hand-edited MLmodel can hold a YAML float such as 3.12)."""
    if not isinstance(value, str):
        return None
    match = _PYTHON_VERSION_RE.fullmatch(value)
    return (int(match.group(1)), int(match.group(2))) if match else None
