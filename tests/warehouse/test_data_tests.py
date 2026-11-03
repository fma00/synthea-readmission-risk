"""The packaged ASSERT scripts (static shape rules) and the verdict rule that separates an assertion failure from a broken
script (v2 slice 4, PRD FR18)."""

from __future__ import annotations

import re
from datetime import date

import pytest
from google.api_core import exceptions as api

from readmission_risk.warehouse import data_tests
from readmission_risk.warehouse.data_tests import (
    assertion_descriptions,
    classify_error,
    render_script,
    run_data_tests,
)
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob, assertion_error

TABLE = "synthea-readmission-risk.scratch.predictions_abc"
RUNS = "synthea-readmission-risk.serving.scoring_runs"
EXPECTED_IDS = {
    "predictions": [f"PRED-{i:02d}" for i in range(1, 12)],
    "scoring_runs": [f"RUNS-{i:02d}" for i in range(1, 8)],
    "monitoring": [f"MON-{i:02d}" for i in range(1, 13)],
    "gold": [f"GOLD-{i:02d}" for i in range(1, 8)],
}


def _statements(name: str) -> list[str]:
    text = "\n".join(line for line in data_tests._script_text(name).splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in text.split(";") if s.strip()]


@pytest.mark.parametrize("name", ["predictions", "scoring_runs", "monitoring", "gold"])
def test_sql_scripts_follow_the_shape_rule(name):
    statements = _statements(name)
    assert statements[0].startswith("DECLARE c DEFAULT (")
    assert statements[0].count("FROM {table}") >= 1
    asserts = statements[1:]
    assert all(s.startswith("ASSERT ") for s in asserts)
    assert all("FROM" not in s.upper().split(" AS '")[0] for s in asserts)  # assertions read only `c`
    labels = [re.search(r" AS '((?:PRED|RUNS|MON|GOLD)-\d{2}): [^']+'$", s) for s in asserts]
    assert all(labels), asserts
    assert [m.group(1) for m in labels] == EXPECTED_IDS[name]
    body = statements[0]
    for countif in re.finditer(r"COUNTIF\(", body):
        assert body[countif.end() :].startswith("("), "every COUNTIF has the form COUNTIF((...) IS NOT TRUE)"
        depth, i = 1, countif.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(body[i], 0)
            i += 1
        assert body[countif.end() : i - 1].rstrip().endswith("IS NOT TRUE")
    for forbidden in ("IFNULL", "COALESCE", "IS NOT FALSE"):
        assert forbidden not in data_tests._script_text(name)
    assert re.search(r"(?<!`)\bpartition\b(?!`)", body) is None  # the reserved word is always back-quoted


def test_assertion_descriptions_parsed_from_sql():
    for name, ids in EXPECTED_IDS.items():
        descriptions = assertion_descriptions(name)
        assert list(descriptions) == ids
        assert all(d.startswith(f"{i}: ") for i, d in descriptions.items())


def test_render_script_backquotes_and_refuses_bad_identifiers():
    assert f"FROM `{TABLE}`" in render_script("predictions", table=TABLE)
    for bad in ("other-project.scratch.x", "synthea-readmission-risk.scratch", "synthea-readmission-risk.a.b; DROP x", "`x`"):
        with pytest.raises(ValueError, match="data tests run only"):
            render_script("predictions", table=bad)
    with pytest.raises(ValueError, match="unknown data-test script"):
        render_script("dashboard", table=TABLE)


def test_classify_error_needs_an_exact_assertion_message():
    pred04 = assertion_descriptions("predictions")["PRED-04"]
    verdict = classify_error(assertion_error(pred04), "predictions")
    assert verdict is not None and not verdict.passed and verdict.failure == pred04
    assert classify_error(assertion_error(f"Query error: {pred04} at [42:1]"), "predictions").failure == pred04
    # any GoogleAPICallError class
    assert classify_error(api.InternalServerError(pred04, errors=[{"message": pred04}]), "predictions").failure == pred04
    # a syntax error that merely QUOTES a label literal is not a verdict
    syntax = f"Syntax error: Expected \")\" but got string literal '{pred04}' at [40:12]"
    assert classify_error(assertion_error(syntax), "predictions") is None
    # an unknown ID, an altered description, another script's label, or a non-API error: not a verdict
    assert classify_error(assertion_error("PRED-99: invented"), "predictions") is None
    assert classify_error(assertion_error(pred04 + " (edited)"), "predictions") is None
    assert classify_error(assertion_error(assertion_descriptions("scoring_runs")["RUNS-03"]), "predictions") is None
    assert classify_error(ValueError(pred04), "predictions") is None


