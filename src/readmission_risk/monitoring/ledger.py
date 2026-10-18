"""docs/run-ledger.md and the shields.io badge JSON, rendered from serving.monitoring + serving.scoring_runs (pure; the
same inputs give byte-identical output). See notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md
(Interfaces > monitoring/ledger.py).
"""

from __future__ import annotations

import math
import re

import pandas as pd

from .config import MonitoringConfig, period_of

HEADER = (
    "**Simulated clock.** Each tick advances a simulated date by one week through the held-out 2023–2026 discharges of a "
    "synthetic 200,000-patient population; the wall-clock time is when the tick ran. Ticks labelled catch-up (laptop) "
    "were run by the owner to fill the first weeks; ticks labelled burn-in (accelerated cadence) are scheduled every 30 "
    "or 60 minutes on a burn-in afternoon; weekly ticks are scheduled on Mondays. This ledger shows repeatability and "
    "idempotency, not weeks of unattended operation. The monitored discharges come from a gold table built with hindsight "
    "(deaths and later stays shape which discharges are included), so this is a demonstration of the monitoring "
    "mechanics, not an estimate of live calibration. The exact Poisson interval treats readmissions as independent "
    "events; patients with several stays in a cohort make it somewhat too narrow. Synthetic data, not for clinical use. "
    "Source of truth: BigQuery `serving.scoring_runs` and `serving.monitoring`."
)
CADENCE_LABELS = {
    "burn_in": "burn-in (accelerated cadence)", "weekly": "weekly", "dispatch": "dispatch", "catch_up": "catch-up (laptop)",
}  # fmt: skip
_CRON_RE = re.compile(r'^\s*-\s*cron:\s*"([^"]+)"\s*$', re.MULTILINE)


def _missing(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NaT


def _text(value) -> str:
    return "-" if _missing(value) else str(value)


def _num(value, digits: int) -> str:
    return "-" if _missing(value) else f"{float(value):.{digits}f}"


def _wall(value) -> str:
    return "-" if _missing(value) else pd.Timestamp(value).tz_convert("UTC").strftime("%Y-%m-%d %H:%M UTC")


def _day(value) -> str:
    return "-" if _missing(value) else pd.Timestamp(value).strftime("%Y-%m-%d")


def _link(url) -> str:
    return "laptop" if _missing(url) else f"[run]({url})"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _sorted(frame: pd.DataFrame, id_column: str, *, newest_first: bool) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.assign(_t=pd.to_datetime(frame["started_at"], utc=True))
    out = out.sort_values(["_t", id_column], ascending=not newest_first, kind="stable")
    return out.drop(columns="_t")


def render_ledger(monitoring: pd.DataFrame, scoring_runs: pd.DataFrame, *, config: MonitoringConfig) -> str:
    lines = ["# Run ledger", "", HEADER, "", f"Monitored bundle: `{config.model_bundle_id[:12]}…`.", "", "## Ticks", ""]
    lines += [
        "| wall time | cadence | sim date | status | verdict | O/E [95% CI] | observed / expected | n matured | AUC (report only) | immature expected | link |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in _sorted(monitoring, "monitoring_run_id", newest_first=True).to_dict("records"):
        status = row["status"] + (f" ({row['failed_stage']})" if not _missing(row.get("failed_stage")) else "")
        if _missing(row.get("oe_ratio")):
            ratio = "-"
        else:
            ratio = f"{_num(row['oe_ratio'], 3)} [{_num(row['oe_ci_low'], 3)}, {_num(row['oe_ci_high'], 3)}]"
        oe = "-" if _missing(row.get("observed")) else f"{row['observed']} / {_num(row['expected'], 1)}"
        cells = [
            _wall(row["started_at"]), CADENCE_LABELS.get(row.get("cadence"), "-"), _day(row.get("sim_date")), status,
            _text(row.get("verdict")), ratio, oe, _text(row.get("n_matured")), _num(row.get("auc"), 3),
            _num(row.get("expected_immature"), 1), _link(row.get("github_run_url")),
        ]  # fmt: skip
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    referenced = set(monitoring["scoring_run_id"].dropna()) if not monitoring.empty else set()
    others = scoring_runs[~scoring_runs["scoring_run_id"].isin(referenced)] if not scoring_runs.empty else scoring_runs
    lines += ["", "## Other scoring runs", "", "| wall time | run date | status | trigger | link |", "|---|---|---|---|---|"]
    for row in _sorted(others, "scoring_run_id", newest_first=True).head(50).to_dict("records"):
        cells = [_wall(row["started_at"]), _day(row.get("run_date")), _text(row["status"]), _text(row["trigger"]), _link(row.get("github_run_url"))]
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    return "\n".join(lines) + "\n"


def cron_state(workflow_text: str, config: MonitoringConfig) -> str | None:
    """The committed monitor.yml's schedule: one of the config's crons, or None without a schedule."""
    crons = _CRON_RE.findall(workflow_text)
    if not crons:
        return None
    if len(crons) > 1:
        raise ValueError(f"monitor.yml has {len(crons)} cron lines; expected at most one")
    (cron,) = crons
    if cron not in (config.crons["burn_in"], config.crons["weekly"]):
        raise ValueError(f"monitor.yml's cron {cron!r} is not one of deploy/monitoring.json's")
    return cron


def last_tick_badge(monitoring: pd.DataFrame, *, cron: str | None) -> dict:
    badge = {"schemaVersion": 1, "label": "last tick (simulated clock)"}
    if monitoring.empty:
        return {**badge, "message": "none yet", "color": "lightgrey"}
    ordered = _sorted(monitoring, "monitoring_run_id", newest_first=True)
    latest = ordered.iloc[0]
    message = f"{_wall(latest['started_at'])} · sim {_day(latest.get('sim_date'))}"
    if cron is None:
        message += " · dispatch only"
    else:
        scheduled = ordered[ordered["trigger"] == "schedule"]
        if scheduled.empty:
            message += " · schedule not started"
        else:
            stale = pd.Timestamp(scheduled.iloc[0]["started_at"]).tz_convert("UTC") + 2 * period_of(cron)
            message += f" · stale after {_wall(stale)}"
    return {**badge, "message": message, "color": "brightgreen" if latest["status"] == "succeeded" else "red"}
