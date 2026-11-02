"""The Streamlit app under AppTest against the scripted BigQuery (v3 slice 3, Verification V-A). The E2 unpickle trap stays
armed (conftest.py), so every cache hit here also proves the app never unpickles (owner decision S5)."""

from __future__ import annotations

import importlib
import logging
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import streamlit as st
from google.api_core import exceptions as api
from streamlit import config as st_config
from streamlit.testing.v1 import AppTest

from readmission_risk.dashboard import app, errors, present
from readmission_risk.dashboard import heartbeat as heartbeat_mod
from readmission_risk.warehouse.bigquery import (
    MAX_BYTES_BILLED,
    SERVING_GOLD_FINGERPRINT,
)
from tests.dashboard.fake_world import (
    ANCHOR,
    FIXED_NOW,
    PUBLISHED_RUNS,
    World,
    monitoring_rows,
    published_row,
    sheet_rows,
)
from tests.dashboard.test_streamlit_config import EXPECTED

REPO = Path(__file__).resolve().parents[2]
ENTRYPOINT = str(REPO / "scripts" / "dashboard_app.py")
CLOCK = date(2023, 10, 30)
LOADERS = {"monitoring_history": "load_monitoring", "recent_scoring_runs": "load_scoring_runs",
           "published_dates": "load_published_dates", "call_sheet": "load_call_sheet"}  # fmt: skip
FETCH_ORDER = ("monitoring_history", "recent_scoring_runs", "published_dates", "call_sheet")
FRESH = (
    "✅ **Fresh** · simulated date 2023-10-30 · last tick ran 2026-10-26 00:17 UTC (succeeded) · data tests passed · "
    "bundle `bbbbbbbbbbbb…`"
)
FALLBACK_WORDS = ("could not be displayed", "could not be read", "was not loaded", "nothing to show yet")


# ---------------------------------------------------------------- fixtures


@pytest.fixture
def expected_log() -> Counter:
    """A test declares the dashboard log records it expects as {(message, exception class or None): count}."""
    return Counter()


def check_log(records, expected: Counter) -> list[str]:
    """The log guard's verdict: problems with the dashboard logger's records (empty when they are exactly as expected)."""
    mine = [r for r in records if r.name.startswith("readmission_risk.dashboard")]
    problems = [r.getMessage() for r in mine if r.exc_info and issubclass(r.exc_info[0], AssertionError)]
    found = Counter((r.getMessage(), r.exc_info[0] if r.exc_info else None) for r in mine)
    if found != expected:
        problems.append(f"log records {dict(found)} != expected {dict(expected)}")
    return problems


@pytest.fixture(autouse=True)
def _log_guard(caplog, expected_log):
    caplog.set_level(logging.WARNING)
    yield
    # At teardown caplog.records holds only the teardown phase: read the setup and call phases explicitly.
    assert check_log(caplog.get_records("setup") + caplog.get_records("call"), expected_log) == []


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch, tmp_path):
    monkeypatch.chdir(REPO)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.streamlit/config.toml (re-resolved through HOME on every reparse)
    monkeypatch.delenv("READMISSION_RISK_GIT_COMMIT", raising=False)
    st_config.get_config_options(force_reparse=True)
    for section, keys in EXPECTED.items():
        for key, value in keys.items():
            assert st_config.get_option(f"{section}.{key}") == value, f"{section}.{key}"
    yield


@pytest.fixture
def world(request, monkeypatch, _no_real_gcp) -> World:
    world = World()
    if not request.node.get_closest_marker("no_fake_world"):
        monkeypatch.setattr("readmission_risk.gcp.bigquery_client", lambda: world.client)
    monkeypatch.setattr("readmission_risk.dashboard.heartbeat.wall_now", lambda: FIXED_NOW)
    st.cache_resource.clear()
    yield world
    st.cache_resource.clear()


def run(**kwargs) -> AppTest:
    at = AppTest.from_file(ENTRYPOINT, default_timeout=30).run()
    assert st_config.get_option("client.showErrorDetails") == "none"
    return at


