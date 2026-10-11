import pytest

from readmission_risk.pipeline.spark_session import make_spark_session


@pytest.fixture(scope="session")
def spark():
    session = make_spark_session("test")
    yield session
    session.stop()


@pytest.fixture(scope="session")
def scoring_env(tmp_path_factory):
    """Trains both models ONCE per session and exports both runs as bundles (see tests/scoring/helpers.build_scoring_env).
    Shared by tests/scoring and tests/models (the bundle exporter's tests). Every import is inside the body: CI's
    pipeline-dataproc-runtime job loads this file under requirements-dataproc-3.0.txt, which has no mlflow or sklearn.
    The tracking URI is restored right after building, so function-scoped guards never capture a leaked URI."""
    import mlflow

    from tests.scoring.helpers import build_scoring_env

    original_uri = mlflow.get_tracking_uri()
    env = build_scoring_env(tmp_path_factory.mktemp("scoring-env"))
    mlflow.set_tracking_uri(original_uri)
    yield env
    mlflow.set_tracking_uri(original_uri)