def _fake_with(outcome) -> FakeBigQuery:
    def on_query(sql, config, job_id):
        if isinstance(outcome, BaseException):
            return FakeJob(raise_on_result=outcome)
        return FakeJob()

    return FakeBigQuery(on_query=on_query)


def test_run_data_tests_passes_and_sends_typed_parameters():
    fake = _fake_with(None)
    result = run_data_tests(fake, "predictions", table=TABLE, run_date=date(2025, 6, 30), scoring_run_id="r1")
    assert result.passed and result.failure is None
    _, sql, config, _ = fake.calls[0]
    assert f"`{TABLE}`" in sql and config.maximum_bytes_billed == 100 * 1024**2
    params = {p.name: (p.type_, p.value) for p in config.query_parameters}
    assert params == {"run_date": ("DATE", date(2025, 6, 30)), "scoring_run_id": ("STRING", "r1")}
    result = run_data_tests(fake, "scoring_runs", table=RUNS)
    assert result.passed and fake.calls[1][2].query_parameters == []


def test_run_data_tests_parameter_rules():
    fake = _fake_with(None)
    with pytest.raises(ValueError, match="need run_date and scoring_run_id"):
        run_data_tests(fake, "predictions", table=TABLE, run_date=date(2025, 6, 30))
    with pytest.raises(ValueError, match="take no parameters"):
        run_data_tests(fake, "scoring_runs", table=RUNS, run_date=date(2025, 6, 30))
    assert fake.calls == []


def test_run_data_tests_verdict_only_on_exact_assertion_message():
    runs03 = assertion_descriptions("scoring_runs")["RUNS-03"]
    result = run_data_tests(_fake_with(assertion_error(runs03)), "scoring_runs", table=RUNS)
    assert (result.passed, result.failure) == (False, runs03)


@pytest.mark.parametrize(
    "error",
    [
        api.Forbidden("403 Access Denied: Table scoring_runs: Permission bigquery.tables.getData denied"),
        api.BadRequest("Query exceeded limit for bytes billed: 1048576. 10485760 or higher required."),
        assertion_error("Syntax error: Unexpected keyword ASSERT at [3:1] near 'RUNS-03: a run finished before it started'"),
    ],
)
def test_run_data_tests_propagates_other_errors(error):
    with pytest.raises(type(error)):
        run_data_tests(_fake_with(error), "scoring_runs", table=RUNS)


# Recorded live in L0 (2026-10-11, as the scorer SA): what BigQuery really returns for a failing ASSERT, and for a
# malformed script that quotes a label literal.
L0_ASSERT_MESSAGE = "Query error: PRED-01: the run date has no prediction rows at [40:1]"
L0_SYNTAX_MESSAGE = 'Syntax error: Expected "," but got keyword AS at [40:23]'


def test_classify_error_on_the_recorded_live_messages():
    real = api.BadRequest(
        "400 GET https://bigquery.googleapis.com/...: " + L0_ASSERT_MESSAGE,
        errors=[{"message": L0_ASSERT_MESSAGE, "domain": "global", "reason": "invalidQuery"}],
    )
    verdict = classify_error(real, "predictions")
    assert verdict is not None and verdict.failure == "PRED-01: the run date has no prediction rows"
    syntax = api.BadRequest("400 " + L0_SYNTAX_MESSAGE, errors=[{"reason": "invalidQuery", "message": L0_SYNTAX_MESSAGE}])
    assert classify_error(syntax, "predictions") is None
