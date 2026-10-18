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

from .bigquery import query_config, quoted

SCRIPT_NAMES = ("predictions", "scoring_runs", "monitoring")
_TABLE_RE = re.compile(re.escape(PROJECT_ID) + r"\.[A-Za-z_][A-Za-z0-9_]{0,1023}\.[A-Za-z_][A-Za-z0-9_]{0,1023}")
_DESCRIPTION_RE = re.compile(r"AS '((?:PRED|RUNS|MON)-\d{2}: [^']*)';")
# A failing ASSERT's message, as BigQuery reports it. Fixed from the live L0 probe (see the design note's Results); a
# syntax error that merely QUOTES a label literal must not match.
_ASSERT_MESSAGE_RE = re.compile(r"(?:Query error: )?(?P<description>(?:PRED|RUNS|MON)-\d{2}: [^\n]*?)(?: at \[\d+:\d+\])?")


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


def render_script(name: str, *, table: str) -> str:
    if not isinstance(table, str) or not _TABLE_RE.fullmatch(table):
        raise ValueError(f"data tests run only on a table of project {PROJECT_ID} (project.dataset.table); got {table!r}")
    return _script_text(name).replace("{table}", quoted(table))


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
    client, name: str, *, table: str, run_date: date | None = None, scoring_run_id: str | None = None
) -> DataTestResult:
    from google.api_core.exceptions import GoogleAPICallError
    from google.cloud import bigquery

    if name == "predictions":
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
    script = render_script(name, table=table)
    try:
        client.query(script, job_config=query_config(query_parameters=parameters)).result()
    except GoogleAPICallError as exc:
        verdict = classify_error(exc, name)
        if verdict is None:
            raise
        return verdict
    return DataTestResult(script=name, passed=True, failure=None)