def texts(at: AppTest) -> list[str]:
    out = []
    for kind in ("markdown", "caption", "info", "warning", "error", "success", "subheader", "title", "header"):
        out += [str(e.value) for e in getattr(at, kind)]
    out += [str(e.message) for e in at.exception]
    out += [t.label for t in at.tabs] + [e.label for e in at.expander]
    for box in at.selectbox:
        out += [box.label, *map(str, box.options)]
    out += [d.value.to_string() for d in at.dataframe]
    out += [c.proto.spec for c in at.get("vega_lite_chart")]
    return out


def warnings_of(at) -> list[str]:
    return [w.value for w in at.warning]


def infos_of(at) -> list[str]:
    return [i.value for i in at.info]


def captions_of(at) -> list[str]:
    return [c.value for c in at.caption]


# ---------------------------------------------------------------- 1. the happy path


def test_happy_path(world):
    at = run()
    assert [m.value for m in at.markdown] == ["**Synthetic data · demo only · not for clinical use.**", FRESH]
    assert len(at.info) == 1 and app.chrome.DEMO_NOTICE in at.info[0].value
    assert at.info[0].value.endswith("**Model card:** in preparation (published with v3.0).")
    assert [t.label for t in at.tabs] == ["Call sheet", "Monitoring", "Run history"]
    box = at.selectbox[0]
    assert box.options == ["2023-10-30", "2023-10-23", "2023-10-16"] and box.value == CLOCK
    captions = captions_of(at)
    assert "Simulated today: 2023-10-30" in captions
    top = at.dataframe[0].value
    assert tuple(top.columns) == present.CALL_SHEET_DISPLAY and list(top["Rank"]) == [1, 2]
    assert top.loc[1, "Reason's historical readmission rate"] == "withheld (n = 9 < 30)"
    assert [s.value for s in at.subheader][:1] == ["Call this week: top 2"]
    assert len(at.get("vega_lite_chart")) == 1
    scoring = at.dataframe[3].value
    assert list(scoring["Run date"]) == ["2023-10-30", "2023-10-23", "2023-10-16", "2023-10-09", "2023-10-02", "2023-09-18"]
    assert "2 runs are not shown: dated after the simulated date or scored on another gold table." in captions
    ticks = at.dataframe[4].value
    assert list(ticks["Simulated date"]) == ["2023-10-30", "2023-10-23", "2023-10-16", "2023-10-09", "2023-10-02"]
    assert "1 tick is not shown: dated after the simulated date or of another model bundle." in captions
    assert (
        "2 weeks have insufficient data or an incomplete cohort (hollow grey; the alert band does not apply) · "
        "1 week has no O/E value (△ on the bottom edge; see the table)"
    ) in captions
    footer = captions[-1]
    assert footer.startswith("Model bundle `bbbbbbbbbbbb…` · ")
    assert f"Gold `{SERVING_GOLD_FINGERPRINT[:12]}…`" in footer and "Image commit unknown (not a deployed image)" in footer
    everything = "\n".join(texts(at))
    for sentinel in ("777777", "SENTINEL-GENDER", "SENTINEL-RACE", "1999-09-09"):
        assert sentinel not in everything
    assert not any("disagree on the simulated date" in c for c in captions)
    assert not any(c.endswith(("shown, but the run recorded 5.", "than the listed one.")) for c in captions)
    assert at.exception == [] or len(at.exception) == 0
    assert at.warning == [] or len(at.warning) == 0
    assert not any(word in everything for word in FALLBACK_WORDS)


# ---------------------------------------------------------------- 2. jobs and the cache


def test_every_job_is_capped_labelled_bounded_and_cached(world):
    at = run()
    assert world.queried() == ["monitoring_history", "recent_scoring_runs", "published_dates", "call_sheet"]
    assert set(world.client.kinds()) == {"query"}
    for call, kwargs in zip(world.client.calls, world.client.query_kwargs, strict=True):
        config = call[2]
        assert config.maximum_bytes_billed == MAX_BYTES_BILLED and config.labels == {"component": "dashboard"}
        assert not config.dry_run
        assert kwargs["job_retry"] is None and kwargs["timeout"] == 10.0 and kwargs["retry"]._timeout == 10.0
    at.run()
    assert len(world.queried()) == 4  # cache hit with the unpickle trap armed: nothing is unpickled
    at.selectbox[0].select(date(2023, 10, 23)).run()
    assert world.queried()[4:] == ["call_sheet"]
    last = world.client.calls[-1][2]
    assert next(p.value for p in last.query_parameters if p.name == "as_of_date") == date(2023, 10, 23)
    at.selectbox[0].select(CLOCK).run()
    assert len(world.queried()) == 5


