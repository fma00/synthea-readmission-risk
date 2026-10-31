"""Live proof of the dashboard's fixed queries (v3 slice 2, PRD FR8, M2), run as dashboard-runtime: the date list and the
simulated clock (Q0), positive controls for the fingerprint and clock rules (Q0b), dry-run bytes under the cap (Q1), partition
pruning (Q2), the filter requirement surviving the view (Q3), and the clock's call sheet against the local gold (Q4). See
notes/eg-new-feature/v3-slice2-authorized-views-2026-10-31.md (Interfaces section 6) for every rule used here.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pandas as pd

from readmission_risk.dashboard import queries as q

from .bigquery import DASHBOARD_DATASET, MAX_BYTES_BILLED, quoted, table_id
from .dashboard_views import reason_rates
from .gold_access_check import is_denied

WIDE_DISCHARGE = (datetime(1900, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))
FEATURES = ("age_at_index_years", "prior_inpatient_count", "prior_emergency_count", "active_condition_count")
NON_NULL = (*FEATURES, "length_of_stay_days", "discharge_time")
LOS_TOLERANCE_DAYS = 1e-9
RATE_TOLERANCE = 1e-12


@dataclass(frozen=True)
class CheckResult:
    check: str
    outcome: str  # "pass" | "fail" | "error"
    detail: str


@dataclass
class QueryCheckReport:
    results: list[CheckResult] = field(default_factory=list)
    measurements: dict = field(default_factory=dict)


class _Failed(Exception):
    """A check's assertion is false (outcome "fail")."""


def _guarded(report: QueryCheckReport, name: str, fn: Callable[[], str]):
    """Runs one check: its returned detail is a pass; _Failed or an access refusal is a fail; anything else is an error. The
    `try` covers the client.query call itself (a dry run raises there, not in .result())."""
    try:
        detail = fn()
    except _Failed as exc:
        report.results.append(CheckResult(name, "fail", str(exc)[:500]))
        return False
    except Exception as exc:  # noqa: BLE001 -- every outcome is a result
        outcome = "fail" if is_denied(exc) else "error"
        report.results.append(CheckResult(name, outcome, f"{type(exc).__name__}: {exc}"[:500]))
        return False
    report.results.append(CheckResult(name, "pass", detail))
    return True


def _skip(report: QueryCheckReport, name: str, prerequisite: str) -> None:
    report.results.append(CheckResult(name, "error", f"skipped: {prerequisite}"))


def _real(client, query: q.DashboardQuery, report: QueryCheckReport, key: str) -> list[dict]:
    """A real query without the result cache, so the billed bytes are real; records billed bytes and cache_hit."""
    rows, job = q.run_job(client, query, use_cache=False)
    report.measurements[f"{key}_bytes_billed"] = job.total_bytes_billed
    report.measurements[f"{key}_cache_hit"] = job.cache_hit
    return rows


def _iso(value) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def _q0(client, report: QueryCheckReport) -> list[q.PublishedDate]:
    parsed: list[q.PublishedDate] = []

    def run() -> str:
        rows = _real(client, q.published_dates(), report, "q0")
        if not rows:
            raise _Failed("no published date up to the clock (no succeeded tick, or nothing published)")
        try:
            rows_parsed = [q.PublishedDate.from_row(r) for r in rows]
        except (KeyError, ValueError) as exc:  # nothing is kept: the later checks never run on a partial list
            raise _Failed(f"malformed published row: {exc}") from exc
        parsed.extend(rows_parsed)
        newest = parsed[0]
        report.measurements |= {
            "clock_sim_date": newest.clock_sim_date.isoformat(),
            "newest_published_date": newest.run_date.isoformat(),
            "n_published_dates": len(parsed),
        }
        if len(parsed) >= q.PUBLISHED_DATES_LIMIT:
            raise _Failed(f"{len(parsed)} dates: the limit {q.PUBLISHED_DATES_LIMIT} is reached (design F7)")
        late = [p.run_date.isoformat() for p in parsed if p.run_date > p.clock_sim_date]
        if late:
            raise _Failed(f"dates after the clock listed: {late}")
        if newest.run_date != newest.clock_sim_date:
            raise _Failed(f"newest date {newest.run_date} != the clock {newest.clock_sim_date} (M2: the tick's own date)")
        return f"{len(parsed)} dates, newest {newest.run_date} = the clock"

    _guarded(report, "Q0", run)
    return parsed


def _count_on(published_run_date: date) -> q.DashboardQuery:
    view = quoted(table_id(DASHBOARD_DATASET, "v_call_sheet"))
    sql = (
        f"SELECT COUNT(*) AS n FROM {view}\n"  # noqa: S608 -- static identifier; values are parameters
        "WHERE as_of_date = @as_of_date AND discharge_time >= @discharge_from AND discharge_time < @discharge_to"
    )
    return q.DashboardQuery(
        "call_sheet_count",
        sql,
        (("as_of_date", "DATE", published_run_date), ("discharge_from", "TIMESTAMP", WIDE_DISCHARGE[0]),
         ("discharge_to", "TIMESTAMP", WIDE_DISCHARGE[1])),
    )  # fmt: skip


