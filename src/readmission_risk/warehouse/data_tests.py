"""The packaged ASSERT data tests (PRD FR18): rendering, running, and telling an assertion failure (a data-test verdict)
from any other error (a broken script, a permission, the bytes cap), which must surface as a failed run instead. See
notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md (Interfaces > SQL and data_tests.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from importlib import resources

from readmission_risk.gcp import PROJECT_ID

from .bigquery import (
    MAX_BYTES_BILLED,
    SCRATCH_GOLD_DATASET,
    SERVING_DATASET,
    query_config,
    quoted,
)

SCRIPT_NAMES = ("predictions", "scoring_runs", "monitoring", "gold")
_TABLE_RE = re.compile(re.escape(PROJECT_ID) + r"\.[A-Za-z_][A-Za-z0-9_]{0,1023}\.[A-Za-z_][A-Za-z0-9_]{0,1023}")
_DESCRIPTION_RE = re.compile(r"AS '((?:PRED|RUNS|MON|GOLD)-\d{2}: [^']*)';")
# A failing ASSERT's message, as BigQuery reports it. Fixed from the live L0 probe (see the design note's Results); a
# syntax error that merely QUOTES a label literal must not match.
_ASSERT_MESSAGE_RE = re.compile(r"(?:Query error: )?(?P<description>(?:PRED|RUNS|MON|GOLD)-\d{2}: [^\n]*?)(?: at \[\d+:\d+\])?")


@dataclass(frozen=True)
class DataTestResult:
    script: str
    passed: bool
    failure: str | None  # "<ID>: <message>"


def _script_text(name: str) -> str:
    if name not in SCRIPT_NAMES:
        raise ValueError(f"unknown data-test script {name!r}; expected one of {SCRIPT_NAMES}")
    return resources.files("readmission_risk.warehouse").joinpath("sql", f"{name}.sql").read_text()


def assertion_descriptions(name: str) -> dict[str, str]:
    """{"PRED-01": "PRED-01: <message>", ...}: every assertion label of the packaged script, by ID."""
    return {description.split(":", 1)[0]: description for description in _DESCRIPTION_RE.findall(_script_text(name))}


def _dataset_of(table: str) -> str:
    return table.split(".")[1]


def render_script(name: str, *, table: str, predictions_table: str | None = None) -> str:
    """The script with its placeholders filled. The gold script (v3 slice 1) audits a staging table in scratch_gold and reads
    the predictions table: exactly serving.predictions, or a seeded copy in scratch_gold (warehouse.gold_seeded_check)."""
    text = _script_text(name)
    if not isinstance(table, str) or not _TABLE_RE.fullmatch(table):
        raise ValueError(f"data tests run only on a table of project {PROJECT_ID} (project.dataset.table); got {table!r}")
    if name != "gold":
        if predictions_table is not None:
            raise ValueError(f"the {name} data tests take no predictions_table")
        return text.replace("{table}", quoted(table))
    if _dataset_of(table) != SCRATCH_GOLD_DATASET:
        raise ValueError(f"the gold data tests run only on a staging table in {SCRATCH_GOLD_DATASET}; got {table!r}")
    if not isinstance(predictions_table, str) or not _TABLE_RE.fullmatch(predictions_table):
        raise ValueError(f"the gold data tests need predictions_table (project.dataset.table); got {predictions_table!r}")
    serving_predictions = f"{PROJECT_ID}.{SERVING_DATASET}.predictions"
    if predictions_table != serving_predictions and _dataset_of(predictions_table) != SCRATCH_GOLD_DATASET:
        raise ValueError(
            f"predictions_table must be {serving_predictions} or a table in {SCRATCH_GOLD_DATASET}; got {predictions_table!r}"
        )
    return text.replace("{table}", quoted(table)).replace("{predictions_table}", quoted(predictions_table))


def _messages(exc: BaseException) -> list[str]:
    structured = [e.get("message", "") for e in (getattr(exc, "errors", None) or []) if isinstance(e, dict)]
    return [m for m in structured if m] or [getattr(exc, "message", None) or str(exc)]


def classify_error(exc: BaseException, name: str) -> DataTestResult | None:
    """A DataTestResult(passed=False) if `exc` is a failing ASSERT of script `name`, else None. The verdict needs a FULL
    match of one structured error message against _ASSERT_MESSAGE_RE AND a description that is exactly one of the
    script's own labels, so a syntax error quoting a label literal is not mistaken for a verdict."""
    from google.api_core.exceptions import GoogleAPICallError

    if not isinstance(exc, GoogleAPICallError):
        return None
    known = set(assertion_descriptions(name).values())
    for message in _messages(exc):
        match = _ASSERT_MESSAGE_RE.fullmatch(message.strip())
        if match and match.group("description") in known:
            return DataTestResult(script=name, passed=False, failure=match.group("description"))
    return None


