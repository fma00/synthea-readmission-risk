"""The dashboard's scripted BigQuery (v3 slice 3, Verification V-A): the four fixed queries routed by their exact SQL, each
answered from an independent literal fixture (never computed from another answer or by the code under test). The fixture is
the design note's table: ticks m0-m5, scoring runs r0-r5 plus r-future and r-v1, three published dates."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from readmission_risk.dashboard import queries as q
from readmission_risk.dashboard.present import MONITORING_LIMIT, SCORING_RUNS_LIMIT
from readmission_risk.warehouse.bigquery import SERVING_GOLD_FINGERPRINT
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob

B = "b" * 64  # the monitored bundle
OTHER = "c" * 64  # another bundle
OTHER_GOLD = "d" * 64
CLOCK = date(2023, 10, 30)
FIXED_NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
ANCHOR = datetime(2026, 10, 26, 0, 17, tzinfo=UTC)  # m5: a Monday, the newest row and the clock tick
QUERY_NAMES = ("published_dates", "call_sheet", "monitoring_history", "recent_scoring_runs")


def _tick(i, started, cadence, trigger, sim_date, verdict, oe, bundle, run):
    ratio, low, high = (None, None, None) if oe is None else oe
    return {
        "monitoring_run_id": f"m{i}", "status": "succeeded", "failed_stage": None, "started_at": started,
        "finished_at": started + timedelta(minutes=3), "sim_date": sim_date, "cadence": cadence, "trigger": trigger,
        "github_run_url": f"https://github.com/fma00/synthea-readmission-risk/actions/runs/{1000 + i}", "git_commit": "0" * 40,
        "git_dirty": False, "github_run_attempt": 1, "history_tests_passed": True, "scoring_run_id": run,
        "model_bundle_id": bundle, "gold_fingerprint": SERVING_GOLD_FINGERPRINT, "verdict": verdict, "n_matured": 1200,
        "observed": None if oe is None else 21, "expected": None if oe is None else 20.6, "oe_ratio": ratio,
        "oe_ci_low": low, "oe_ci_high": high, "min_positives_floor": 20, "band_low": 0.8, "band_high": 1.25, "error_type": None,
    }  # fmt: skip


def monitoring_rows() -> list[dict]:
    return [
        _tick(0, datetime(2026, 10, 15, 10, 0, tzinfo=UTC), "dispatch", "workflow_dispatch", date(2023, 9, 18), "incomplete_cohort", (0.80, 0.40, 1.40), OTHER, "r0"),
        _tick(1, datetime(2026, 10, 20, 9, 0, tzinfo=UTC), "burn_in", "workflow_dispatch", date(2023, 10, 2), "incomplete_cohort", (0.90, 0.50, 1.50), B, "r1"),
        _tick(2, datetime(2026, 10, 20, 10, 0, tzinfo=UTC), "burn_in", "workflow_dispatch", date(2023, 10, 9), "insufficient_data", (1.20, 0.40, 2.80), B, "r2"),
        _tick(3, datetime(2026, 10, 20, 11, 0, tzinfo=UTC), "burn_in", "workflow_dispatch", date(2023, 10, 16), "incomplete_cohort", None, B, "r3"),
        _tick(4, datetime(2026, 10, 21, 9, 0, tzinfo=UTC), "burn_in", "workflow_dispatch", date(2023, 10, 23), "alert", (1.90, 1.30, 2.60), B, "r4"),
        _tick(5, ANCHOR, "weekly", "schedule", CLOCK, "ok", (1.02, 0.80, 1.30), B, "r5"),
    ]  # fmt: skip


def _run(run_id, started, run_date, *, fingerprint=SERVING_GOLD_FINGERPRINT):
    return {
        "scoring_run_id": run_id, "status": "succeeded", "started_at": started, "finished_at": started + timedelta(minutes=2),
        "run_date": run_date, "trigger": "workflow_dispatch", "github_run_url": None, "git_commit": "0" * 40, "git_dirty": False,
        "python_version": "3.14.7", "gold_fingerprint": fingerprint, "champion_sha256": "e" * 64, "model_bundle_id": B,
        "model_name": "logistic_regression", "top_n": 2, "window_days": 7, "n_predictions": 5, "n_top_n": 2,
        "history_tests_passed": True, "predictions_tests_passed": True, "published": True, "data_test_failure": None,
        "error_type": None, "gold_fingerprint_definition": "spark-rowhash-v1",
    }  # fmt: skip


def scoring_rows() -> list[dict]:
    ticks = monitoring_rows()
    rows = [_run(t["scoring_run_id"], t["started_at"] - timedelta(minutes=5), t["sim_date"]) for t in ticks]
    rows.append(_run("r-future", datetime(2026, 10, 18, 9, 0, tzinfo=UTC), date(2025, 6, 30)))
    rows.append(_run("r-v1", datetime(2026, 10, 12, 9, 0, tzinfo=UTC), date(2025, 7, 31), fingerprint=OTHER_GOLD))
    return sorted(rows, key=lambda r: r["started_at"], reverse=True)


PUBLISHED_RUNS = {date(2023, 10, 30): "r5", date(2023, 10, 23): "r4", date(2023, 10, 16): "r3"}
SENTINELS = {"is_readmitted": 777777, "label_horizon": datetime(1999, 9, 9, tzinfo=UTC), "gender": "SENTINEL-GENDER",
             "race": "SENTINEL-RACE"}  # fmt: skip
WITHHELD_REASON = "195967001"


def sheet_rows(day: date, run_id: str, *, n: int = 5, top: int = 2) -> list[dict]:
    """Rank 1 shown rate; rank 2 the withheld reason (n = 9); rank 3 no history; rank 4 NULL prior_emergency_count; rank 5 shown."""
    reasons = [("84114007", "Heart failure", 120, 0.081), (WITHHELD_REASON, "Asthma", 9, None), ("10509002", "Bronchitis", None, None),
               ("84114007", "Heart failure", 120, 0.081), ("44054006", "Diabetes", 64, 0.031)]  # fmt: skip
    rows = []
    for rank in range(1, n + 1):
        code, description, history_n, rate = reasons[(rank - 1) % len(reasons)]
        stop = datetime(day.year, day.month, day.day, 12, tzinfo=UTC) - timedelta(days=rank)
        rows.append({
            "as_of_date": day, "rank": rank, "is_top_n": rank <= top, "top_n": top, "patient_id": f"p-{day:%m%d}-{rank}",
            "encounter_id": f"e-{day:%m%d}-{rank}", "discharge_time": stop, "admission_reason_code": code,
            "admission_reason_description": description, "risk_score": 0.2 - rank / 100, "age_at_index_years": 60.0 + rank,
            "length_of_stay_days": 1.5 * rank, "prior_inpatient_count": rank, "prior_emergency_count": None if rank == 4 else 2,
            "active_condition_count": 3 + rank, "reason_history_n": history_n, "reason_history_rate": rate,
            "model_bundle_id": B, "scoring_run_id": run_id, "scoring_window_days": 7, **SENTINELS,
        })  # fmt: skip
    return rows


def published_row(day: date, run_id: str, *, n: int = 5, top_n: int = 2, clock: date = CLOCK) -> dict:
    return {"run_date": day, "scoring_run_id": run_id, "model_bundle_id": B, "window_days": 7, "top_n": top_n,
            "n_predictions": n, "clock_sim_date": clock}  # fmt: skip


@dataclass
class World:
    """`fail[name] = (exc, site)`: site "query" raises from client.query, "result" from job.result. `rows[name]` overrides an
    answer (for call_sheet: a dict keyed by as_of_date)."""

    sql_clock: date = CLOCK
    monitoring: list = field(default_factory=monitoring_rows)
    scoring: list = field(default_factory=scoring_rows)
    published: list = field(default_factory=list)
    sheets: dict = field(default_factory=dict)
    fail: dict = field(default_factory=dict)
    client: FakeBigQuery = field(init=False)

    def __post_init__(self):
        if not self.published:
            self.published = [published_row(day, run, clock=self.sql_clock) for day, run in PUBLISHED_RUNS.items()]
        for day, run in PUBLISHED_RUNS.items():
            self.sheets.setdefault(day, sheet_rows(day, run))
        self.client = FakeBigQuery(on_query=self.on_query)

    def sql_of(self) -> dict[str, str]:
        sample = q.PublishedDate(CLOCK, "r5", B, 7, 2, 5, CLOCK)
        return {
            q.published_dates().sql: "published_dates",
            q.call_sheet(sample).sql: "call_sheet",
            q.monitoring_history(limit=MONITORING_LIMIT).sql: "monitoring_history",
            q.recent_scoring_runs(limit=SCORING_RUNS_LIMIT).sql: "recent_scoring_runs",
        }

    def answer(self, name: str, config) -> list:
        if name == "published_dates":
            return [dict(r) for r in self.published]
        if name == "call_sheet":
            day = next(p.value for p in config.query_parameters if p.name == "as_of_date")
            return [dict(r) for r in self.sheets.get(day, [])]
        if name == "monitoring_history":
            return [dict(r) for r in sorted(self.monitoring, key=lambda r: r["started_at"], reverse=True)]
        return [dict(r) for r in self.scoring]

    def on_query(self, sql, config, job_id):
        name = self.sql_of().get(sql)
        assert name is not None, f"unexpected dashboard SQL: {sql[:80]!r}"
        if name in self.fail:
            exc, site = self.fail[name]
            if site == "query":
                raise exc
            return FakeJob(allow_timeout=True, raise_on_result=exc)
        return FakeJob(rows=self.answer(name, config), allow_timeout=True)

    def queried(self) -> list[str]:
        names = self.sql_of()
        return [names[c[1]] for c in self.client.calls if c[0] == "query"]