def _q0b(client, report: QueryCheckReport, listed: list[q.PublishedDate]) -> None:
    listed_dates = {p.run_date for p in listed}
    clock = listed[0].clock_sim_date
    other: dict[date, int] = {}

    def step_i() -> str:
        unfiltered = _real(
            client, q._published_dates_query(clock_bound=False, fingerprint_filter=False, limit=q.PUBLISHED_DATES_LIMIT),
            report, "q0b_i_unfiltered",
        )  # fmt: skip
        filtered = _real(
            client, q._published_dates_query(clock_bound=False, fingerprint_filter=True, limit=q.PUBLISHED_DATES_LIMIT),
            report, "q0b_i_filtered",
        )  # fmt: skip
        other.update({r["run_date"]: r["n_predictions"] for r in unfiltered if r["gold_fingerprint"] != q.SERVING_GOLD_FINGERPRINT})
        report.measurements["excluded_other_fingerprint_dates"] = {_iso(d): n for d, n in sorted(other.items())}
        if not other:
            raise _Failed("no published date with another gold fingerprint: the fingerprint control has nothing to exclude")
        leaked = sorted(_iso(d) for d in other if d in {r["run_date"] for r in filtered} or d in listed_dates)
        if leaked:
            raise _Failed(f"other-fingerprint dates listed: {leaked}")
        return f"{len(other)} other-fingerprint date(s) excluded"

    _guarded(report, "Q0b:i", step_i)
    for day in sorted(other):

        def step_ii(day=day) -> str:
            rows = _real(client, _count_on(day), report, f"q0b_ii_{day.isoformat()}")
            if rows[0]["n"] != 0:
                raise _Failed(f"v_call_sheet shows {rows[0]['n']} row(s) on {day}")
            return f"0 call-sheet rows on {day} ({other[day]} predictions of another gold)"

        _guarded(report, f"Q0b:ii:{day.isoformat()}", step_ii)

    def step_iii() -> str:
        rows = _real(
            client, q._published_dates_query(clock_bound=False, fingerprint_filter=True, limit=q.PUBLISHED_DATES_LIMIT),
            report, "q0b_iii",
        )  # fmt: skip
        future = sorted(r["run_date"] for r in rows if r["run_date"] > clock)
        report.measurements["excluded_future_dates"] = [d.isoformat() for d in future]
        if not future:
            raise _Failed("no future date to exclude: the clock control has nothing to exclude")
        leaked = [d.isoformat() for d in future if d in listed_dates]
        if leaked:
            raise _Failed(f"dates after the clock listed: {leaked}")
        return f"{len(future)} date(s) after the clock {clock} excluded"

    _guarded(report, "Q0b:iii", step_iii)


def _q1(client, report: QueryCheckReport, newest: q.PublishedDate) -> int | None:
    sheet_bytes = None
    for query in (q.published_dates(), q.call_sheet(newest), q.monitoring_history(), q.recent_scoring_runs()):

        def run(query=query) -> str:
            nonlocal sheet_bytes
            processed = q.dry_run(client, query)
            report.measurements[f"dry_run_bytes_{query.name}"] = processed
            if query.name == "call_sheet":
                sheet_bytes = processed
            if processed is None or processed > MAX_BYTES_BILLED:
                raise _Failed(f"{processed} bytes > cap {MAX_BYTES_BILLED}")
            return f"{processed} bytes"

        _guarded(report, f"Q1:{query.name}", run)
    return sheet_bytes


def _q2(client, report: QueryCheckReport, newest: q.PublishedDate, narrow: int) -> None:
    def run() -> str:
        wide = q.dry_run(client, q._call_sheet_variant(newest, discharge=WIDE_DISCHARGE, as_of=True))
        report.measurements["dry_run_bytes_call_sheet_wide"] = wide
        if not narrow < wide:
            raise _Failed(f"narrow {narrow} bytes not < wide {wide} bytes: the discharge filter does not prune gold")
        return f"narrow {narrow} < wide {wide} bytes"

    _guarded(report, "Q2", run)


def _q3(client, report: QueryCheckReport, newest: q.PublishedDate) -> None:
    from google.api_core import exceptions as api

    variants = {
        "Q3:discharge": q._call_sheet_variant(newest, discharge=None, as_of=True),
        "Q3:as_of": q._call_sheet_variant(newest, discharge=q.discharge_window(newest), as_of=False),
    }
    for name, query in variants.items():
        try:
            q.dry_run(client, query)
        except api.BadRequest as exc:
            if "without a filter" in str(exc):
                report.results.append(CheckResult(name, "pass", "refused without a partition filter"))
            else:
                report.results.append(CheckResult(name, "error", f"BadRequest: {exc}"[:500]))
            continue
        except Exception as exc:  # noqa: BLE001 -- recorded
            report.results.append(CheckResult(name, "error", f"{type(exc).__name__}: {exc}"[:500]))
            continue
        report.results.append(
            CheckResult(name, "fail", "accepted without the partition filter: the requirement does not survive the view (F8)")
        )


