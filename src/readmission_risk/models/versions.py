"""The Python-version format shared by training (the `python_version` run tag), the bundle exporter and the scorer's
guard 14. Imports only sys, so the scorer can use it without importing tracking.py (which imports mlflow). See
notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md.
"""

from __future__ import annotations

import sys


def current_python_version() -> str:
    """'X.Y.Z' of the running interpreter -- sys.version_info's major, minor and micro, the same format MLflow writes into
    a logged model's MLmodel (flavors.python_function.python_version). The single definition of the format: training
    records it as the run tag `python_version` and the scorer's guard 14 compares against it."""
    v = sys.version_info
    return f"{v.major}.{v.minor}.{v.micro}"