# ---------------------------------------------------------------- 3. every failure class on every query


def _bytes_cap():
    return api.BadRequest("SECRET-123", errors=[{"reason": "bytesBilledLimitExceeded", "message": "SECRET-123"}])


def _quota():
    return api.Forbidden("SECRET-123", errors=[{"reason": "quotaExceeded", "message": "SECRET-123"}])


def _down():
    return api.ServiceUnavailable("SECRET-123")


FAILURES = [("bytes_cap", _bytes_cap, "result"), ("quota", _quota, "result"), ("unreachable", _down, "query"),
            ("unreachable", _down, "result")]  # fmt: skip
PANELS = {
    "monitoring_history": [("The heartbeat", "The call sheet may still work."),
                           ("The monitoring record", "The call sheet may still work."),
                           ("The monitoring-run history", "The scoring-run table may still work.")],
    "recent_scoring_runs": [("The scoring-run history", "The monitoring-run table may still work.")],
    "published_dates": [("The list of published run dates", "The monitoring and run-history tabs may still work.")],
    "call_sheet": [("The call sheet for 2023-10-30", "Other run dates and tabs may still work.")],
}  # fmt: skip


def _expected_panels(name: str, kind: str) -> tuple[list[str], list[str]]:
    """(the warnings, the loaders S18 skips) for a failure of `name`."""
    panels = [errors.panel_text(kind, what=w, still=s) for w, s in PANELS[name]]
    skipped = []
    if kind == "unreachable":
        later = FETCH_ORDER[FETCH_ORDER.index(name) + 1 :]
        skipped = [n for n in later if n != "call_sheet"]  # the call sheet is not fetched once the dates failed
        if name == "monitoring_history":
            panels.insert(3, errors.panel_text(kind, what=PANELS["recent_scoring_runs"][0][0], still=PANELS["recent_scoring_runs"][0][1]))
        panels += [errors.panel_text(kind, what=w, still=s) for n in skipped if n != "recent_scoring_runs" for w, s in PANELS[n]]
    return panels, skipped


@pytest.mark.parametrize("name", FETCH_ORDER)
@pytest.mark.parametrize(("kind", "make", "site"), FAILURES, ids=["bytes_cap", "quota", "down@query", "down@result"])
def test_each_failure_shows_only_its_panels(world, expected_log, name, kind, make, site):
    world.fail[name] = (make(), site)
    exc_class = type(make())
    panels, skipped = _expected_panels(name, kind)
    expected_log[(f"dashboard query {LOADERS[name]} failed ({exc_class.__name__})", exc_class)] = 1
    for skipped_name in skipped:
        expected_log[(f"dashboard query {LOADERS[skipped_name]} skipped (BigQuery unreachable earlier in this run)", None)] = 1
    at = run()
    assert sorted(warnings_of(at)) == sorted(panels)
    assert len(at.exception) == 0
    everything = "\n".join(texts(at))
    assert "SECRET-123" not in everything and exc_class.__name__ not in everything and "could not be displayed" not in everything
    if kind == "unreachable":
        assert len(world.queried()) == FETCH_ORDER.index(name) + 1
    if name == "monitoring_history":
        assert at.markdown[1].value == "❔ **Heartbeat unavailable**"
        if kind != "unreachable":
            assert app.NO_CLOCK_UNKNOWN.format(what="scoring runs") in infos_of(at)
    if name == "recent_scoring_runs":
        assert "data tests unknown" in at.markdown[1].value
    if name in ("monitoring_history", "recent_scoring_runs") and kind != "unreachable":
        assert len(at.dataframe) >= 1  # the call sheet still renders


# ---------------------------------------------------------------- 4. empty answers


def test_no_published_date(world):
    world.published = []
    at = run()
    assert errors.panel_text("empty", what="The list of published run dates",
                             still="The monitoring and run-history tabs may still work.") in infos_of(at)  # fmt: skip


