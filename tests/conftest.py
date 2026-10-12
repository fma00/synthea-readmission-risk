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


@pytest.fixture(autouse=True)
def _no_real_gcp(request, monkeypatch):
    """Tests never reach Google Cloud (v2 slice 4): the credential and client factories raise unless a test replaces them
    with a fake. A test marked real_gcp_module (only tests/test_gcp.py) keeps the real functions and fakes google.auth
    itself. Patched by dotted string, so this file imports no google package (CI's Dataproc-runtime job has none)."""
    if request.node.get_closest_marker("real_gcp_module"):
        return

    def refuse(*_args, **_kwargs):
        raise RuntimeError("tests must not call GCP: replace readmission_risk.gcp's client factories with a fake")

    for name in ("credentials", "storage_client", "bigquery_client"):
        monkeypatch.setattr(f"readmission_risk.gcp.{name}", refuse)

    def refuse_gcloud(argv, *_args, **_kwargs):
        raise RuntimeError(f"tests must not run gcloud ({' '.join(argv[:3])} ...): pass a fake Runner")

    # v2 slice 5: the operations CLIs' gcloud subprocesses (ops/gcloud.py is stdlib + readmission_risk.gcp, so this import is
    # safe in CI's Dataproc-runtime job too)
    monkeypatch.setattr("readmission_risk.ops.gcloud.default_runner", refuse_gcloud)
