"""The Streamlit dashboard (PRD v3 FR10-FR19): the honesty strip and heartbeat, then the call sheet, the monitoring O/E chart
and the run history. Run it with `streamlit run scripts/dashboard_app.py` (owner decision S11); importing this module renders
nothing and calls no client. It reads BigQuery only through dashboard.queries (bounded, S16; a per-run circuit breaker, S18),
caches in memory without pickling (S5), and turns every failure into a text panel (FR16, E7). The helper modules are called
as module attributes so tests can patch them. See notes/eg-new-feature/v3-slice3-dashboard-app-2026-11-01.md (section 9).
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from datetime import timedelta
from types import MappingProxyType

import streamlit as st

from readmission_risk import gcp
from readmission_risk.dashboard import charts, chrome, errors, present, queries
from readmission_risk.dashboard import heartbeat as heartbeat_mod
from readmission_risk.dashboard.heartbeat import Heartbeat
from readmission_risk.dashboard.queries import PublishedDate
from readmission_risk.reporting.palette import THEMES, chart_colors
from readmission_risk.warehouse.dashboard_views import (
    REASON_RATE_CUTOFF,
    REASON_RATE_MIN_N,
)

CACHE_TTL = timedelta(hours=3)  # S4
CACHE_MAX_ENTRIES = 64
NO_CLOCK_UNKNOWN = "The simulated date could not be read, so the {what} are not shown (some may be dated after it)."
NO_CLOCK_YET = "No monitoring tick has succeeded yet, so there is no simulated date and no {what} are shown."
MON_NO_BUNDLE_UNKNOWN = (
    "The simulated clock could not be read, so the monitored model bundle is unknown and the chart is not shown."
)
MON_NO_BUNDLE_YET = "No monitoring tick has succeeded yet, so there is no monitored model bundle to chart."
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_HANDLER_MARK = "_readmission_risk_dashboard"
logger = logging.getLogger("readmission_risk.dashboard")


def _ensure_log_handler() -> None:
    """One stderr handler on the dashboard logger (Cloud Run sends stderr to Cloud Logging; the default Formatter appends the
    traceback of exc_info records). Streamlit configures only its own loggers. Idempotent across re-imports."""
    if any(getattr(h, _HANDLER_MARK, False) for h in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.setLevel(logging.WARNING)
    setattr(handler, _HANDLER_MARK, True)
    logger.addHandler(handler)


_ensure_log_handler()
_cache = st.cache_resource(ttl=CACHE_TTL, max_entries=CACHE_MAX_ENTRIES, show_spinner="Reading BigQuery…")


@_cache
def load_published_dates() -> tuple[PublishedDate, ...]:
    return tuple(PublishedDate.from_row(r) for r in queries.run_bounded(gcp.bigquery_client(), queries.published_dates()))


@_cache
def load_call_sheet(published: PublishedDate) -> tuple[MappingProxyType, ...]:
    return present.freeze_rows(queries.run_bounded(gcp.bigquery_client(), queries.call_sheet(published)))


@_cache
def load_monitoring() -> tuple[MappingProxyType, ...]:
    query = queries.monitoring_history(limit=present.MONITORING_LIMIT)
    return present.freeze_rows(queries.run_bounded(gcp.bigquery_client(), query))


@_cache
def load_scoring_runs() -> tuple[MappingProxyType, ...]:
    query = queries.recent_scoring_runs(limit=present.SCORING_RUNS_LIMIT)
    return present.freeze_rows(queries.run_bounded(gcp.bigquery_client(), query))


def fetch(loader, *args, breaker: dict) -> tuple[object | None, errors.FailureClass | None]:
    """The loader's value, or (None, failure class) with the failure logged server-side only. S18: once a fetch of this run
    was unreachable, later fetches skip BigQuery and report unreachable too."""
    if breaker["open"]:
        logger.warning("dashboard query %s skipped (BigQuery unreachable earlier in this run)", loader.__name__)
        return None, "unreachable"
    try:
        return loader(*args), None
    except Exception as exc:  # FR16: every query failure becomes a text panel, AssertionError included
        logger.warning("dashboard query %s failed (%s)", loader.__name__, type(exc).__name__, exc_info=True)
        failure = errors.classify(exc)
        if failure == "unreachable":
            breaker["open"] = True
        return None, failure


def guarded[T](name: str, body: Callable[[], T], fallback: T = None) -> T:
    """E7: a rendering fault in one part shows a fixed text there and leaves the other parts working."""
    try:
        return body()
    except Exception as exc:  # E7: no traceback reaches the browser
        logger.warning("dashboard part %s failed (%s)", name, type(exc).__name__, exc_info=True)
        st.warning(errors.RENDER_FAILED.format(view=name))
        return fallback


def _panel(kind, *, what: str, still: str) -> None:
    text = errors.panel_text(kind, what=what, still=still)
    if kind == "empty":
        st.info(text)
    else:
        st.warning(text)


def _notice() -> None:
    st.markdown(chrome.SYNTHETIC_NOTICE)
    st.info(chrome.honesty_strip())


def _heartbeat_line(hb: Heartbeat, m_fail) -> None:
    st.markdown(heartbeat_mod.heartbeat_line(hb))
    if m_fail:
        _panel(m_fail, what="The heartbeat", still="The call sheet may still work.")


_SHEET_COLUMNS = {
    "Risk score (%)": st.column_config.NumberColumn(format="%.1f"),
    "Age (years)": st.column_config.NumberColumn(format="%.0f"),
    "Length of stay (days)": st.column_config.NumberColumn(format="%.1f"),
    "Discharged (UTC)": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
}
_RUN_COLUMNS = {"Run": st.column_config.LinkColumn(display_text="run")}


def _sheet_table(rows) -> None:
    st.dataframe(present.call_sheet_frame(rows), hide_index=True, column_config=_SHEET_COLUMNS)


def _call_sheet(hb: Heartbeat, shown: list, breaker: dict) -> None:
    still = "The monitoring and run-history tabs may still work."
    dates, fail = fetch(load_published_dates, breaker=breaker)
    if fail:
        _panel(fail, what="The list of published run dates", still=still)
        return
    if not dates:
        _panel("empty", what="The list of published run dates", still=still)
        return
    picked = st.selectbox("Run date (simulated)", options=tuple(p.run_date for p in dates), format_func=lambda d: f"{d:%Y-%m-%d}")
    chosen = next(p for p in dates if p.run_date == picked)
    st.caption(f"Simulated today: {dates[0].clock_sim_date:%Y-%m-%d}")
    if hb.sim_date is not None and dates[0].clock_sim_date != hb.sim_date:
        st.caption(
            f"The date list and the heartbeat disagree on the simulated date ({dates[0].clock_sim_date:%Y-%m-%d} vs "
            f"{hb.sim_date:%Y-%m-%d}); they were read at different times and should agree within 3 hours."
        )
    rows, fail = fetch(load_call_sheet, chosen, breaker=breaker)
    what = f"The call sheet for {chosen.run_date:%Y-%m-%d}"
    if fail:
        _panel(fail, what=what, still="Other run dates and tabs may still work.")
        return
    if not rows:
        _panel("empty", what=what, still="Other run dates and tabs may still work.")
        return
    for note in present.sheet_warnings(rows, chosen):
        st.caption(note)
    top, rest = present.split_top_n(rows)
    st.subheader(f"Call this week: top {len(top)}")
    if top:
        _sheet_table(top)
    else:
        st.caption("No discharge is flagged Top-N for this run date.")
    if rest:
        with st.expander(f"Rest of the ranked list ({len(rest)})"):
            _sheet_table(rest)
    st.caption(
        f"Reason's historical readmission rate: the share of settled discharges before {REASON_RATE_CUTOFF:%Y-%m-%d} with the "
        "same admission reason that had an unplanned readmission within 30 days (planned chemotherapy/radiation stays "
        f"excluded); withheld under {REASON_RATE_MIN_N} discharges."
    )
    shown.extend(r["model_bundle_id"] for r in rows)


def _monitoring(monitoring, fail, hb: Heartbeat, theme) -> None:
    if fail:
        _panel(fail, what="The monitoring record", still="The call sheet may still work.")
        return
    if hb.state == "unknown":
        st.info(MON_NO_BUNDLE_UNKNOWN)
        return
    if hb.monitored_bundle is None:
        st.info(MON_NO_BUNDLE_YET)
        return
    points = present.monitoring_points(monitoring, bundle=hb.monitored_bundle)
    if points.empty:
        _panel("empty", what="The monitoring record", still="The call sheet may still work.")
        return
    st.altair_chart(charts.oe_chart(points, chart_colors(theme)), theme=None, width="stretch")
    st.caption(charts.LEGEND_TEXT)
    k1 = int((points["verdict"].isin(charts.MUTED_VERDICTS) & points["plotted"]).sum())
    k2 = int((~points["plotted"]).sum())
    parts = []
    if k1:
        parts.append(
            f"{present.count_text(k1, 'week has', 'weeks have')} insufficient data or an incomplete cohort (hollow grey; "
            "the alert band does not apply)"
        )
    if k2:
        parts.append(f"{present.count_text(k2, 'week has', 'weeks have')} no O/E value (△ on the bottom edge; see the table)")
    if parts:
        st.caption(" · ".join(parts))
    st.dataframe(present.monitoring_table(points), hide_index=True)


def _history(runs, r_fail, monitoring, m_fail, hb: Heartbeat) -> None:
    st.subheader("Scoring runs")
    still = "The monitoring-run table may still work."
    if r_fail:
        _panel(r_fail, what="The scoring-run history", still=still)
    elif hb.state == "unknown":
        st.info(NO_CLOCK_UNKNOWN.format(what="scoring runs"))
    elif hb.sim_date is None:
        st.info(NO_CLOCK_YET.format(what="scoring runs"))
    else:
        kept, hidden = present.visible_scoring_runs(runs, clock=hb.sim_date)
        if kept:
            st.dataframe(present.scoring_runs_table(kept), hide_index=True, column_config=_RUN_COLUMNS)
        else:
            _panel("empty", what="The scoring-run history", still=still)
        if hidden:
            st.caption(
                f"{present.count_text(hidden, 'run is', 'runs are')} not shown: dated after the simulated date or scored on "
                "another gold table."
            )
        if len(runs) == present.SCORING_RUNS_LIMIT:
            st.caption(f"Only the {present.SCORING_RUNS_LIMIT} most recent scoring runs are loaded.")
    st.subheader("Monitoring ticks")
    still = "The scoring-run table may still work."
    if m_fail:
        _panel(m_fail, what="The monitoring-run history", still=still)
    elif hb.state == "unknown":
        st.info(NO_CLOCK_UNKNOWN.format(what="monitoring ticks"))
    elif hb.sim_date is None:
        st.info(NO_CLOCK_YET.format(what="monitoring ticks"))
    else:
        kept, hidden = present.visible_monitoring_runs(monitoring, clock=hb.sim_date, bundle=hb.monitored_bundle)
        if kept:
            st.dataframe(present.monitoring_runs_table(kept), hide_index=True, column_config=_RUN_COLUMNS)
        else:
            _panel("empty", what="The monitoring-run history", still=still)
        if hidden:
            st.caption(
                f"{present.count_text(hidden, 'tick is', 'ticks are')} not shown: dated after the simulated date or of "
                "another model bundle."
            )
        if len(kept) > present.TICKS_SHOWN:
            st.caption(f"Showing the {present.TICKS_SHOWN} most recent of {len(kept)} ticks.")
        if len(monitoring) == present.MONITORING_LIMIT:
            st.caption(f"Only the {present.MONITORING_LIMIT} most recent ticks are loaded.")


def _theme():
    try:
        return THEMES.get(st.context.theme.type or "light", THEMES["light"])
    except Exception:  # noqa: BLE001 -- st.context can be unavailable; light is always readable (opaque chart background)
        return THEMES["light"]


def main() -> None:
    st.set_page_config(page_title="Readmission call sheet · synthetic demo", layout="wide")
    theme = _theme()
    breaker = {"open": False}
    guarded("The notice", _notice)
    monitoring, m_fail = fetch(load_monitoring, breaker=breaker)
    runs, r_fail = fetch(load_scoring_runs, breaker=breaker)
    hb = guarded(
        "The heartbeat",
        lambda: heartbeat_mod.heartbeat(monitoring, runs, now=heartbeat_mod.wall_now()),
        fallback=Heartbeat.unavailable(),
    )
    guarded("The heartbeat line", lambda: _heartbeat_line(hb, m_fail))
    call_tab, monitoring_tab, history_tab = st.tabs(["Call sheet", "Monitoring", "Run history"])
    shown_bundles: list = []
    with call_tab:
        guarded("The call sheet", lambda: _call_sheet(hb, shown_bundles, breaker))
    with monitoring_tab:
        guarded("The monitoring view", lambda: _monitoring(monitoring, m_fail, hb, theme))
    with history_tab:
        guarded("The run history", lambda: _history(runs, r_fail, monitoring, m_fail, hb))
    st.divider()
    guarded("The footer", lambda: st.caption(chrome.footer(commit=chrome.git_commit(os.environ), bundles=shown_bundles)))
