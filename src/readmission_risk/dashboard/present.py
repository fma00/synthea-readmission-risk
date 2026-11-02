"""Pure formatting for the dashboard (no Streamlit): the call-sheet frame, the monitoring points and tables, the run-history
tables, and the short texts that may enter markdown. Rows arrive as read-only mappings (owner decision S5) and are never
mutated. See notes/eg-new-feature/v3-slice3-dashboard-app-2026-11-01.md (Interfaces section 5).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from types import MappingProxyType

import pandas as pd

from readmission_risk.monitoring.freshness import cadence_label
from readmission_risk.warehouse.bigquery import SERVING_GOLD_FINGERPRINT
from readmission_risk.warehouse.dashboard_views import REASON_RATE_MIN_N

from .queries import PublishedDate

GITHUB_RUN_PREFIX = "https://github.com/fma00/synthea-readmission-risk/actions/runs/"
_RUN_URL_RE = re.compile(re.escape(GITHUB_RUN_PREFIX) + r"\d+(/attempts/\d+)?")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
STATUS_TEXT = {"succeeded": "✅ succeeded", "failed": "❌ failed", "data_tests_failed": "⚠️ data tests failed", "refused": "⛔ refused"}
VERDICT_TEXT = {"ok": "✅ ok", "alert": "🚨 alert", "insufficient_data": "⚪ insufficient data", "incomplete_cohort": "⚪ incomplete cohort"}
SCORING_RUNS_LIMIT = 50  # app.py passes both limits to the queries explicitly (the queries' own defaults; a test pins them)
MONITORING_LIMIT = 1000
TICKS_SHOWN = 50

CALL_SHEET_DISPLAY = (
    "Rank", "Top-N", "Discharged (UTC)", "Admission reason", "Risk score (%)", "Age (years)", "Length of stay (days)",
    "Prior inpatient stays", "Prior emergency visits", "Active conditions", "Reason's historical readmission rate",
    "Patient ID (synthetic)",
)  # fmt: skip
POINT_COLUMNS = (
    "sim_date", "sim_week", "verdict", "verdict_label", "oe_ratio", "oe_ci_low", "oe_ci_high", "band_low", "band_high",
    "observed", "expected", "min_positives_floor", "cadence_label", "plotted",
)  # fmt: skip
_COUNTS = (
    ("Prior inpatient stays", "prior_inpatient_count"), ("Prior emergency visits", "prior_emergency_count"),
    ("Active conditions", "active_condition_count"),
)  # fmt: skip


def _missing(value) -> bool:
    return value is None or value is pd.NA or value is pd.NaT or (isinstance(value, float) and math.isnan(value))


def freeze_rows(rows: Iterable[Mapping]) -> tuple[MappingProxyType, ...]:
    """Read-only copies, safe to share between sessions through the app's resource cache."""
    return tuple(MappingProxyType(dict(row)) for row in rows)


def count_text(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular}" if n == 1 else f"{n} {plural}"


def short_id(value) -> str:
    """The first 12 characters + "…" of a 64-hex id, else "unknown" (safe inside a markdown code span)."""
    return value[:12] + "…" if isinstance(value, str) and _HEX64_RE.fullmatch(value) else "unknown"


def status_word(status) -> str:
    """A known status key, "other" or "-": safe in markdown, unlike the raw value."""
    if _missing(status):
        return "-"
    return status if status in STATUS_TEXT else "other"


def status_text(status) -> str:  # tables only
    return "-" if _missing(status) else STATUS_TEXT.get(status, f"❔ {status}")


def verdict_text(verdict) -> str:  # tables only
    return "-" if _missing(verdict) else VERDICT_TEXT.get(verdict, f"❔ {verdict}")


def run_link(url) -> str | None:
    return url if isinstance(url, str) and _RUN_URL_RE.fullmatch(url) else None


def rate_text(n, rate) -> str:
    """The reason's historical rate as text; a withheld or missing rate is never rendered as a number."""
    if _missing(n):
        return "no history"
    if n < REASON_RATE_MIN_N:
        return f"withheld (n = {n} < {REASON_RATE_MIN_N})"
    if _missing(rate):
        return f"rate unavailable (n = {n:,})"
    return f"{rate:.1%} of {n:,}"


def reason_text(code, description) -> str:
    has_code, has_description = not _missing(code), not _missing(description)
    if has_code and has_description:
        return f"{description} ({code})"
    if has_code:
        return f"(no description) ({code})"
    if has_description:
        return f"{description} (no code)"
    return "(no reason recorded)"


def _float(value) -> float:
    return math.nan if _missing(value) else float(value)


