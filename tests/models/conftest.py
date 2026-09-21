import os

# MLflow prints an informational "agent hint" line on import; silence it so test output stays readable.
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import pytest

import mlflow


@pytest.fixture(autouse=True)
def _restore_mlflow_global_state():
    """Belt-and-braces guard, acting in TEARDOWN ONLY (after the test body has run): restores the tracking URI
    that was current before the test and disables autolog. Because it acts only after the body, assertions made
    inside a test observe the production code's own cleanup (its `finally`), never this fixture's."""
    original_uri = mlflow.get_tracking_uri()
    yield
    mlflow.autolog(disable=True)
    mlflow.set_tracking_uri(original_uri)
