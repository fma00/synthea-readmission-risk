"""deploy/monitoring.json and its parser (v2 slice 7)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from readmission_risk.bundles.champion import Champion, read_champion
from readmission_risk.monitoring import config as mc

ROOT = Path(__file__).resolve().parents[2]
COMMITTED = json.loads((ROOT / "deploy" / "monitoring.json").read_text())


def _parse(**changes):
    return mc.parse_monitoring_config({**COMMITTED, **changes})


def test_committed_config_parses_to_the_measured_grid():
    config = mc.read_monitoring_config(ROOT / "deploy" / "monitoring.json")
    grid = mc.sim_grid(config)
    assert len(grid) == 178 and grid[0] == date(2023, 4, 17) and grid[-1] == date(2026, 9, 7)
    assert all(d.weekday() == 0 for d in grid)
    assert config.window_days == config.step_days == 7 and config.top_n == 10
    assert config.epoch == date(2026, 10, 18)


def test_committed_config_monitors_the_committed_champion():
    config = mc.read_monitoring_config(ROOT / "deploy" / "monitoring.json")
    mc.check_champion_matches(config, read_champion(ROOT / "deploy" / "champion.json"))


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"model_bundle_id": "ab" * 31}, "model_bundle_id"),
        ({"gold_uri": "data/gold/main_200k"}, "gold_uri"),
        ({"first_sim_date": "2023-04-17"}, "YYYYMMDD"),
        ({"first_sim_date": "20230231"}, "calendar date"),
        ({"last_sim_date": "20260908"}, "whole number of weeks"),
        ({"last_sim_date": "20230410"}, "whole number of weeks"),
        ({"step_days": 14}, "step_days must be 7"),
        ({"window_days": 30}, "window_days must be 7"),
        ({"top_n": 0}, "top_n"),
        ({"top_n": True}, "top_n"),
        ({"crons": {"burn_in": "7,37 13-18 * * *"}}, "exactly the keys burn_in and weekly"),
        ({"crons": {"burn_in": "*/5 * * * *", "weekly": "17 6 * * 1"}}, "crons.burn_in"),
        ({"crons": {"burn_in": "7,37 13-18 * * *", "weekly": "0 0 * * 1"}}, "crons.weekly"),
        ({"extra": 1}, "exactly the keys"),
    ],
)
def test_malformed_configs_are_refused(changes, match):
    with pytest.raises(ValueError, match=match):
        _parse(**changes)


def test_hourly_burn_in_fallback_is_accepted():
    assert _parse(crons={"burn_in": "7 13-22 * * *", "weekly": "17 6 * * 1"}).crons["burn_in"] == "7 13-22 * * *"


def test_read_refuses_non_json_and_passes_missing_file(tmp_path):
    bad = tmp_path / "m.json"
    bad.write_bytes(b"\xff{")
    with pytest.raises(ValueError, match="not UTF-8 JSON"):
        mc.read_monitoring_config(bad)
    with pytest.raises(FileNotFoundError):
        mc.read_monitoring_config(tmp_path / "missing.json")
    bad.write_text("[]")
    with pytest.raises(ValueError, match="JSON object"):
        mc.read_monitoring_config(bad)


def test_cadence_for():
    config = _parse()
    assert mc.cadence_for("schedule", "7,37 13-18 * * *", config) == "burn_in"
    assert mc.cadence_for("schedule", "17 6 * * 1", config) == "weekly"
    assert mc.cadence_for("workflow_dispatch", "", config) == "dispatch"
    assert mc.cadence_for("laptop", None, config) == "catch_up"
    with pytest.raises(ValueError, match="does not name"):
        mc.cadence_for("schedule", "7 13-22 * * *", config)
    with pytest.raises(ValueError, match="does not run on event"):
        mc.cadence_for("push", None, config)


def test_champion_mismatch_is_refused():
    config = _parse()
    with pytest.raises(ValueError, match="must update the monitoring config"):
        mc.check_champion_matches(config, Champion(bundle_uri="gs://b/" + "0" * 64, sha256="0" * 64))
    mc.check_champion_matches(config, Champion(bundle_uri=f"gs://b/{config.model_bundle_id}/", sha256="0" * 64))


def test_on_grid_and_period_of():
    config = _parse()
    assert mc.on_grid(config, date(2023, 4, 17)) and mc.on_grid(config, date(2026, 9, 7))
    assert not mc.on_grid(config, date(2023, 4, 18)) and not mc.on_grid(config, date(2023, 4, 10))
    assert not mc.on_grid(config, date(2026, 9, 14))
    assert mc.period_of("7,37 13-18 * * *") == timedelta(minutes=30)
    assert mc.period_of("7 13-22 * * *") == timedelta(hours=1)
    assert mc.period_of("17 6 * * 1") == timedelta(days=7)
    with pytest.raises(ValueError, match="unknown monitoring cron"):
        mc.period_of("* * * * *")