def call_sheet_frame(rows: Sequence[Mapping]) -> pd.DataFrame:
    """The call-sheet table, columns exactly CALL_SHEET_DISPLAY, sorted by rank; built column by column with explicit dtypes
    whatever the row count; only the listed source columns are read."""
    ordered = sorted(rows, key=lambda r: r["rank"])
    columns = {
        "Rank": pd.Series([int(r["rank"]) for r in ordered], dtype="int64"),
        "Top-N": pd.Series(["✅ call this week" if r["is_top_n"] is True else "-" for r in ordered], dtype="object"),
        "Discharged (UTC)": pd.Series(pd.to_datetime([r["discharge_time"] for r in ordered], utc=True), dtype="datetime64[ns, UTC]"),
        "Admission reason": pd.Series(
            [reason_text(r["admission_reason_code"], r["admission_reason_description"]) for r in ordered], dtype="object"
        ),
        "Risk score (%)": pd.Series([_float(r["risk_score"]) * 100 for r in ordered], dtype="float64"),
        "Age (years)": pd.Series([_float(r["age_at_index_years"]) for r in ordered], dtype="float64"),
        "Length of stay (days)": pd.Series([_float(r["length_of_stay_days"]) for r in ordered], dtype="float64"),
    }
    for display, source in _COUNTS:
        columns[display] = pd.Series([None if _missing(r[source]) else int(r[source]) for r in ordered], dtype="Int64")
    columns["Reason's historical readmission rate"] = pd.Series(
        [rate_text(r["reason_history_n"], r["reason_history_rate"]) for r in ordered], dtype="object"
    )
    columns["Patient ID (synthetic)"] = pd.Series([str(r["patient_id"]) for r in ordered], dtype="object")
    return pd.DataFrame({name: columns[name].reset_index(drop=True) for name in CALL_SHEET_DISPLAY})


def split_top_n(rows: Iterable[Mapping]) -> tuple[list[Mapping], list[Mapping]]:
    ordered = sorted(rows, key=lambda r: r["rank"])
    return [r for r in ordered if r["is_top_n"] is True], [r for r in ordered if r["is_top_n"] is not True]


def sheet_warnings(rows: Sequence[Mapping], published: PublishedDate) -> list[str]:
    """Visible consistency notes (slice 2's F10); ints only, no BigQuery string enters the text."""
    notes = []
    if len(rows) != published.n_predictions:
        notes.append(f"{count_text(len(rows), 'row is', 'rows are')} shown, but the run recorded {published.n_predictions}.")
    k = sum(1 for r in rows if r["scoring_run_id"] != published.scoring_run_id)
    if k > 0:
        notes.append(f"{count_text(k, 'row comes', 'rows come')} from another scoring run than the listed one.")
    t, m = sum(1 for r in rows if r["is_top_n"] is True), min(published.top_n, len(rows))
    if t != m:
        notes.append(f"{count_text(t, 'row is', 'rows are')} flagged Top-N; the run's Top-N is {m}.")
    return notes


def _order_key(row: Mapping, id_column: str) -> tuple:
    return (row["started_at"], row.get(id_column) or "")


def monitoring_points(rows: Sequence[Mapping], *, bundle: str | None) -> pd.DataFrame:
    """The chart's weeks: the newest succeeded tick of `bundle` per sim_date (verdict and sim_date present), by sim_date."""
    newest: dict[date, Mapping] = {}
    for row in rows:
        if bundle is None or row.get("status") != "succeeded" or row.get("model_bundle_id") != bundle:
            continue
        if _missing(row.get("verdict")) or _missing(row.get("sim_date")):
            continue
        current = newest.get(row["sim_date"])
        if current is None or _order_key(row, "monitoring_run_id") > _order_key(current, "monitoring_run_id"):
            newest[row["sim_date"]] = row
    chosen = [newest[day] for day in sorted(newest)]
    days = [datetime(d.year, d.month, d.day, tzinfo=UTC) for d in (r["sim_date"] for r in chosen)]
    frame = pd.DataFrame({
        "sim_date": pd.Series(pd.to_datetime(days, utc=True), dtype="datetime64[ns, UTC]"),
        "sim_week": pd.Series([f"{r['sim_date']:%Y-%m-%d}" for r in chosen], dtype="object"),
        "verdict": pd.Series([r["verdict"] for r in chosen], dtype="object"),
        "verdict_label": pd.Series([verdict_text(r["verdict"]) for r in chosen], dtype="object"),
        **{c: pd.Series([_float(r.get(c)) for r in chosen], dtype="float64")
           for c in ("oe_ratio", "oe_ci_low", "oe_ci_high", "band_low", "band_high")},
        "observed": pd.Series([None if _missing(r.get("observed")) else int(r["observed"]) for r in chosen], dtype="Int64"),
        "expected": pd.Series([_float(r.get("expected")) for r in chosen], dtype="float64"),
        "min_positives_floor": pd.Series(
            [None if _missing(r.get("min_positives_floor")) else int(r["min_positives_floor"]) for r in chosen], dtype="Int64"
        ),
        "cadence_label": pd.Series([cadence_label(r.get("cadence"), r.get("trigger")) for r in chosen], dtype="object"),
    })  # fmt: skip
    frame["plotted"] = frame["oe_ratio"].notna().astype(bool)
    return frame[list(POINT_COLUMNS)]


