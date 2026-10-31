"""The dashboard's authorized views (v3 slice 2): the packaged SQL exposes exactly the designed columns, never a label or
demographic column, and its constants equal the Python ones (notes/eg-new-feature/v3-slice2-authorized-views-2026-10-31.md,
Verification V-W)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from readmission_risk.models.baselines import _MISSING_KEY
from readmission_risk.warehouse import dashboard_views as dv
from readmission_risk.warehouse.bigquery import SERVING_GOLD_FINGERPRINT, column_names

ROOT = Path(__file__).resolve().parents[2]
_CUTOFF_LITERAL = "TIMESTAMP '2023-01-01 00:00:00+00'"


def _main_part(sql: str) -> str:
    """The call sheet's text after the reason_rates CTE (its main SELECT and JOINs)."""
    return dv.strip_comments(sql).split("\n)\n", 1)[1]


def _cte(sql: str) -> str:
    text = dv.strip_comments(sql)
    start = text.index("WITH reason_rates AS (")
    return text[start : text.index("\n)\n", start)]


@pytest.mark.parametrize("name", dv.VIEW_NAMES)
def test_view_file_shape(name):
    text = dv.view_template(name)
    assert dv.placeholders(text) == set(dv.VIEW_PLACEHOLDERS[name])
    for escape in ("$${", "%{", "%%{"):
        assert escape not in text
    assert "CURRENT_" not in text.upper()
    assert not re.search(r"SELECT\s+\*|\.\*", text, flags=re.IGNORECASE)
    assert dv.select_list(dv.view_sql(name)) == list(dv.VIEW_COLUMNS[name])
    assert not set(dv.VIEW_COLUMNS[name]) & dv.FORBIDDEN_OUTPUT_COLUMNS
    assert text.startswith("-- dashboard.") and "v3-slice2-authorized-views-2026-10-31.md" in text.split("\n", 1)[0]


def test_ops_views_are_their_schema_minus_the_free_text_columns():
    assert tuple(c for c in column_names("scoring_runs") if c not in dv.SCORING_RUNS_EXCLUDED) == dv.SCORING_RUNS_COLUMNS
    assert tuple(c for c in column_names("monitoring") if c not in dv.MONITORING_EXCLUDED) == dv.MONITORING_COLUMNS
    assert {"error_message", "gold_uri", "predictions_table"} == dv.SCORING_RUNS_EXCLUDED
    assert {"error_message"} == dv.MONITORING_EXCLUDED
    assert set(column_names("scoring_runs")) >= dv.SCORING_RUNS_EXCLUDED
    assert set(column_names("monitoring")) >= dv.MONITORING_EXCLUDED


def test_every_base_column_is_classified():
    predictions = set(column_names("predictions"))
    assert not dv.PREDICTIONS_EXPOSED & dv.PREDICTIONS_NOT_EXPOSED
    assert predictions == dv.PREDICTIONS_EXPOSED | dv.PREDICTIONS_NOT_EXPOSED
    gold = set(column_names("gold"))
    assert not dv.GOLD_READ & dv.GOLD_NOT_READ
    assert gold == dv.GOLD_READ | dv.GOLD_NOT_READ
    assert gold >= dv.GOLD_RATE_INPUTS
    assert set(dv.CALL_SHEET_COLUMNS) >= dv.PREDICTIONS_EXPOSED


def test_call_sheet_reads_only_the_classified_columns():
    sql = dv.view_template("v_call_sheet")
    main = _main_part(sql)
    p_refs = set(re.findall(r"\bp\.(\w+)", main))
    g_refs = set(re.findall(r"\bg\.(\w+)", main))
    assert p_refs <= dv.PREDICTIONS_EXPOSED | {"gold_fingerprint", "admission_reason_code"}
    assert p_refs >= dv.PREDICTIONS_EXPOSED
    assert g_refs == dv.GOLD_READ
    in_cte = {c for c in column_names("gold") if re.search(rf"\b{c}\b", _cte(sql))}
    assert in_cte <= dv.GOLD_RATE_INPUTS
    # the labels are read only inside the CTE's aggregate and filter, never by the main SELECT
    assert "is_readmitted" not in main and "label_horizon" not in main