def _equal_float(a, b, tolerance: float) -> bool:
    if a is None or b is None or (isinstance(b, float) and math.isnan(b)):
        return (a is None) == (b is None or (isinstance(b, float) and math.isnan(b)))
    return abs(float(a) - float(b)) <= tolerance


def _q4(client, report: QueryCheckReport, newest: q.PublishedDate, gold: pd.DataFrame, los: pd.Series) -> None:
    def run() -> str:
        rows = _real(client, q.call_sheet(newest), report, "q4")
        report.measurements["call_sheet_rows"] = len(rows)
        problems = []
        runs = sorted({r["scoring_run_id"] for r in rows})
        if runs != [newest.scoring_run_id] and rows:
            problems.append(f"rows of run(s) {runs}, listed run {newest.scoring_run_id} (F10)")
        if len(rows) != newest.n_predictions:
            problems.append(f"{len(rows)} rows != n_predictions {newest.n_predictions}")
        if [r["rank"] for r in rows] != list(range(1, len(rows) + 1)):
            problems.append("ranks are not 1..n")
        top = sum(1 for r in rows if r["is_top_n"])
        if top != min(newest.top_n, len(rows)):
            problems.append(f"{top} top-N rows != min(top_n {newest.top_n}, {len(rows)})")
        nulls = sorted({c for r in rows for c in NON_NULL if r[c] is None})
        if nulls:
            problems.append(f"NULL in {nulls}")
        rates = reason_rates(gold).set_index("reason_key")
        by_id = gold.set_index("encounter_id")
        mismatched: dict[str, int] = {}
        without_history = 0
        for r in rows:
            if r["encounter_id"] not in by_id.index:
                mismatched["encounter_id (absent from the local gold)"] = mismatched.get("encounter_id (absent from the local gold)", 0) + 1
                continue
            g = by_id.loc[r["encounter_id"]]
            checks = {
                "discharge_time": pd.Timestamp(r["discharge_time"]) == g["index_stop"],
                **{c: r[c] == g[c] for c in FEATURES},
                "length_of_stay_days": _equal_float(r["length_of_stay_days"], los.loc[r["encounter_id"]], LOS_TOLERANCE_DAYS),
            }
            key = r["admission_reason_code"] if r["admission_reason_code"] is not None else "MISSING"
            if key in rates.index:
                checks["reason_history_n"] = r["reason_history_n"] == int(rates.loc[key, "n"])
                checks["reason_history_rate"] = _equal_float(r["reason_history_rate"], rates.loc[key, "rate"], RATE_TOLERANCE)
            else:
                checks["reason_history_n"] = r["reason_history_n"] is None
                checks["reason_history_rate"] = r["reason_history_rate"] is None
            if r["reason_history_n"] is None:
                without_history += 1
            for column, ok in checks.items():
                if not ok:
                    mismatched[column] = mismatched.get(column, 0) + 1
        report.measurements["rows_without_reason_history"] = without_history
        if mismatched:
            problems.append(f"parity with the local gold fails: {mismatched} (column -> rows)")
        if problems:
            raise _Failed("; ".join(problems))
        return f"{len(rows)} rows on {newest.run_date}, parity with the local gold"

    _guarded(report, "Q4", run)


def run_query_checks(client, *, gold: pd.DataFrame) -> QueryCheckReport:
    """Every check's result plus the measurements. Dependencies (design, Interfaces 6): Q0b, Q1, Q3 and Q4 need Q0's rows (they
    use the newest); Q2 also needs Q1's call-sheet byte count. A fail never skips anything."""
    from readmission_risk.models.data import (
        prepare_features,  # the check only: the dashboard itself never imports models
    )

    report = QueryCheckReport()
    listed = _q0(client, report)
    if not listed:
        for name in ("Q0b", "Q1", "Q2", "Q3", "Q4"):
            _skip(report, name, "Q0 returned no parseable row")
        return report
    newest = listed[0]
    _q0b(client, report, listed)
    narrow = _q1(client, report, newest)
    if narrow is None:
        _skip(report, "Q2", "Q1:call_sheet produced no byte count")
    else:
        _q2(client, report, newest, narrow)
    _q3(client, report, newest)
    los = pd.Series(prepare_features(gold)["length_of_stay_days"].to_numpy(), index=gold["encounter_id"].to_numpy())
    _q4(client, report, newest, gold, los)
    return report


def exit_code(results: list[CheckResult]) -> int:
    """3 if any check failed (wins), else 5 if any errored, else 0."""
    if any(r.outcome == "fail" for r in results):
        return 3
    if any(r.outcome == "error" for r in results):
        return 5
    return 0
