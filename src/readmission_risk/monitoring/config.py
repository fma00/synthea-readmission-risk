"""deploy/monitoring.json: which bundle the tick monitors, on which gold, over which simulated weeks, at which cadences.
Standard library only. See notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md (Interfaces > config).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from readmission_risk.bundles.champion import Champion
from readmission_risk.bundles.manifest import HEX64_RE

DEFAULT_CONFIG_PATH = Path("deploy/monitoring.json")
STEP_DAYS = 7  # consecutive ticks' 7-day windows tile the timeline exactly; any other value is a design change
WINDOW_DAYS = 7
BURN_IN_CRONS = ("7,37 13-18 * * *", "7 13-22 * * *")  # default, hourly fallback (if one tick takes > 25 minutes)
WEEKLY_CRON = "17 6 * * 1"
CADENCES = ("burn_in", "weekly", "dispatch", "catch_up")
_KEYS = {
    "model_bundle_id", "gold_uri", "first_sim_date", "last_sim_date", "step_days", "window_days", "top_n", "crons", "epoch",
}  # fmt: skip
_GS_URI_RE = re.compile(r"gs://[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]/[^\s]+")
_INT32_MAX = 2**31 - 1
_PERIODS = {
    BURN_IN_CRONS[0]: timedelta(minutes=30),
    BURN_IN_CRONS[1]: timedelta(minutes=60),
    WEEKLY_CRON: timedelta(days=7),
}


@dataclass(frozen=True)
class MonitoringConfig:
    model_bundle_id: str
    gold_uri: str
    first_sim_date: date
    last_sim_date: date
    step_days: int
    window_days: int
    top_n: int
    crons: dict[str, str]  # exactly {"burn_in", "weekly"}
    epoch: date  # every read of serving tables filters started_at >= this day


def _yyyymmdd(key: str, value: object) -> date:
    if not (isinstance(value, str) and value.isascii() and value.isdigit() and len(value) == 8):
        raise ValueError(f"monitoring.json: {key} must be a YYYYMMDD string; got {value!r}")
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:]))
    except ValueError:
        raise ValueError(f"monitoring.json: {key} is not a calendar date: {value!r}") from None


def parse_monitoring_config(document: object) -> MonitoringConfig:
    if not isinstance(document, dict):
        raise ValueError(f"monitoring.json must hold a JSON object; got {type(document).__name__}")  # noqa: TRY004 -- malformed input is a ValueError by design
    if set(document) != _KEYS:
        raise ValueError(f"monitoring.json must have exactly the keys {sorted(_KEYS)}; got {sorted(document)}")
    bundle_id = document["model_bundle_id"]
    if type(bundle_id) is not str or not HEX64_RE.fullmatch(bundle_id):
        raise ValueError(f"monitoring.json: model_bundle_id must be 64 lowercase hex characters; got {bundle_id!r}")
    gold_uri = document["gold_uri"]
    if type(gold_uri) is not str or not _GS_URI_RE.fullmatch(gold_uri):
        raise ValueError(f"monitoring.json: gold_uri must be gs://<bucket>/<path>; got {gold_uri!r}")
    first = _yyyymmdd("first_sim_date", document["first_sim_date"])
    last = _yyyymmdd("last_sim_date", document["last_sim_date"])
    epoch = _yyyymmdd("epoch", document["epoch"])
    for key, required in (("step_days", STEP_DAYS), ("window_days", WINDOW_DAYS)):
        if type(document[key]) is not int or document[key] != required:
            raise ValueError(f"monitoring.json: {key} must be {required}; got {document[key]!r}")
    if first > last or (last - first).days % STEP_DAYS != 0:
        raise ValueError(f"monitoring.json: last_sim_date {last} must be first_sim_date {first} plus a whole number of weeks")
    top_n = document["top_n"]
    if type(top_n) is not int or not 1 <= top_n <= _INT32_MAX:
        raise ValueError(f"monitoring.json: top_n must be an int in [1, {_INT32_MAX}]; got {top_n!r}")
    crons = document["crons"]
    if not isinstance(crons, dict) or set(crons) != {"burn_in", "weekly"}:
        raise ValueError(f"monitoring.json: crons must have exactly the keys burn_in and weekly; got {crons!r}")
    if crons["burn_in"] not in BURN_IN_CRONS:
        raise ValueError(f"monitoring.json: crons.burn_in must be one of {list(BURN_IN_CRONS)}; got {crons['burn_in']!r}")
    if crons["weekly"] != WEEKLY_CRON:
        raise ValueError(f"monitoring.json: crons.weekly must be {WEEKLY_CRON!r}; got {crons['weekly']!r}")
    return MonitoringConfig(
        model_bundle_id=bundle_id, gold_uri=gold_uri, first_sim_date=first, last_sim_date=last, step_days=STEP_DAYS,
        window_days=WINDOW_DAYS, top_n=top_n, crons=dict(crons), epoch=epoch,
    )  # fmt: skip


def read_monitoring_config(path: Path = DEFAULT_CONFIG_PATH) -> MonitoringConfig:
    """FileNotFoundError passes through; every other defect is a ValueError."""
    raw = Path(path).read_bytes()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"monitoring.json: {path} is not UTF-8 JSON: {exc}") from exc
    return parse_monitoring_config(document)


def sim_grid(config: MonitoringConfig) -> list[date]:
    n = (config.last_sim_date - config.first_sim_date).days // config.step_days
    return [config.first_sim_date + timedelta(days=config.step_days * k) for k in range(n + 1)]


def on_grid(config: MonitoringConfig, day: date) -> bool:
    return config.first_sim_date <= day <= config.last_sim_date and (day - config.first_sim_date).days % config.step_days == 0


def cadence_for(event_name: str, schedule: str | None, config: MonitoringConfig) -> str:
    schedule = schedule or None
    if event_name == "laptop":
        return "catch_up"
    if event_name == "workflow_dispatch":
        return "dispatch"
    if event_name == "schedule":
        if schedule == config.crons["burn_in"]:
            return "burn_in"
        if schedule == config.crons["weekly"]:
            return "weekly"
        raise ValueError(f"scheduled by cron {schedule!r}, which deploy/monitoring.json does not name")
    raise ValueError(f"the monitoring tick does not run on event {event_name!r}")


def check_champion_matches(config: MonitoringConfig, champion: Champion) -> None:
    leaf = champion.bundle_uri.rstrip("/").rsplit("/", 1)[-1]
    if leaf != config.model_bundle_id:
        raise ValueError(
            f"deploy/monitoring.json monitors bundle {config.model_bundle_id}, but the champion is {leaf}: a promotion must "
            "update the monitoring config (and its clock range) in the same change"
        )


def period_of(cron: str) -> timedelta:
    if cron not in _PERIODS:
        raise ValueError(f"unknown monitoring cron {cron!r}")
    return _PERIODS[cron]