def run_data_tests(
    client,
    name: str,
    *,
    table: str,
    run_date: date | None = None,
    scoring_run_id: str | None = None,
    predictions_table: str | None = None,
    n_rows: int | None = None,
    n_positive: int | None = None,
    predicted_fingerprint: str | None = None,
    max_bytes_billed: int = MAX_BYTES_BILLED,
    job_id: str | None = None,
) -> DataTestResult:
    """Runs one packaged script. `job_id` (optional) fixes the job's id, e.g. so the caller can read its billed bytes; a
    caller-chosen id cannot be retried under the default job retry, so it is sent with job_retry=None."""
    from google.api_core.exceptions import GoogleAPICallError
    from google.cloud import bigquery

    gold_keywords = (n_rows, n_positive, predicted_fingerprint)
    if name != "gold" and any(value is not None for value in gold_keywords):
        raise ValueError(f"the {name} data tests take no n_rows/n_positive/predicted_fingerprint")
    if name == "gold":
        if run_date is not None or scoring_run_id is not None:
            raise ValueError("the gold data tests take no run_date or scoring_run_id")
        if not (type(n_rows) is int and type(n_positive) is int and isinstance(predicted_fingerprint, str)):
            raise ValueError("the gold data tests need n_rows (int), n_positive (int) and predicted_fingerprint (str)")
        parameters = [
            bigquery.ScalarQueryParameter("n_rows", "INT64", n_rows),
            bigquery.ScalarQueryParameter("n_positive", "INT64", n_positive),
            bigquery.ScalarQueryParameter("predicted_fingerprint", "STRING", predicted_fingerprint),
        ]
    elif name == "predictions":
        if run_date is None or scoring_run_id is None:
            raise ValueError("the predictions data tests need run_date and scoring_run_id")
        parameters = [
            bigquery.ScalarQueryParameter("run_date", "DATE", run_date),
            bigquery.ScalarQueryParameter("scoring_run_id", "STRING", scoring_run_id),
        ]
    elif name in ("scoring_runs", "monitoring"):
        if run_date is not None or scoring_run_id is not None:
            raise ValueError(f"the {name} data tests take no parameters")
        parameters = []
    else:
        raise ValueError(f"unknown data-test script {name!r}; expected one of {SCRIPT_NAMES}")
    script = render_script(name, table=table, predictions_table=predictions_table)
    try:
        if job_id is None:
            job = client.query(script, job_config=query_config(max_bytes_billed=max_bytes_billed, query_parameters=parameters))
        else:
            job = client.query(
                script,
                job_config=query_config(max_bytes_billed=max_bytes_billed, query_parameters=parameters),
                job_id=job_id,
                job_retry=None,
            )
        job.result()
    except GoogleAPICallError as exc:
        verdict = classify_error(exc, name)
        if verdict is None:
            raise
        return verdict
    return DataTestResult(script=name, passed=True, failure=None)