def test_no_monitoring_tick_at_all(world):
    world.monitoring, world.published = [], []  # as the SQL's clock CROSS JOIN would
    at = run()
    assert at.markdown[1].value == "⏸️ **No monitoring tick yet**"
    infos = infos_of(at)
    assert app.MON_NO_BUNDLE_YET in infos
    assert app.NO_CLOCK_YET.format(what="scoring runs") in infos and app.NO_CLOCK_YET.format(what="monitoring ticks") in infos
    assert len(at.warning) == 0


def test_no_scoring_run(world):
    world.scoring = []
    at = run()
    assert errors.panel_text("empty", what="The scoring-run history", still="The monitoring-run table may still work.") in infos_of(at)


def test_monitoring_branch_four_is_defensive(world, monkeypatch):
    empty = present.monitoring_points([], bundle=None)  # computed before the patch replaces the function
    monkeypatch.setattr(present, "monitoring_points", lambda rows, *, bundle: empty)
    at = run()
    assert errors.panel_text("empty", what="The monitoring record", still="The call sheet may still work.") in infos_of(at)


# ---------------------------------------------------------------- 5-8


def test_a_failure_is_not_cached(world, expected_log):
    world.fail["published_dates"] = (_down(), "query")
    expected_log[("dashboard query load_published_dates failed (ServiceUnavailable)", api.ServiceUnavailable)] = 1
    at = run()
    assert len(at.selectbox) == 0
    del world.fail["published_dates"]
    at.run()
    assert len(at.selectbox) == 1


@pytest.mark.parametrize(
    ("now", "head"),
    [(ANCHOR + timedelta(days=15), "⛔ **Stale since 2026-11-09**"), (heartbeat_mod.late_after(ANCHOR), "⚠️ **Late**"),
     (heartbeat_mod.late_after(ANCHOR) - timedelta(microseconds=1), "✅ **Fresh**")],
)  # fmt: skip
def test_freshness(world, monkeypatch, now, head):
    monkeypatch.setattr("readmission_risk.dashboard.heartbeat.wall_now", lambda: now)
    assert run().markdown[1].value.startswith(head + " · ")


def test_the_image_commit(world, monkeypatch):
    monkeypatch.setenv("READMISSION_RISK_GIT_COMMIT", "0123456789abcdef")
    assert "Image commit `0123456789ab`" in captions_of(run())[-1]


def test_a_clock_mismatch_is_visible(world):
    for row in world.published:
        row["clock_sim_date"] = date(2023, 11, 6)
    captions = captions_of(run())
    assert (
        "The date list and the heartbeat disagree on the simulated date (2023-11-06 vs 2023-10-30); they were read at "
        "different times and should agree within 3 hours."
    ) in captions


# ---------------------------------------------------------------- 9. E7


def test_a_broken_call_sheet_leaves_the_rest(world, monkeypatch, expected_log):
    def broken(rows):
        raise ValueError("SECRET-123")

    monkeypatch.setattr(present, "call_sheet_frame", broken)
    expected_log[("dashboard part The call sheet failed (ValueError)", ValueError)] = 1
    at = run()
    assert warnings_of(at) == ["The call sheet could not be displayed. The other views still work."]
    assert len(at.get("vega_lite_chart")) == 1 and "SECRET-123" not in "\n".join(texts(at))


def test_a_broken_heartbeat_leaves_the_rest(world, monkeypatch, expected_log):
    def broken(*args, **kwargs):
        raise ValueError("SECRET-123")

    monkeypatch.setattr(heartbeat_mod, "heartbeat", broken)
    expected_log[("dashboard part The heartbeat failed (ValueError)", ValueError)] = 1
    at = run()
    assert warnings_of(at) == ["The heartbeat could not be displayed. The other views still work."]
    assert at.markdown[1].value == "❔ **Heartbeat unavailable**"
    assert len(at.dataframe) >= 1
    infos = infos_of(at)
    assert app.MON_NO_BUNDLE_UNKNOWN in infos
    assert app.NO_CLOCK_UNKNOWN.format(what="scoring runs") in infos and app.NO_CLOCK_UNKNOWN.format(what="monitoring ticks") in infos