def _num(value, digits: int) -> str:
    return "-" if _missing(value) else f"{float(value):.{digits}f}"


def monitoring_table(points: pd.DataFrame) -> pd.DataFrame:
    """The chart's text alternative (FR13), newest first; the value columns follow ledger.py's formatting."""
    rows = []
    for p in points.iloc[::-1].to_dict("records"):
        if _missing(p["oe_ratio"]):
            ratio = "-"
        else:
            ratio = f"{_num(p['oe_ratio'], 3)} [{_num(p['oe_ci_low'], 3)}, {_num(p['oe_ci_high'], 3)}]"
        observed = "-" if _missing(p["observed"]) else f"{p['observed']} / {_num(p['expected'], 1)}"
        floor = "-" if _missing(p["min_positives_floor"]) else str(int(p["min_positives_floor"]))
        rows.append([p["sim_week"], p["verdict_label"], ratio, observed, floor, p["cadence_label"]])
    columns = ["Simulated week", "Verdict", "O/E [95% CI]", "Observed / expected", "Positives floor", "Cadence"]
    return pd.DataFrame(rows, columns=columns, dtype="object")


def visible_scoring_runs(rows: Sequence[Mapping], *, clock: date | None) -> tuple[list[Mapping], int]:
    """S12: hides PUBLISHED runs dated after the clock (every dated published run without one) and runs scored on another
    gold; keeps the order. Unpublished runs publish nothing, so they always show: a failed weekly attempt is dated clock + 7
    days (the clock moves only on success) and is what an operator needs to see."""
    kept = []
    for row in rows:
        run_date, fingerprint = row.get("run_date"), row.get("gold_fingerprint")
        if row.get("published") is True and not _missing(run_date) and (clock is None or run_date > clock):
            continue
        if not _missing(fingerprint) and fingerprint != SERVING_GOLD_FINGERPRINT:
            continue
        kept.append(row)
    return kept, len(rows) - len(kept)


def visible_monitoring_runs(rows: Sequence[Mapping], *, clock: date | None, bundle: str | None) -> tuple[list[Mapping], int]:
    """S12: hides SUCCEEDED ticks dated after the clock (every dated succeeded tick without one) and ticks of another bundle;
    keeps the order. Failed attempts always show (dated clock + 7 days, since the clock moves only on success)."""
    kept = []
    for row in rows:
        sim_date, row_bundle = row.get("sim_date"), row.get("model_bundle_id")
        if row.get("status") == "succeeded" and not _missing(sim_date) and (clock is None or sim_date > clock):
            continue
        if not _missing(row_bundle) and (bundle is None or row_bundle != bundle):
            continue
        kept.append(row)
    return kept, len(rows) - len(kept)


def _wall(value) -> str:
    return "-" if _missing(value) else pd.Timestamp(value).tz_convert("UTC").strftime("%Y-%m-%d %H:%M")


def _day(value) -> str:
    return "-" if _missing(value) else f"{value:%Y-%m-%d}"


def _text(value) -> str:
    return "-" if _missing(value) else str(value)


def _tests(value) -> str:
    return "-" if _missing(value) else ("passed" if value is True else "FAILED")


def _newest_first(rows: Iterable[Mapping], id_column: str) -> list[Mapping]:
    return sorted(rows, key=lambda r: _order_key(r, id_column), reverse=True)


def scoring_runs_table(rows: Sequence[Mapping]) -> pd.DataFrame:
    records = [
        [_wall(r["started_at"]), _day(r.get("run_date")), status_text(r.get("status")), _text(r.get("trigger")),
         _tests(r.get("history_tests_passed")), _tests(r.get("predictions_tests_passed")),
         "-" if _missing(r.get("published")) else ("yes" if r["published"] is True else "no"),
         _text(r.get("data_test_failure")), _text(r.get("error_type")), run_link(r.get("github_run_url"))]
        for r in _newest_first(rows, "scoring_run_id")
    ]  # fmt: skip
    columns = ["Ran at (wall clock, UTC)", "Run date", "Status", "Trigger", "History tests", "Prediction tests", "Published",
               "Data-test failure", "Error type", "Run"]  # fmt: skip
    return pd.DataFrame(records, columns=columns, dtype="object")


def monitoring_runs_table(rows: Sequence[Mapping]) -> pd.DataFrame:
    records = [
        [_wall(r["started_at"]), _day(r.get("sim_date")), status_text(r.get("status")), verdict_text(r.get("verdict")),
         cadence_label(r.get("cadence"), r.get("trigger")), _text(r.get("trigger")), _tests(r.get("history_tests_passed")),
         _text(r.get("error_type")), run_link(r.get("github_run_url"))]
        for r in _newest_first(rows, "monitoring_run_id")[:TICKS_SHOWN]
    ]  # fmt: skip
    columns = ["Ran at (wall clock, UTC)", "Simulated date", "Status", "Verdict", "Cadence", "Trigger", "History tests",
               "Error type", "Run"]  # fmt: skip
    return pd.DataFrame(records, columns=columns, dtype="object")
