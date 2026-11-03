"""The heartbeat (v3 slice 3, Verification V-H): the D5 clock on hand-written cases, the S13 Late rule, the ledger's Stale rule,
the six states and their literal lines."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from readmission_risk.dashboard.heartbeat import (
    Heartbeat,
    heartbeat,
    heartbeat_line,
    late_after,
    simulated_clock,
)

B, OTHER = "b" * 64, "c" * 64


def _tick(i, started, *, sim=None, bundle=B, status="succeeded", cadence="weekly", trigger="schedule", run=None, tests=True):
    return {
        "monitoring_run_id": f"m{i}", "started_at": started, "sim_date": sim, "model_bundle_id": bundle, "status": status,
        "cadence": cadence, "trigger": trigger, "scoring_run_id": run, "history_tests_passed": tests,
    }  # fmt: skip


def _t(day, hour=0, minute=17):
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


# ---------------------------------------------------------------- the clock (D5), expected values written by hand


def test_the_newest_succeeded_bundle_wins_although_another_bundle_has_a_later_date():
    rows = [_tick(1, _t(19), sim=date(2024, 1, 1), bundle=OTHER), _tick(2, _t(26), sim=date(2023, 10, 30))]
    assert simulated_clock(rows)[:2] == (date(2023, 10, 30), B)


def test_within_the_bundle_the_max_sim_date_wins_even_from_an_older_tick():
    rows = [_tick(1, _t(19), sim=date(2023, 11, 6)), _tick(2, _t(26), sim=date(2023, 10, 30))]
    clock, bundle, row = simulated_clock(rows)
    assert (clock, bundle, row["monitoring_run_id"]) == (date(2023, 11, 6), B, "m1")


def test_failed_and_incomplete_rows_are_ignored_and_ties_break_by_id():
    rows = [
        _tick(1, _t(27), sim=date(2024, 1, 1), status="failed"), _tick(2, _t(27), sim=None), _tick(3, _t(27), bundle=None,
        sim=date(2024, 1, 1)), _tick(4, _t(26), sim=date(2023, 10, 30), bundle=OTHER), _tick(5, _t(26), sim=date(2023, 10, 23)),
    ]  # fmt: skip
    assert simulated_clock(rows)[:2] == (date(2023, 10, 23), B)  # m5 > m4 on the tie
    assert simulated_clock([_tick(1, _t(26), status="failed", sim=date(2023, 1, 2))]) is None


# ---------------------------------------------------------------- late (S13) and stale


def test_late_after_is_friday_of_the_next_iso_week():
    friday = datetime(2026, 11, 6, tzinfo=UTC)
    assert late_after(datetime(2026, 10, 26, 0, 17, tzinfo=UTC)) == friday  # Monday
    assert late_after(datetime(2026, 10, 28, 23, 17, tzinfo=UTC)) == friday  # Wednesday
    assert late_after(datetime(2026, 10, 29, 2, 27, tzinfo=UTC)) == friday  # a delayed Thursday slot
    assert late_after(datetime(2026, 10, 26, 0, 30, tzinfo=timezone(timedelta(hours=1)))) == datetime(2026, 10, 30, tzinfo=UTC)


def test_a_delayed_next_week_slot_is_not_late():
    anchor = [_tick(1, datetime(2026, 10, 19, 0, 17, tzinfo=UTC), sim=date(2023, 10, 23))]
    assert heartbeat(anchor, [], now=datetime(2026, 10, 29, 2, 27, tzinfo=UTC)).state == "ok"
    assert heartbeat(anchor, [], now=datetime(2026, 10, 30, tzinfo=UTC)).state == "late"


def test_states_at_their_boundaries():
    rows = [_tick(1, _t(26), sim=date(2023, 10, 30))]
    hb = heartbeat(rows, [], now=_t(27))
    assert (hb.late_at, hb.stale_at) == (datetime(2026, 11, 6, tzinfo=UTC), _t(26) + timedelta(days=14))
    assert heartbeat(rows, [], now=hb.late_at - timedelta(microseconds=1)).state == "ok"
    assert heartbeat(rows, [], now=hb.late_at).state == "late"
    assert heartbeat(rows, [], now=hb.stale_at).state == "stale"
    assert heartbeat([_tick(1, _t(26), cadence="burn_in", sim=date(2023, 10, 30))], [], now=_t(27)).state == "not_started"
    assert heartbeat([], [], now=_t(27)).state == "no_ticks"
    assert heartbeat(None, [], now=_t(27)) == Heartbeat.unavailable() == Heartbeat("unknown")
    with pytest.raises(ValueError, match="tz-aware"):
        heartbeat(rows, [], now=datetime(2026, 10, 27))


def test_the_data_tests_describe_the_newest_tick():
    ok = _tick(1, _t(26), sim=date(2023, 10, 30), run="r1")
    failed = _tick(2, _t(28), sim=None, status="failed", run="r2", tests=False)
    hb = heartbeat([ok, failed], [{"scoring_run_id": "r2", "predictions_tests_passed": True}], now=_t(28, 12))
    assert hb.state == "ok"
    assert "(failed) · data tests FAILED" in heartbeat_line(hb)
    runs = [{"scoring_run_id": "r1", "predictions_tests_passed": True}]
    assert "data tests passed" in heartbeat_line(heartbeat([ok], runs, now=_t(27)))
    assert "data tests unknown" in heartbeat_line(heartbeat([ok], [], now=_t(27)))  # run absent
    assert "data tests unknown" in heartbeat_line(heartbeat([ok], None, now=_t(27)))  # runs query failed
    no_run = _tick(1, _t(26), sim=date(2023, 10, 30), run=None)
    assert "data tests unknown" in heartbeat_line(heartbeat([no_run], runs, now=_t(27)))


# ---------------------------------------------------------------- the literal lines


def test_heartbeat_lines():
    rows = [_tick(1, _t(26), sim=date(2023, 10, 30), run="r1")]
    runs = [{"scoring_run_id": "r1", "predictions_tests_passed": True}]
    facts = "simulated date 2023-10-30 · last tick ran 2026-10-26 00:17 UTC (succeeded) · data tests passed · bundle `bbbbbbbbbbbb…`"
    assert heartbeat_line(heartbeat(rows, runs, now=_t(27))) == f"✅ **Fresh** · {facts}"
    assert heartbeat_line(heartbeat(rows, runs, now=datetime(2026, 11, 6, tzinfo=UTC))) == (
        f"⚠️ **Late** · {facts} · no scheduled weekly tick since 2026-10-26 00:17 UTC; stale from 2026-11-09"
    )
    assert heartbeat_line(heartbeat(rows, runs, now=datetime(2026, 11, 10, tzinfo=UTC))) == (
        f"⛔ **Stale since 2026-11-09** · {facts} · no scheduled weekly tick since 2026-10-26 00:17 UTC"
    )
    burn_in = [_tick(1, _t(26), sim=date(2023, 10, 30), run="r1", cadence="burn_in")]
    assert heartbeat_line(heartbeat(burn_in, runs, now=_t(27))) == f"⏸️ **Schedule not started** · {facts}"
    assert heartbeat_line(heartbeat([], runs, now=_t(27))) == "⏸️ **No monitoring tick yet**"
    assert heartbeat_line(Heartbeat.unavailable()) == "❔ **Heartbeat unavailable**"


def test_a_failed_newest_tick_without_a_clock_and_an_unknown_status():
    rows = [_tick(1, _t(26), sim=None, status="weird<b>", run=None, bundle="not-hex")]
    line = heartbeat_line(heartbeat(rows, [], now=_t(27)))
    assert line == (
        "⏸️ **Schedule not started** · simulated date unknown · last tick ran 2026-10-26 00:17 UTC (other) · data tests unknown"
        " · bundle `unknown`"
    )
