"""The dashboard's fixed queries (v3 slice 2, PRD FR8/FR15; Verification V-D): views only, every value a parameter, no
CURRENT_*, the byte cap and the job label on every job, the scorer's own window, and the variants' contract."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

import pytest

from readmission_risk.dashboard import queries as q
from readmission_risk.monitoring.config import read_monitoring_config
from readmission_risk.scoring.selection import window_bounds
from readmission_risk.warehouse.bigquery import (
    MAX_BYTES_BILLED,
    SERVING_GOLD_FINGERPRINT,
)
from readmission_risk.warehouse.dashboard_views import (
    CALL_SHEET_COLUMNS,
    MONITORING_COLUMNS,
    SCORING_RUNS_COLUMNS,
)
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob

PUBLISHED = q.PublishedDate(date(2023, 10, 30), "run-1", "bundle-1", 7, 20, 41, date(2023, 10, 30))
VARIANTS = [
    q._published_dates_query(clock_bound=c, fingerprint_filter=f, limit=10) for c in (True, False) for f in (True, False)
] + [
    q._call_sheet_variant(PUBLISHED, discharge=d, as_of=a)
    for d in (None, (datetime(1900, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC)))
    for a in (True, False)
]
FIXED = [q.published_dates(), q.call_sheet(PUBLISHED), q.monitoring_history(), q.recent_scoring_runs()]
ALL = FIXED + VARIANTS


def _selected(sql: str) -> list[str]:
    outer = sql.rsplit("\nSELECT ", 1)[1] if "\nSELECT " in sql else sql.split("SELECT ", 1)[1]
    return [c.strip().split(" AS ")[-1].split(".")[-1] for c in outer.split("\nFROM ", 1)[0].split(",")]


@pytest.mark.parametrize("query", ALL, ids=lambda x: x.name)
def test_views_only_parameters_only_and_no_current(query):
    tables = set(re.findall(r"`([^`]+)`", query.sql))
    assert tables and all(re.fullmatch(r"synthea-readmission-risk\.dashboard\.v_\w+", t) for t in tables)
    assert "CURRENT_" not in query.sql.upper()
    assert "'" not in query.sql and '"' not in query.sql  # no string literal of any kind
    assert not re.search(r"\b(DATE|TIMESTAMP)\s*'", query.sql)
    assert set(re.findall(r"\b\d+\b", query.sql)) <= {"1"}
    for number in re.finditer(r"\b\d+\b", query.sql):
        context = query.sql[max(0, number.start() - 6) : number.end()]
        assert context.endswith(("rn = 1", "LIMIT 1")), context
    assert set(re.findall(r"@(\w+)", query.sql)) == {name for name, _, _ in query.parameters}
    assert len({name for name, _, _ in query.parameters}) == len(query.parameters)


def test_selected_columns_equal_the_view_constants():
    published, sheet, monitoring, runs = FIXED
    assert _selected(sheet.sql) == list(CALL_SHEET_COLUMNS)
    assert _selected(monitoring.sql) == list(MONITORING_COLUMNS)
    assert _selected(runs.sql) == list(SCORING_RUNS_COLUMNS)
    assert _selected(published.sql) == [
        "run_date", "scoring_run_id", "model_bundle_id", "window_days", "top_n", "n_predictions", "clock_sim_date"
    ]  # fmt: skip
    assert [f for f in q.PublishedDate.__dataclass_fields__] == _selected(published.sql)


def test_published_dates_rules():
    query = q.published_dates()
    assert query == q._published_dates_query(clock_bound=True, fingerprint_filter=True, limit=q.PUBLISHED_DATES_LIMIT)
    assert query.name == "published_dates"
    params = {n: (t, v) for n, t, v in query.parameters}
    assert params == {
        "epoch": ("DATE", q.OPS_EPOCH),
        "succeeded": ("STRING", "succeeded"),
        "gold_fingerprint": ("STRING", SERVING_GOLD_FINGERPRINT),
        "limit": ("INT64", q.PUBLISHED_DATES_LIMIT),
    }
    latest = query.sql[query.sql.index("latest AS (") :]
    latest = latest[: latest.index("\n)")]
    assert "status" not in latest  # D1: the newest PUBLISHED run owns the date's rows, whatever its status
    assert "published IS TRUE" in latest
    for column in ("model_bundle_id", "window_days", "top_n", "n_predictions", "scoring_run_id", "run_date"):
        assert f"{column} IS NOT NULL" in latest
    ticks = query.sql[query.sql.index("ticks AS (") : query.sql.index("monitored_bundle AS (")]
    assert "status = @succeeded" in ticks
    assert "PARTITION BY run_date ORDER BY started_at DESC, scoring_run_id DESC" in latest
    assert "ORDER BY started_at DESC, monitoring_run_id DESC LIMIT 1" in query.sql  # the currently monitored bundle (D5)
    assert "JOIN monitored_bundle AS b USING (model_bundle_id)" in query.sql
    assert "l.run_date <= c.sim_date" in query.sql and "l.gold_fingerprint = @gold_fingerprint" in query.sql
    assert query.sql.endswith("ORDER BY l.run_date DESC\nLIMIT @limit")


@pytest.mark.parametrize("clock_bound", [True, False])
@pytest.mark.parametrize("fingerprint_filter", [True, False])
def test_published_dates_variant_contract(clock_bound, fingerprint_filter):
    query = q._published_dates_query(clock_bound=clock_bound, fingerprint_filter=fingerprint_filter, limit=7)
    names = {n for n, _, _ in query.parameters}
    for fragment in ("ticks AS (", "monitored_bundle AS (", "clock AS (", "CROSS JOIN clock", "l.run_date <= c.sim_date",
                     "clock_sim_date"):  # fmt: skip
        assert (fragment in query.sql) == clock_bound, fragment
    assert ("succeeded" in names) == clock_bound
    assert ("l.gold_fingerprint = @gold_fingerprint" in query.sql) == fingerprint_filter
    assert ("gold_fingerprint" in names) == fingerprint_filter
    expected = ["run_date", "scoring_run_id", "model_bundle_id", "window_days", "top_n", "n_predictions"]
    expected += ["clock_sim_date"] * clock_bound + ["gold_fingerprint"] * (not fingerprint_filter)
    assert _selected(query.sql) == expected
    assert {"epoch", "limit"} <= names
    if not (clock_bound and fingerprint_filter):
        assert query.name == f"published_dates[clock_bound={clock_bound},fingerprint_filter={fingerprint_filter}]"


def test_call_sheet_uses_the_scorers_window_and_no_run_id_predicate():
    query = q.call_sheet(PUBLISHED)
    start, end = window_bounds(PUBLISHED.run_date, PUBLISHED.window_days)
    params = {n: (t, v) for n, t, v in query.parameters}
    assert params == {
        "as_of_date": ("DATE", date(2023, 10, 30)),
        "discharge_from": ("TIMESTAMP", start.to_pydatetime()),
        "discharge_to": ("TIMESTAMP", end.to_pydatetime()),
    }
    assert params["discharge_from"][1].tzinfo is not None and params["discharge_from"][1] == datetime(2023, 10, 24, tzinfo=UTC)
    assert params["discharge_to"][1] == datetime(2023, 10, 31, tzinfo=UTC)
    assert "scoring_run_id =" not in query.sql and query.sql.endswith("ORDER BY rank")
    assert query == q._call_sheet_variant(PUBLISHED, discharge=q.discharge_window(PUBLISHED), as_of=True)


def test_call_sheet_variants_omit_their_predicates_and_parameters():
    bare = q._call_sheet_variant(PUBLISHED, discharge=None, as_of=False)
    assert "WHERE" not in bare.sql and bare.parameters == ()
    no_discharge = q._call_sheet_variant(PUBLISHED, discharge=None, as_of=True)
    assert "discharge_time" not in no_discharge.sql.split("FROM", 1)[1] and [p[0] for p in no_discharge.parameters] == ["as_of_date"]
    wide = (datetime(1900, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))
    no_as_of = q._call_sheet_variant(PUBLISHED, discharge=wide, as_of=False)
    assert "as_of_date =" not in no_as_of.sql and [p[0] for p in no_as_of.parameters] == ["discharge_from", "discharge_to"]


@pytest.mark.parametrize("bad", [0, -1, 1.5, "10", True])
def test_limits_are_validated(bad):
    for build in (lambda: q.published_dates(limit=bad), lambda: q.monitoring_history(limit=bad), lambda: q.recent_scoring_runs(limit=bad)):
        with pytest.raises(ValueError, match="limit"):
            build()


def test_ops_epoch_is_not_after_the_monitor_epoch():
    assert read_monitoring_config().epoch >= q.OPS_EPOCH == date(2026, 10, 1)


@pytest.mark.parametrize(("dry_run", "use_cache", "cached"), [(False, True, True), (False, False, False), (True, True, False)])
def test_every_job_carries_the_cap_the_label_and_parameters(dry_run, use_cache, cached):
    client = FakeBigQuery(on_query=lambda sql, config, job_id: FakeJob(rows=[{"n": 1}], total_bytes_processed=123))
    query = q.call_sheet(PUBLISHED)
    if dry_run:
        assert q.dry_run(client, query) == 123
    else:
        assert q.run_job(client, query, use_cache=use_cache)[0] == [{"n": 1}]
    (_, sql, config, job_id) = client.calls[0]
    assert sql == query.sql and job_id is None
    assert config.maximum_bytes_billed == MAX_BYTES_BILLED
    assert config.labels == {"component": "dashboard"}
    assert config.dry_run is dry_run and config.use_query_cache is cached
    assert [(p.name, p.type_, p.value) for p in config.query_parameters] == list(query.parameters)


def test_dry_run_never_waits_for_a_result_and_run_returns_rows():
    client = FakeBigQuery(
        on_query=lambda sql, config, job_id: FakeJob(total_bytes_processed=5, raise_on_result=AssertionError("result() called"))
    )
    assert q.dry_run(client, q.monitoring_history()) == 5
    client = FakeBigQuery(on_query=lambda sql, config, job_id: FakeJob(rows=[{"a": 1}, {"a": 2}]))
    assert q.run(client, q.recent_scoring_runs()) == [{"a": 1}, {"a": 2}]


def test_published_date_from_row():
    row = {f: getattr(PUBLISHED, f) for f in q.PublishedDate.__dataclass_fields__} | {"extra": 1}
    assert q.PublishedDate.from_row(row) == PUBLISHED
    with pytest.raises(KeyError):
        q.PublishedDate.from_row({k: v for k, v in row.items() if k != "window_days"})
    with pytest.raises(ValueError, match="window_days"):
        q.PublishedDate.from_row(row | {"window_days": None})
