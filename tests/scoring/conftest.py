import os

# MLflow prints an informational "agent hint" line on import; silence it so test output stays readable.
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import mlflow
import pytest


@pytest.fixture(autouse=True)
def _restore_mlflow_global_state():
    """Equivalent of tests/models/conftest.py's guard (duplicated rather than hoisted so slice 3's test infrastructure
    is not edited): acts in TEARDOWN ONLY, restoring the tracking URI that was current before the test and disabling
    autolog. The session's scoring_env fixture (tests/conftest.py) trains through MLflow and the tests still use MLflow as an
    independent oracle, so the global tracking URI is still restored."""
    original_uri = mlflow.get_tracking_uri()
    yield
    mlflow.autolog(disable=True)
    mlflow.set_tracking_uri(original_uri)
