"""Offline checks over a local gold copy and a local bundle (no GCP): preflight proves the completeness guard accepts every
grid date's 7-day window; simulate scores every grid date through the unchanged scorer and evaluates each cohort as a
clean catch-up-then-tick history would (the sanity check, and L10's oracle). See
notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md (CLIs > preflight, simulate).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from readmission_risk.models.data import load_gold_table_with_metadata
from readmission_risk.pipeline.gold_metadata import parse_yyyymmdd
from readmission_risk.scoring.bundle_guard import validate_bundle
from readmission_risk.scoring.pipeline import ScoringConfig, score_run_date
from readmission_risk.scoring.selection import (
    observation_horizon,
    select_held_out_window,
)

from .cohort import cohort_spec, evaluate_cohort, required_ticks
from .config import MonitoringConfig, sim_grid
from .rule import AlertRule


@dataclass(frozen=True)
class PreflightLine:
    sim_date: date
    n_rows: int | None
    refusal: str | None


def preflight(gold_dir: Path, bundle_dir: Path, config: MonitoringConfig) -> list[PreflightLine]:
    gold, metadata = load_gold_table_with_metadata(Path(gold_dir))
    bundle = validate_bundle(str(bundle_dir), gold=gold, metadata=metadata)  # guards + integrity; nothing is unpickled
    horizon = observation_horizon(parse_yyyymmdd(metadata.reference_date, "reference_date"), metadata.readmission_window_days)
    lines = []
    for day in sim_grid(config):
        try:
            batch = select_held_out_window(
                gold, bundle.partition_of, run_date=day, window_days=config.window_days, observed_until=horizon
            )
            lines.append(PreflightLine(day, len(batch), None))
        except ValueError as exc:
            lines.append(PreflightLine(day, None, str(exc)))
    return lines


def simulate(gold_dir: Path, bundle_dir: Path, config: MonitoringConfig, rule: AlertRule) -> pd.DataFrame:
    frames = []

    def writer(predictions: pd.DataFrame, _output_dir: Path, run_date: date) -> str:
        frames.append(predictions.copy())
        return f"memory://{run_date:%Y%m%d}"

    grid = sim_grid(config)
    for day in grid:
        scoring = ScoringConfig(
            gold_dir=Path(gold_dir), run_date=f"{day:%Y%m%d}", bundle_uri=str(bundle_dir), top_n=config.top_n,
            window_days=config.window_days,
        )  # fmt: skip
        score_run_date(scoring, writer=writer)
    predictions = pd.concat(frames, ignore_index=True)
    gold, metadata = load_gold_table_with_metadata(Path(gold_dir))
    labels = gold[["encounter_id", "is_readmitted", "label_horizon"]]
    spec = cohort_spec(rule, metadata)
    rows = []
    for day in grid:
        found = {d for d in grid if d <= day}
        evaluation = evaluate_cohort(
            predictions[["encounter_id", "discharge_time", "risk_score"]], labels, sim_date=day, rule=rule, spec=spec,
            found_ticks=found, required=required_ticks(day, spec, config),
        )  # fmt: skip
        rows.append(asdict(evaluation))
    return pd.DataFrame(rows)
