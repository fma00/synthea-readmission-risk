import pytest

from readmission_risk.pipeline.spark_session import make_spark_session

# One JVM serves the whole test session. Its default 1 GiB driver heap ran out once in CI (run 38253244020, a Big Join
# test after 316 pipeline tests). Measured 2026-10-12: ~200 MiB stays live after GC, but the heap fills to ~900 MiB
# between collections, so the failure was a transient peak, not a leak (notes/eg-new-feature/ci-hygiene-lockfiles-2026-10-12.md).
# spark.driver.memory only takes effect if this fixture is the first code in the pytest process to start the JVM:
# a test that calls make_spark_session() itself before this fixture runs would start a 1 GiB JVM that every later
# session reuses.
TEST_SPARK_CONF = {"spark.driver.memory": "2g"}


@pytest.fixture(scope="session")
def spark():
    session = make_spark_session("test", conf=TEST_SPARK_CONF)
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


@pytest.fixture(scope="session")
def portable_env(scoring_env, tmp_path_factory):
    """Exports both scoring_env bundles ONCE per session as schema-2 bundles with their portable form (v2 slice 6c,
    notes/eg-new-feature/v2-slice6c-portable-v1-export-2026-10-17.md). Imports inside the body, like scoring_env."""
    from types import SimpleNamespace

    from readmission_risk.portable.export import export_portable_bundle

    root = tmp_path_factory.mktemp("portable-env") / "bundles"
    lr = export_portable_bundle(scoring_env.lr_bundle_uri, scoring_env.gold_dir, root)
    xgb = export_portable_bundle(scoring_env.xgb_bundle_uri, scoring_env.gold_dir, root)
    return SimpleNamespace(
        bundles_root=root,
        lr_portable_uri=lr.bundle_uri,
        xgb_portable_uri=xgb.bundle_uri,
        lr_result=lr,
        xgb_result=xgb,
    )


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
