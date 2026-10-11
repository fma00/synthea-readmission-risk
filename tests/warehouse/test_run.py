"""score_to_bigquery end to end against the real scorer (the session scoring_env, local gold) and a fake BigQuery client:
call order, statuses, publish gating and resolution, and that every attempt is recorded (FR21)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from google.api_core import exceptions as api
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from requests.exceptions import JSONDecodeError

from readmission_risk.warehouse import run as run_module
from readmission_risk.warehouse.data_tests import assertion_descriptions
from readmission_risk.warehouse.run import BigQueryRunRequest, score_to_bigquery
from tests.scoring.helpers import DEFAULT_RUN_DATE, DEFAULT_WINDOW_DAYS
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob, assertion_error

PRED04 = assertion_descriptions("predictions")["PRED-04"]
RUNS03 = assertion_descriptions("scoring_runs")["RUNS-03"]


def _kind(sql: str) -> str:
    if sql.startswith("BEGIN TRANSACTION"):
        return "publish"
    if sql.startswith("INSERT INTO `synthea-readmission-risk.serving.scoring_runs`"):
        return "record"
    if "DECLARE c DEFAULT" in sql:
        return "history" if "serving.scoring_runs" in sql else "predictions"
    if sql.startswith("SELECT COUNT(*) AS n"):
        return "landed"
    raise AssertionError(f"unexpected query: {sql[:80]}")


def scripted(**outcomes) -> FakeBigQuery:
    """outcomes[kind] = an exception raised by .result(), or a FakeJob; default: success."""
    fake = FakeBigQuery()

    def on_query(sql, config, job_id):
        outcome = outcomes.get(_kind(sql))
        if isinstance(outcome, BaseException):
            return FakeJob(raise_on_result=outcome)
        return outcome or FakeJob()

    fake.on_query = on_query
    return fake


def _request(env, **overrides) -> BigQueryRunRequest:
    fields = {
        "gold_uri": str(env.gold_dir),
        "run_date": DEFAULT_RUN_DATE,
        "bundle_uri": env.lr_bundle_uri,
        "window_days": DEFAULT_WINDOW_DAYS,
    }
    return BigQueryRunRequest(**{**fields, **overrides})


def _query_kinds(fake: FakeBigQuery) -> list[str]:
    return [(_kind(c[1]) if c[0] == "query" else c[0]) for c in fake.calls]


def _record_values(fake: FakeBigQuery) -> dict:
    [config] = [c[2] for c in fake.calls if c[0] == "query" and _kind(c[1]) == "record"]
    return {p.name: p.value for p in config.query_parameters}


@pytest.fixture(autouse=True)
def _fast_resolution(monkeypatch):
    monkeypatch.setattr("readmission_risk.warehouse.staging.time.sleep", lambda s: None)


def test_succeeded_records_one_row_in_order(scoring_env):
    fake = scripted()
    result = score_to_bigquery(_request(scoring_env), client_factory=lambda: fake)
    assert result.error is None and result.recorded
    assert _query_kinds(fake) == ["history", "create_table", "load", "predictions", "publish", "delete_table", "record"]
    row = _record_values(fake)
    assert row["status"] == "succeeded" and row["published"] is True
    assert row["history_tests_passed"] is True and row["predictions_tests_passed"] is True
    assert row["model_bundle_id"] == result.scoring.bundle.bundle_id and row["window_days"] == DEFAULT_WINDOW_DAYS
    assert row["n_predictions"] == len(result.scoring.predictions) > 0
    assert row["n_top_n"] == int(result.scoring.predictions["is_top_n"].sum())
    assert row["trigger"] == "laptop" and row["github_run_url"] is None
    assert row["finished_at"] >= row["started_at"]
    assert str(result.scoring.partition_path).startswith("bigquery://synthea-readmission-risk.scratch.predictions_")


def test_predictions_tests_fail_not_published(scoring_env):
    fake = scripted(predictions=assertion_error(PRED04))
    result = score_to_bigquery(_request(scoring_env), client_factory=lambda: fake)
    assert "publish" not in _query_kinds(fake)
    row = _record_values(fake)
    assert (row["status"], row["published"], row["data_test_failure"]) == ("data_tests_failed", False, PRED04)
    assert result.error is None and result.recorded


def test_history_tests_fail_still_published_status_data_tests_failed(scoring_env):
    fake = scripted(history=assertion_error(RUNS03))
    score_to_bigquery(_request(scoring_env), client_factory=lambda: fake)
    row = _record_values(fake)
    assert "publish" in _query_kinds(fake)
    assert (row["status"], row["published"], row["data_test_failure"]) == ("data_tests_failed", True, RUNS03)
    assert row["history_tests_passed"] is False and row["predictions_tests_passed"] is True


def test_refused_bad_run_date_is_recorded_without_tests(scoring_env):
    fake = scripted()
    result = score_to_bigquery(_request(scoring_env, run_date="2025-06-30"), client_factory=lambda: fake)
    assert _query_kinds(fake) == ["delete_table", "record"]
    row = _record_values(fake)
    assert (row["status"], row["run_date"], row["history_tests_passed"]) == ("refused", None, None)
    assert isinstance(result.error, ValueError) and result.recorded


def test_refused_after_validation_records_bundle_fields(scoring_env):
    fake = scripted()
    result = score_to_bigquery(_request(scoring_env, run_date="20221130", window_days=30), client_factory=lambda: fake)
    row = _record_values(fake)
    assert row["status"] == "refused" and "in-sample" in row["error_message"]
    assert row["model_bundle_id"] == scoring_env.lr_bundle_uri.rsplit("/", 1)[-1] and row["window_days"] == 30
    assert "load" not in _query_kinds(fake) and isinstance(result.error, ValueError)


def test_failed_load_records_and_keeps_live_untouched(scoring_env):
    fake = scripted()
    fake.load_output_rows = 1
    result = score_to_bigquery(_request(scoring_env), client_factory=lambda: fake)
    assert "publish" not in _query_kinds(fake) and "predictions" not in _query_kinds(fake)
    row = _record_values(fake)
    assert (row["status"], row["published"], row["error_type"]) == ("failed", False, "RuntimeError")
    assert isinstance(result.error, RuntimeError)


def test_publish_failure_failed_not_published(scoring_env):
    fake = scripted(publish=api.BadRequest("400 Transaction is aborted due to concurrent update"))
    run_id_holder = {}
    original = fake.on_query

    def capture(sql, config, job_id):
        if job_id and job_id.startswith("publish_"):
            run_id_holder["id"] = job_id
            fake.jobs[job_id] = [FakeJob(state="DONE", error_result={"reason": "invalidQuery"})]
        return original(sql, config, job_id)

    fake.on_query = capture
    result = score_to_bigquery(_request(scoring_env), client_factory=lambda: fake)
    row = _record_values(fake)
    assert (row["status"], row["published"]) == ("failed", False)
    assert ("get_job", run_id_holder["id"]) in fake.calls and isinstance(result.error, api.BadRequest)


def test_error_after_commit_is_failed_with_published_true_and_staging_dropped_after(scoring_env):
    fake = scripted(publish=JSONDecodeError("garbled poll response", "", 0))  # a ValueError subclass
    original = fake.on_query

    def capture(sql, config, job_id):
        if job_id and job_id.startswith("publish_"):
            fake.jobs[job_id] = [FakeJob(state="RUNNING"), FakeJob(state="DONE")]
        return original(sql, config, job_id)

    fake.on_query = capture
    result = score_to_bigquery(_request(scoring_env), client_factory=lambda: fake)
    row = _record_values(fake)
    assert (row["status"], row["published"]) == ("failed", True)  # never "refused" once a publish was attempted
    kinds = _query_kinds(fake)
    assert kinds.index("delete_table") > max(i for i, k in enumerate(kinds) if k == "get_job")
    assert isinstance(result.error, JSONDecodeError)


def test_unobservable_publish_falls_back_to_the_count(scoring_env, monkeypatch):
    monkeypatch.setattr(run_module, "resolve_job", lambda client, job_id: None)
    fake = scripted(publish=api.ServiceUnavailable("503"), landed=FakeJob(rows=[{"n": 7}]))
    score_to_bigquery(_request(scoring_env), client_factory=lambda: fake)
    assert _record_values(fake)["published"] is True

    fake = scripted(publish=api.ServiceUnavailable("503"), landed=FakeJob(rows=[{"n": 0}]))
    score_to_bigquery(_request(scoring_env), client_factory=lambda: fake)
    row = _record_values(fake)
    assert row["published"] is False and "publish outcome unknown" in row["error_message"]
    assert row["error_message"].startswith("503")


def test_insert_error_after_commit_counts_as_recorded(scoring_env):
    fake = scripted(record=api.ServiceUnavailable("503 while polling"))
    original = fake.on_query

    def capture(sql, config, job_id):
        if job_id and job_id.startswith("record_"):
            fake.jobs[job_id] = [FakeJob(state="DONE")]
        return original(sql, config, job_id)

    fake.on_query = capture
    result = score_to_bigquery(_request(scoring_env), client_factory=lambda: fake)
    assert result.recorded and result.error is None and result.record.status == "succeeded"


def test_insert_failure_on_success_sets_error(scoring_env, capsys):
    fake = scripted(record=api.Forbidden("403 bigquery.tables.updateData denied"))
    result = score_to_bigquery(_request(scoring_env), client_factory=lambda: fake)  # NotFound job -> False
    assert not result.recorded and isinstance(result.error, api.Forbidden) and result.record.status == "succeeded"
    assert "not recorded in scoring_runs" in capsys.readouterr().err


def test_insert_failure_on_data_tests_failed_keeps_status_and_none_error(scoring_env):
    fake = scripted(predictions=assertion_error(PRED04), record=FakeJob(num_dml_affected_rows=0))
    result = score_to_bigquery(_request(scoring_env), client_factory=lambda: fake)
    assert not result.recorded and result.error is None and result.record.status == "data_tests_failed"


def test_no_client_skips_the_insert(scoring_env, capsys):
    def no_adc():
        raise FileNotFoundError("no Application Default Credentials")

    result = score_to_bigquery(_request(scoring_env), client_factory=no_adc)
    assert result.record.status == "refused" and not result.recorded
    assert "no BigQuery client" in capsys.readouterr().err


def test_finished_at_uses_monotonic(scoring_env, monkeypatch):
    ticks = iter([100.0, 107.5])
    monkeypatch.setattr(run_module.time, "monotonic", lambda: next(ticks))
    fake = scripted()
    score_to_bigquery(_request(scoring_env), client_factory=lambda: fake)
    row = _record_values(fake)
    assert row["finished_at"] - row["started_at"] == timedelta(seconds=7.5)


def test_keyboard_interrupt_drops_staging_skips_insert_and_propagates(scoring_env):
    fake = scripted()

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        score_to_bigquery(_request(scoring_env), client_factory=lambda: fake, score=interrupted)
    assert "delete_table" in _query_kinds(fake) and "record" not in _query_kinds(fake)


def test_actions_trigger_and_url(scoring_env, monkeypatch):
    for key, value in {
        "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_SHA": "abc123",
        "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "42",
    }.items():  # fmt: skip
        monkeypatch.setenv(key, value)
    fake = scripted()
    score_to_bigquery(_request(scoring_env), client_factory=lambda: fake)
    row = _record_values(fake)
    assert (row["trigger"], row["github_run_url"]) == ("workflow_dispatch", "https://github.com/o/r/actions/runs/42")
    assert (row["git_commit"], row["git_dirty"]) == ("abc123", None)


def _gcs_failing_with(monkeypatch, error):
    from tests.bundles.fake_gcs import FakeStorage

    store = FakeStorage()
    store.auth_error = error  # every GCS call raises it
    monkeypatch.setattr("readmission_risk.gcp.storage_client", lambda: store)


@pytest.mark.parametrize(
    "error, status",
    [
        (api.ServiceUnavailable("503 backend error"), "failed"),  # transient: infrastructure, not the operator
        (api.Forbidden("403 storage.objects.get denied"), "refused"),  # IAM: the operator corrects it
        # what impersonated credentials really raise: RefreshError(message, response_body), never `retryable`
        (RefreshError("Unable to acquire impersonated credentials", '{"error": {"code": 403, "status": "PERMISSION_DENIED"}}'), "refused"),
        (RefreshError("Unable to acquire impersonated credentials", '{"error": {"code": 503, "status": "UNAVAILABLE"}}'), "failed"),
        (RefreshError("Unable to acquire impersonated credentials", '{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}'), "failed"),
        (RefreshError("Unable to acquire impersonated credentials", '{"error": {"code": 408, "status": "DEADLINE_EXCEEDED"}}'), "failed"),
        (RefreshError("Unable to acquire impersonated credentials", "<html>bad gateway</html>"), "failed"),
        # what user (ADC) credentials raise: an OAuth body, `retryable` set by status
        (RefreshError("invalid_grant: Token has been expired or revoked.", {"error": "invalid_grant"}), "refused"),
        (RefreshError("temporarily_unavailable", {"error": "temporarily_unavailable"}, retryable=True), "failed"),
    ],
)
def test_gcs_read_errors_behind_a_refusal_are_classified_by_cause(scoring_env, monkeypatch, error, status):
    _gcs_failing_with(monkeypatch, error)
    fake = scripted()
    result = score_to_bigquery(_request(scoring_env, bundle_uri="gs://fake-bundles/" + "a" * 64), client_factory=lambda: fake)
    row = _record_values(fake)
    assert row["status"] == status and "cannot read" in row["error_message"]
    assert isinstance(result.error, ValueError)  # the reader's mapped error, chained to the Google one


def test_gold_download_errors_are_classified_by_cause(scoring_env, monkeypatch):
    _gcs_failing_with(monkeypatch, api.ServiceUnavailable("503 backend error"))
    fake = scripted()
    score_to_bigquery(_request(scoring_env, gold_uri="gs://fake-gold/full_run_2b"), client_factory=lambda: fake)
    assert _record_values(fake)["status"] == "failed"


@pytest.mark.real_gcp_module  # the real gcp.credentials(); google.auth.default is faked below, so no network
def test_missing_adc_through_the_real_credentials_is_refused(scoring_env, monkeypatch):
    """The real gcp.credentials() maps DefaultCredentialsError to FileNotFoundError(...) from it: an operator-correctable
    refusal ("run gcloud auth application-default login"), not an infrastructure failure."""
    import google.auth

    from readmission_risk import gcp

    def no_adc(scopes=None):
        raise DefaultCredentialsError("Your default credentials were not found")

    monkeypatch.setattr(google.auth, "default", no_adc)
    result = score_to_bigquery(_request(scoring_env), client_factory=gcp.bigquery_client)
    assert result.record.status == "refused" and not result.recorded
    assert "application-default login" in str(result.error)
