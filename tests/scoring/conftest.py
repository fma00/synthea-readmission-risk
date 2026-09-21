import os

# MLflow prints an informational "agent hint" line on import; silence it so test output stays readable.
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import mlflow
import pytest


@pytest.fixture(autouse=True)
def _restore_mlflow_global_state():
    """Equivalent of tests/models/conftest.py's guard (duplicated rather than hoisted so slice 3's test infrastructure
    is not edited): acts in TEARDOWN ONLY, restoring the tracking URI that was current before the test and disabling
    autolog. The scorer's resolve_run_id / validate_run / load_logged_model set the global tracking URI."""
    original_uri = mlflow.get_tracking_uri()
    yield
    mlflow.autolog(disable=True)
    mlflow.set_tracking_uri(original_uri)


@pytest.fixture(scope="session")
def scoring_env(tmp_path_factory):
    """Trains both models ONCE for the whole tests/scoring package (see helpers.build_scoring_env). The tracking URI is
    restored right after training and again at teardown, so the function-scoped autouse fixture never captures the leaked
    URI as the "original"."""
    from tests.scoring.helpers import build_scoring_env

    original_uri = mlflow.get_tracking_uri()
    env = build_scoring_env(tmp_path_factory.mktemp("scoring-env"))
    mlflow.set_tracking_uri(original_uri)
    yield env
    mlflow.set_tracking_uri(original_uri)
