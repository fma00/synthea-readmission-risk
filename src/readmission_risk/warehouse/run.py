"""One scoring attempt into BigQuery: history data tests, gold, the unchanged scorer writing to staging, predictions data
tests, publish, and ALWAYS one scoring_runs row (unless interrupted or no client exists). Never raises an Exception: the
result carries the error, and the CLI decides the exit code. See
notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md (Interfaces > warehouse/run.py; Flow).
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from readmission_risk import gcp
from readmission_risk.pipeline.gold_metadata import parse_yyyymmdd
from readmission_risk.scoring.constants import DEFAULT_TOP_N
from readmission_risk.scoring.pipeline import (
    ScoringConfig,
    ScoringResult,
    score_run_date,
)

from .data_tests import DataTestResult, run_data_tests
from .gold_source import materialized_gold_dir
from .scoring_runs import (
    ERROR_MESSAGE_LIMIT,
    RunRecord,
    append_run,
    record_job_id,
    scoring_runs_table_id,
)
from .staging import (
    drop_staging,
    make_staging_writer,
    predictions_table_id,
    publish,
    publish_job_id,
    publish_landed,
    resolve_job,
    staging_table_id,
)


@dataclass(frozen=True)
class BigQueryRunRequest:
    gold_uri: str
    run_date: str  # "YYYYMMDD" as given; parsed in the flow (after the client is built), never at construction
    bundle_uri: str | None = None
    champion_path: Path | None = None
    top_n: int = DEFAULT_TOP_N
    window_days: int | None = None


@dataclass(frozen=True, eq=False)
class BigQueryRunResult:
    record: RunRecord
    scoring: ScoringResult | None
    data_tests: tuple[DataTestResult, ...]
    error: BaseException | None
    recorded: bool


def _trigger_and_url() -> tuple[str, str | None]:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return "laptop", None
    trigger = os.environ.get("GITHUB_EVENT_NAME") or "github_actions"
    parts = [os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")]
    url = f"{parts[0]}/{parts[1]}/actions/runs/{parts[2]}" if all(parts) else None
    return trigger, url


def _git_state() -> tuple[str | None, bool | None]:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return os.environ.get("GITHUB_SHA") or None, None
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return commit or None, bool(status.strip())


def _chain(exc: BaseException) -> list[BaseException]:
    """exc and every exception it was raised from or during (__cause__, then __context__), without cycles."""
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and all(current is not s for s in seen):
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def _refresh_error_is_permanent(error: BaseException) -> bool:
    """A token refresh the operator must fix (a denied impersonation, an expired or revoked login), as opposed to a
    transient one. google-auth's impersonated credentials raise RefreshError(message, response_body) for EVERY non-200
    answer of the IAM Credentials endpoint, with no `retryable` flag, so the decision is made from the body: an HTTP 4xx
    that google-auth does not itself retry (e.g. 403 PERMISSION_DENIED; not 408 or 429) or an OAuth error string (e.g. invalid_grant) is permanent; a 5xx, a 408 or 429,
    a `retryable` error or an unreadable body is not."""
    from google.auth.transport import (
        DEFAULT_RETRYABLE_STATUS_CODES,  # google-auth's own transient set (408, 429, 5xx)
    )

    if getattr(error, "retryable", False):
        return False
    body = error.args[1] if len(error.args) > 1 else None
    if isinstance(body, bytes | str):
        try:
            body = json.loads(body)
        except ValueError:
            return False
    detail = body.get("error") if isinstance(body, dict) else None
    if isinstance(detail, str):  # OAuth token endpoint: {"error": "invalid_grant", ...}
        return True
    code = detail.get("code") if isinstance(detail, dict) else None
    return isinstance(code, int) and 400 <= code < 500 and code not in DEFAULT_RETRYABLE_STATUS_CODES


def _classify(exc: Exception, publish_attempted: bool) -> str:
    """refused = a user-correctable refusal before any write (the CLI's ValueError/FileNotFoundError family). failed = an
    infrastructure failure: an error raised by a Google, auth or HTTP library is a failure even when it subclasses
    ValueError, and so is a refusal-shaped ValueError that a reader raised FROM such an error (a 503 while downloading
    the bundle or the gold table) -- except an operator-correctable cause behind it: a 403/404 (IAM, a wrong path), no ADC,
    or a non-retryable token refresh (impersonation denied, an expired login). Anything
    after a publish was attempted is a failure too (a refusal must never co-occur with a publish: RUNS-07)."""
    from google.api_core.exceptions import Forbidden, GoogleAPIError, NotFound
    from google.auth.exceptions import (
        DefaultCredentialsError,
        GoogleAuthError,
        RefreshError,
    )
    from requests.exceptions import RequestException

    infrastructure = GoogleAPIError | GoogleAuthError | RequestException

    def operator_correctable(cause: BaseException) -> bool:
        # IAM (403), a wrong path (404), no ADC, a denied impersonation or dead login
        if isinstance(cause, Forbidden | NotFound | DefaultCredentialsError):
            return True
        return isinstance(cause, RefreshError) and _refresh_error_is_permanent(cause)

    if isinstance(exc, infrastructure) or publish_attempted:
        return "failed"
    if not isinstance(exc, ValueError | FileNotFoundError):
        return "failed"
    for cause in _chain(exc)[1:]:
        if isinstance(cause, infrastructure) and not operator_correctable(cause):
            return "failed"
    return "refused"


def _warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)


def score_to_bigquery(
    request: BigQueryRunRequest,
    *,
    client_factory: Callable | None = None,
    score: Callable = score_run_date,
) -> BigQueryRunResult:
    scoring_run_id = uuid.uuid4().hex
    started_at, start = datetime.now(UTC), time.monotonic()
    trigger, url = _trigger_and_url()
    commit, dirty = _git_state()
    record = RunRecord(
        scoring_run_id=scoring_run_id, status=None, started_at=started_at, finished_at=None, run_date=None,
        trigger=trigger, github_run_url=url, git_commit=commit, git_dirty=dirty,
        python_version=platform.python_version(), gold_uri=request.gold_uri, gold_fingerprint=None,
        champion_sha256=None, model_bundle_id=None, model_name=None, predictions_table=predictions_table_id(),
        top_n=request.top_n, window_days=None, n_predictions=None, n_top_n=None, history_tests_passed=None,
        predictions_tests_passed=None, published=False, data_test_failure=None, error_type=None, error_message=None,
        gold_fingerprint_definition=None,
    )  # fmt: skip
    client = None
    scoring: ScoringResult | None = None
    tests: list[DataTestResult] = []
    error: BaseException | None = None
    publish_attempted = False
    publish_warning: str | None = None

    def on_validated(bundle, window_days: int) -> None:
        record.gold_fingerprint, record.champion_sha256 = bundle.gold_fingerprint, bundle.champion_sha256
        record.model_bundle_id, record.model_name, record.window_days = bundle.bundle_id, bundle.model_name, window_days
        record.gold_fingerprint_definition = bundle.gold_fingerprint_definition

    try:
        client = (client_factory or gcp.bigquery_client)()
        run_date = parse_yyyymmdd(request.run_date, "run_date")
        record.run_date = run_date
        history = run_data_tests(client, "scoring_runs", table=scoring_runs_table_id())
        tests.append(history)
        record.history_tests_passed = history.passed
        with materialized_gold_dir(request.gold_uri) as gold_dir:
            config = ScoringConfig(
                gold_dir=gold_dir,
                run_date=request.run_date,
                output_dir=Path("unused-by-bigquery"),
                bundle_uri=request.bundle_uri,
                champion_path=request.champion_path,
                top_n=request.top_n,
                window_days=request.window_days,
                show_observed_outcomes=False,
            )
            scoring = score(config, writer=make_staging_writer(client, scoring_run_id, lambda: record.gold_fingerprint_definition), on_validated=on_validated)
        record.n_predictions = len(scoring.predictions)
        record.n_top_n = int(scoring.predictions["is_top_n"].sum())
        predictions = run_data_tests(
            client, "predictions", table=staging_table_id(scoring_run_id), run_date=run_date, scoring_run_id=scoring_run_id
        )
        tests.append(predictions)
        record.predictions_tests_passed = predictions.passed
        if predictions.passed:
            publish_attempted = True
            try:
                publish(client, scoring_run_id, run_date)
                record.published = True
            except Exception:
                outcome = resolve_job(client, publish_job_id(scoring_run_id))
                if outcome is None:
                    outcome = publish_landed(client, scoring_run_id, run_date)
                record.published = outcome is True
                if outcome is None:
                    publish_warning = f"publish outcome unknown; check serving.predictions for scoring_run_id {scoring_run_id}"
                    _warn(publish_warning)
                raise
        failures = [t.failure for t in tests if not t.passed]
        record.status = "succeeded" if not failures and record.published else "data_tests_failed"
        record.data_test_failure = failures[0] if failures else None
    except Exception as exc:  # noqa: BLE001 -- the run must be recorded whatever failed; the exception is kept and re-raised by the CLI
        error = exc
        record.status = _classify(exc, publish_attempted)
        record.error_type = type(exc).__name__
        message = str(exc) + (f"\n{publish_warning}" if publish_warning else "")
        record.error_message = message[:ERROR_MESSAGE_LIMIT]
    finally:  # a KeyboardInterrupt still drops staging, then propagates: the insert below never runs, so it is unrecorded
        if client is not None:
            try:
                drop_staging(client, scoring_run_id)
            except Exception as exc:  # noqa: BLE001 -- best effort; the scratch dataset's 1-day expiration is the backstop
                _warn(f"could not drop the staging table {staging_table_id(scoring_run_id)}: {exc}")
    recorded = False
    record.finished_at = started_at + timedelta(seconds=time.monotonic() - start)
    if client is None:
        _warn(f"the run was not recorded in scoring_runs: no BigQuery client ({error})")
    else:
        recorded, insert_error = _record(client, record)
        if not recorded and record.status == "succeeded":
            error = insert_error
    return BigQueryRunResult(record=record, scoring=scoring, data_tests=tuple(tests), error=error, recorded=recorded)


def _record(client, record: RunRecord) -> tuple[bool, BaseException | None]:
    """Inserts the row. A client-side error is resolved by the insert job's id before declaring the run unrecorded."""
    try:
        append_run(client, record)
    except RuntimeError as exc:  # the affected-rows post-condition: the job ran but did not insert exactly one row
        _warn(f"the run was not recorded in scoring_runs: {exc}")
        return False, exc
    except Exception as exc:  # noqa: BLE001 -- resolved by the job id below; the run's own outcome must not be lost
        outcome = resolve_job(client, record_job_id(record.scoring_run_id))
        if outcome is True:
            return True, None
        unknown = " (outcome unknown)" if outcome is None else ""
        _warn(f"the run was not recorded in scoring_runs{unknown}: {exc}")
        return False, exc
    return True, None
