"""The live query check (v3 slice 2, Verification V-C) against a scripted fake BigQuery: every query is routed by its exact SQL,
so each check's pass, fail and error path and the dependency rules are exercised without GCP."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pandas as pd
import pytest
from google.api_core import exceptions as api

from readmission_risk.dashboard import queries as q
from readmission_risk.models.data import prepare_features
from readmission_risk.warehouse import dashboard_query_check as qc
from readmission_risk.warehouse.bigquery import (
    MAX_BYTES_BILLED,
    SERVING_GOLD_FINGERPRINT,
)
from readmission_risk.warehouse.dashboard_views import reason_rates
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob
from tests.warehouse.gold_fixture import gold_frame

CLOCK = date(2023, 10, 30)
RUN = "run-clock"
V1_DATE, FUTURE_DATE = date(2025, 7, 31), date(2025, 6, 30)


def _gold() -> pd.DataFrame:
    """The slice-1 fixture's columns, UTC-aware like the loader's output: 3 call-sheet discharges in the clock's window and
    40 settled pre-2023 rows (reason 84114007: 31 rows, so its rate is shown; reason 195967001: 9 rows, withheld)."""
    base = gold_frame().iloc[[2]]
    rows = []
    for i in range(43):
        row = base.copy()
        row["encounter_id"] = f"e{i}"
        row["patient_id"] = f"p{i}"
        if i < 3:
            stop = datetime(2023, 10, 25 + i, 12, tzinfo=UTC)
            row["admission_reason_code"] = ["84114007", "195967001", None][i]
        else:
            stop = datetime(2021, 3, 1, 12, tzinfo=UTC)
            row["admission_reason_code"] = "84114007" if i < 34 else "195967001"
            row["is_readmitted"] = 1 if i % 4 == 0 else 0
        row["index_stop"] = stop
        row["index_start"] = stop - pd.Timedelta(hours=30 + i)
        row["label_horizon"] = stop + pd.Timedelta(days=30)
        row["age_at_index_years"] = 50.0 + i
        rows.append(row)
    frame = pd.concat(rows, ignore_index=True)
    for col in ("index_start", "index_stop", "label_horizon"):
        frame[col] = pd.to_datetime(frame[col], utc=True)
    return frame


def _sheet_rows(gold: pd.DataFrame) -> list[dict]:
    rates = reason_rates(gold).set_index("reason_key")
    los = prepare_features(gold)["length_of_stay_days"]
    rows = []
    for rank, i in enumerate(range(3), start=1):
        g = gold.iloc[i]
        key = g["admission_reason_code"] if g["admission_reason_code"] is not None else "MISSING"
        n = int(rates.loc[key, "n"]) if key in rates.index else None
        rate = rates.loc[key, "rate"] if key in rates.index else None
        rows.append({
            "as_of_date": CLOCK, "rank": rank, "is_top_n": rank <= 2, "top_n": 2, "patient_id": g["patient_id"],
            "encounter_id": g["encounter_id"], "discharge_time": g["index_stop"].to_pydatetime(),
            "admission_reason_code": g["admission_reason_code"], "admission_reason_description": "x", "risk_score": 0.1,
            "age_at_index_years": g["age_at_index_years"], "length_of_stay_days": float(los.iloc[i]),
            **{c: int(g[c]) for c in ("prior_inpatient_count", "prior_emergency_count", "active_condition_count")},
            "reason_history_n": n, "reason_history_rate": None if rate is None or pd.isna(rate) else float(rate),
            "model_bundle_id": "b", "scoring_run_id": RUN, "scoring_window_days": 7,
        })  # fmt: skip
    return rows


def _published(run_date, run_id, fingerprint=SERVING_GOLD_FINGERPRINT, n=3) -> dict:
    return {"run_date": run_date, "scoring_run_id": run_id, "model_bundle_id": "b", "window_days": 7, "top_n": 2,
            "n_predictions": n, "gold_fingerprint": fingerprint}  # fmt: skip


@dataclass
class World:
    """Scripted answers keyed by query SQL. `published` = the clock-free, fingerprint-free list (each row's latest run)."""

    gold: pd.DataFrame = field(default_factory=_gold)
    published: list[dict] = field(default_factory=list)
    clock: date | None = CLOCK
    sheet: list[dict] | None = None
    dry_bytes: dict[str, int] = field(default_factory=dict)
    raise_on: dict[str, BaseException] = field(default_factory=dict)
    v1_count: int = 0
    filter_required: bool = True

    def __post_init__(self):
        if not self.published:
            self.published = [_published(V1_DATE, "run-v1", "205c4c64", 44), _published(FUTURE_DATE, "run-future"),
                              _published(CLOCK, RUN), _published(date(2023, 10, 23), "run-prev")]  # fmt: skip
        if self.sheet is None:
            self.sheet = _sheet_rows(self.gold)

    def _lists(self, clock_bound, fingerprint_filter):
        rows = sorted(self.published, key=lambda r: r["run_date"], reverse=True)
        if fingerprint_filter:
            rows = [r for r in rows if r["gold_fingerprint"] == SERVING_GOLD_FINGERPRINT]
        if clock_bound:
            rows = [] if self.clock is None else [r | {"clock_sim_date": self.clock} for r in rows if r["run_date"] <= self.clock]
        if fingerprint_filter:
            rows = [{k: v for k, v in r.items() if k != "gold_fingerprint"} for r in rows]
        return rows

    def client(self) -> FakeBigQuery:
        newest = q.PublishedDate(CLOCK, RUN, "b", 7, 2, 3, CLOCK)
        kinds = {
            q.published_dates().sql: "published",
            q._published_dates_query(clock_bound=False, fingerprint_filter=True, limit=q.PUBLISHED_DATES_LIMIT).sql: "clock_free",
            q._published_dates_query(clock_bound=False, fingerprint_filter=False, limit=q.PUBLISHED_DATES_LIMIT).sql: "unfiltered",
            q.call_sheet(newest).sql: "call_sheet",
            q._call_sheet_variant(newest, discharge=None, as_of=True).sql: "no_discharge",
            q._call_sheet_variant(newest, discharge=qc.WIDE_DISCHARGE, as_of=False).sql: "no_as_of",
            q.monitoring_history().sql: "monitoring_history",
            q.recent_scoring_runs().sql: "recent_scoring_runs",
            qc._count_on(V1_DATE).sql: "count",
        }

        def on_query(sql, config, job_id):
            kind = kinds[sql]
            if config.dry_run:
                if kind == "call_sheet" and any(p.name == "discharge_from" and p.value == qc.WIDE_DISCHARGE[0] for p in config.query_parameters):
                    kind = "call_sheet_wide"
                if kind in ("no_discharge", "no_as_of") and self.filter_required:
                    raise api.BadRequest("Cannot query over table 'serving.gold' without a filter over column(s) 'index_stop'")
                if f"dry:{kind}" in self.raise_on:
                    raise self.raise_on[f"dry:{kind}"]
                return FakeJob(total_bytes_processed=self.dry_bytes.get(kind, {"call_sheet_wide": 9000}.get(kind, 1000)))
            assert config.use_query_cache is False, "the check's real queries bypass the result cache"
            if kind in self.raise_on:
                raise self.raise_on[kind]
            rows = {
                "published": lambda: self._lists(True, True),
                "clock_free": lambda: self._lists(False, True),
                "unfiltered": lambda: self._lists(False, False),
                "call_sheet": lambda: self.sheet,
                "count": lambda: [{"n": self.v1_count}],
            }[kind]()
            return FakeJob(rows=rows, total_bytes_billed=10485760, cache_hit=False)

        return FakeBigQuery(on_query=on_query)


def _run(world: World) -> tuple[dict[str, qc.CheckResult], qc.QueryCheckReport]:
    report = qc.run_query_checks(world.client(), gold=world.gold)
    return {r.check: r for r in report.results}, report


def _outcomes(results) -> dict[str, str]:
    return {k: r.outcome for k, r in results.items()}


def test_everything_passes_and_is_measured():
    results, report = _run(World())
    assert set(_outcomes(results).values()) == {"pass"}, {k: r.detail for k, r in results.items() if r.outcome != "pass"}
    assert set(results) == {"Q0", "Q0b:i", f"Q0b:ii:{V1_DATE}", "Q0b:iii", "Q1:published_dates", "Q1:call_sheet",
                            "Q1:monitoring_history", "Q1:recent_scoring_runs", "Q2", "Q3:discharge", "Q3:as_of", "Q4"}  # fmt: skip
    m = report.measurements
    assert m["clock_sim_date"] == m["newest_published_date"] == CLOCK.isoformat() and m["n_published_dates"] == 2
    assert m["excluded_other_fingerprint_dates"] == {V1_DATE.isoformat(): 44}
    assert m["excluded_future_dates"] == [FUTURE_DATE.isoformat()]
    assert m["dry_run_bytes_call_sheet"] == 1000 and m["dry_run_bytes_call_sheet_wide"] == 9000
    assert m["call_sheet_rows"] == 3 and m["q4_bytes_billed"] == 10485760 and m["q4_cache_hit"] is False
    assert m["rows_without_reason_history"] == 1  # the NULL-reason discharge: no settled MISSING rows in the fixture
    assert qc.exit_code(report.results) == 0


def test_q0_empty_or_raising_skips_everything_else():
    for world in (World(clock=None), World(raise_on={"published": RuntimeError("boom")})):
        results, _ = _run(world)
        assert results["Q0"].outcome in ("fail", "error")
        assert {k: r.detail for k, r in results.items() if k != "Q0"} == {
            n: "skipped: Q0 returned no parseable row" for n in ("Q0b", "Q1", "Q2", "Q3", "Q4")
        }


def test_q0_access_denied_is_a_fail_not_an_error():
    results, report = _run(World(raise_on={"published": api.Forbidden("403 Access Denied")}))
    assert results["Q0"].outcome == "fail" and qc.exit_code(report.results) == 3


def test_q0_newest_not_the_clock_fails_but_the_others_still_run():
    world = World(clock=date(2023, 11, 6))  # the clock moved on but its tick published nothing yet
    results, _ = _run(world)
    assert results["Q0"].outcome == "fail" and "!= the clock" in results["Q0"].detail
    assert results["Q1:call_sheet"].outcome == "pass"


def test_q0_limit_reached_fails(monkeypatch):
    monkeypatch.setattr(q, "PUBLISHED_DATES_LIMIT", 2)
    results, _ = _run(World())
    assert results["Q0"].outcome == "fail" and "limit" in results["Q0"].detail


@pytest.mark.parametrize("index", [2, 3], ids=["newest listed", "second listed"])
def test_q0_malformed_row_fails_and_no_partial_list_is_used(index):
    world = World()
    world.published[index] = world.published[index] | {"window_days": None}
    results, report = _run(world)
    assert results["Q0"].outcome == "fail" and "malformed" in results["Q0"].detail
    assert {k: r.detail for k, r in results.items() if k != "Q0"} == {
        n: "skipped: Q0 returned no parseable row" for n in ("Q0b", "Q1", "Q2", "Q3", "Q4")
    }
    assert "n_published_dates" not in report.measurements


def test_q0b_controls_need_something_to_exclude():
    results, _ = _run(World(published=[_published(CLOCK, RUN)]))
    assert results["Q0b:i"].outcome == "fail" and "nothing to exclude" in results["Q0b:i"].detail
    assert results["Q0b:iii"].outcome == "fail" and "no future date" in results["Q0b:iii"].detail


def test_q0b_ii_fails_when_other_gold_rows_reach_the_call_sheet():
    results, _ = _run(World(v1_count=44))
    assert results[f"Q0b:ii:{V1_DATE}"].outcome == "fail"


def test_q1_over_the_cap_fails_and_a_raising_call_sheet_dry_run_skips_only_q2():
    results, _ = _run(World(dry_bytes={"monitoring_history": MAX_BYTES_BILLED + 1}))
    assert results["Q1:monitoring_history"].outcome == "fail" and results["Q2"].outcome == "pass"
    results, _ = _run(World(raise_on={"dry:call_sheet": RuntimeError("boom")}))
    assert results["Q1:call_sheet"].outcome == "error"
    assert results["Q2"].detail == "skipped: Q1:call_sheet produced no byte count"
    assert results["Q3:discharge"].outcome == "pass" and results["Q4"].outcome == "pass"


def test_q2_narrow_not_below_wide_fails():
    results, _ = _run(World(dry_bytes={"call_sheet": 9000}))
    assert results["Q2"].outcome == "fail"


def test_q3_accepted_is_a_fail_and_another_error_is_an_error():
    results, _ = _run(World(filter_required=False))
    assert results["Q3:discharge"].outcome == results["Q3:as_of"].outcome == "fail"
    world = World()
    world.filter_required = False
    world.raise_on["dry:no_as_of"] = api.BadRequest("Syntax error")
    results, _ = _run(world)
    assert results["Q3:as_of"].outcome == "error"


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda rows: rows[0].update(scoring_run_id="other"), "F10"),
        (lambda rows: rows.pop(), "!= n_predictions"),
        (lambda rows: rows[1].update(rank=5), "ranks"),
        (lambda rows: rows[2].update(is_top_n=True), "top-N"),
        (lambda rows: rows[0].update(age_at_index_years=None), "NULL"),
        (lambda rows: rows[0].update(prior_inpatient_count=999), "prior_inpatient_count"),
        (lambda rows: rows[0].update(length_of_stay_days=rows[0]["length_of_stay_days"] + 1e-6), "length_of_stay_days"),
        (lambda rows: rows[0].update(reason_history_rate=0.5), "reason_history_rate"),
        (lambda rows: rows[0].update(reason_history_n=1), "reason_history_n"),
        (lambda rows: rows[0].update(discharge_time=datetime(2023, 10, 26, tzinfo=UTC)), "discharge_time"),
        (lambda rows: rows[0].update(encounter_id="unknown"), "absent from the local gold"),
    ],
)
def test_q4_failures(mutate, expected):
    world = World()
    mutate(world.sheet)
    results, _ = _run(world)
    assert results["Q4"].outcome == "fail" and expected in results["Q4"].detail, results["Q4"].detail


def test_q4_a_reason_without_history_is_measured_not_failed():
    world = World()
    gold = world.gold
    gold.loc[gold.index < 3, "admission_reason_code"] = "999999"  # never seen before the cutoff
    world.sheet = _sheet_rows(gold)
    results, report = _run(world)
    assert results["Q4"].outcome == "pass"
    assert report.measurements["rows_without_reason_history"] == 3


def test_exit_code_precedence():
    fail, error, ok = qc.CheckResult("a", "fail", ""), qc.CheckResult("b", "error", ""), qc.CheckResult("c", "pass", "")
    assert qc.exit_code([ok]) == 0 and qc.exit_code([ok, error]) == 5 and qc.exit_code([error, fail]) == 3