def test_call_sheet_constants_equal_the_python_constants():
    sql = dv.view_template("v_call_sheet")
    assert re.findall(r"'([0-9a-f]{64})'", sql) == [SERVING_GOLD_FINGERPRINT]
    assert sql.count(_CUTOFF_LITERAL) == 2
    assert dv.REASON_RATE_CUTOFF.strftime("TIMESTAMP '%Y-%m-%d %H:%M:%S+00'") == _CUTOFF_LITERAL
    prereg = (ROOT / "prereg.yaml").read_text()
    resolved = re.search(r'test_start_date_resolved:\s*"(\d{8})"', prereg)
    assert resolved and resolved.group(1) == dv.REASON_RATE_CUTOFF.strftime("%Y%m%d")
    assert f"IF(r.n >= {dv.REASON_RATE_MIN_N}, r.rate, NULL)" in sql
    assert sql.count(f"'{dv.MISSING_REASON_KEY}'") == 2
    assert dv.MISSING_REASON_KEY == _MISSING_KEY
    assert "TIMESTAMP_DIFF(g.index_stop, g.index_start, MICROSECOND) / 86400000000 AS length_of_stay_days" in sql


def test_view_sql_renders_the_terraform_table_references():
    sql = dv.view_sql("v_call_sheet", project_id="p")
    assert "`p.serving.predictions` AS p" in sql and "`p.serving.gold` AS g" in sql
    assert "${" not in sql


def test_view_sql_refuses_unknown_names_escapes_and_leftover_placeholders(monkeypatch):
    with pytest.raises(ValueError, match="unknown view"):
        dv.view_sql("v_gold")
    monkeypatch.setattr(dv, "view_template", lambda name: "SELECT a FROM `$${x}`")
    with pytest.raises(ValueError, match="templatefile"):
        dv.view_sql("v_monitoring")
    monkeypatch.setattr(dv, "view_template", lambda name: "SELECT a FROM `${gold}`")
    with pytest.raises(ValueError, match="placeholder"):
        dv.view_sql("v_monitoring")


def test_select_list_handles_nested_commas_comments_and_aliases():
    sql = "-- SELECT x FROM y\nWITH c AS (SELECT a, b FROM t)\nSELECT\n  c.a,\n  IF(c.a >= 3, c.b, NULL) AS x,\n  f(c.a, 1) AS y\nFROM c"
    assert dv.select_list(sql) == ["a", "x", "y"]
    with pytest.raises(ValueError):
        dv.select_list("no select here")


def _gold(rows):
    frame = pd.DataFrame(rows, columns=["admission_reason_code", "is_readmitted", "index_stop", "label_horizon"])
    for col in ("index_stop", "label_horizon"):
        frame[col] = pd.to_datetime(frame[col], utc=True)
    return frame


def test_reason_rates_population_suppression_and_missing_key():
    early, cutoff = "2022-06-01T00:00:00Z", "2023-01-01T00:00:00Z"
    rows = (
        [("A", 1, early, early)] * 10 + [("A", 0, early, early)] * 20  # n = 30: rate shown
        + [("B", 1, early, early)] * 29  # n = 29: rate withheld
        + [(None, 0, early, early)] * 30  # NULL reason: its own group
        + [("A", 1, cutoff, cutoff), ("A", 1, early, cutoff)]  # == cutoff on either column: excluded
        + [("C", 1, "2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z")]  # after the cutoff
    )  # fmt: skip
    gold = _gold(rows)
    before = gold.copy()
    rates = dv.reason_rates(gold)
    pd.testing.assert_frame_equal(gold, before)
    assert list(rates["reason_key"]) == ["A", "B", "MISSING"]
    assert list(rates["n"]) == [30, 29, 30] and rates["n"].dtype == "int64"
    assert rates["rate"].iloc[0] == pytest.approx(10 / 30)
    assert pd.isna(rates["rate"].iloc[1])
    assert rates["rate"].iloc[2] == 0.0


def test_reason_cutoff_is_before_every_published_200k_date():
    """All published 200k run dates are in the champion's test window (2023-04-17..), after the cutoff (D2)."""
    assert dv.REASON_RATE_CUTOFF.date() < date(2023, 4, 17)