def test_a_broken_footer_leaves_the_rest(world, monkeypatch, expected_log):
    def broken(**kwargs):
        raise ValueError("x")

    monkeypatch.setattr(app.chrome, "footer", broken)
    expected_log[("dashboard part The footer failed (ValueError)", ValueError)] = 1
    at = run()
    assert warnings_of(at) == ["The footer could not be displayed. The other views still work."]
    assert len(at.get("vega_lite_chart")) == 1 and len(at.dataframe) == 5


def test_the_log_guard_reports_a_swallowed_assertion():
    record = logging.LogRecord("readmission_risk.dashboard", logging.WARNING, __file__, 1, "dashboard query x failed", (), None)
    record.exc_info = (AssertionError, AssertionError("trap"), None)  # what fetch logs when the E2 trap fires
    assert check_log([record], Counter({("dashboard query x failed", AssertionError): 1})) == ["dashboard query x failed"]


# ---------------------------------------------------------------- 10. the date list changes mid-session


def test_the_date_list_changes_during_a_session(world, monkeypatch):
    at = run()
    new_day, now = date(2023, 11, 6), datetime(2026, 11, 3, 12, tzinfo=UTC)
    world.published = [published_row(new_day, "r6", clock=new_day)] + [
        published_row(day, run_id, clock=new_day) for day, run_id in PUBLISHED_RUNS.items()
    ]
    world.sheets[new_day] = sheet_rows(new_day, "r6")
    m6 = {**monitoring_rows()[-1], "monitoring_run_id": "m6", "sim_date": new_day, "scoring_run_id": "r6",
          "started_at": datetime(2026, 11, 2, 0, 17, tzinfo=UTC)}  # fmt: skip
    world.monitoring = [*world.monitoring, m6]
    monkeypatch.setattr("readmission_risk.dashboard.heartbeat.wall_now", lambda: now)
    st.cache_resource.clear()
    at.run()
    assert at.selectbox[0].value == new_day and at.dataframe[0].value["Patient ID (synthetic)"].iloc[0] == "p-1106-1"
    assert not any("disagree on the simulated date" in c for c in captions_of(at))
    at.selectbox[0].select(CLOCK).run()
    world.published = [published_row(new_day, "r6", clock=new_day)] + [
        published_row(day, "r5b" if day == CLOCK else run_id, n=4 if day == CLOCK else 5, clock=new_day)
        for day, run_id in PUBLISHED_RUNS.items()
    ]
    world.sheets[CLOCK] = sheet_rows(CLOCK, "r5b", n=4)
    st.cache_resource.clear()
    at.run()
    assert at.selectbox[0].value == CLOCK  # the options did not change, so the pick is kept
    captions = captions_of(at)
    assert not any(c.endswith(("shown, but the run recorded 4.", "than the listed one.")) or "flagged Top-N" in c for c in captions)
    assert len(at.dataframe[0].value) + len(at.dataframe[1].value) == 4


# ---------------------------------------------------------------- 11-12


@pytest.mark.no_fake_world
def test_constants_and_a_side_effect_free_import(world):
    assert app.CACHE_TTL.total_seconds() == 3 * 3600 and app.CACHE_MAX_ENTRIES == 64
    assert (present.SCORING_RUNS_LIMIT, present.MONITORING_LIMIT) == (50, 1000)
    importlib.reload(app)  # gcp's factories still refuse (tests/conftest.py): any client call would raise
    assert len(world.client.calls) == 0
    marked = [h for h in app.logger.handlers if getattr(h, "_readmission_risk_dashboard", False)]
    assert len(marked) == 1


def test_an_empty_top_n_and_an_all_top_n_date(world):
    world.published = [published_row(CLOCK, "r5", top_n=0), published_row(date(2023, 10, 23), "r4", top_n=5)]
    world.sheets[CLOCK] = sheet_rows(CLOCK, "r5", top=0)
    world.sheets[date(2023, 10, 23)] = sheet_rows(date(2023, 10, 23), "r4", top=5)
    at = run()
    assert at.subheader[0].value == "Call this week: top 0"
    captions = captions_of(at)
    assert "No discharge is flagged Top-N for this run date." in captions and not any("flagged Top-N;" in c for c in captions)
    assert [e.label for e in at.expander] == ["Rest of the ranked list (5)"]
    at.selectbox[0].select(date(2023, 10, 23)).run()
    assert at.subheader[0].value == "Call this week: top 5" and len(at.expander) == 0
