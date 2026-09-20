"""The orchestration: one run date in, one replaced prediction partition out. Every validation happens before the first
side effect (the write), and no model is unpickled until every guard has passed. See
notes/eg-new-feature/batch-scoring-cli-2026-09-20.md (Interfaces > pipeline.py; UX flow).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from readmission_risk.models.data import LABEL_COLUMN, load_gold_table_with_metadata
from readmission_risk.models.tracking import load_logged_model
from readmission_risk.models.training import MODEL_NAMES
from readmission_risk.pipeline.gold_metadata import parse_yyyymmdd

from .constants import DEFAULT_MODEL, DEFAULT_TOP_N
from .ranking import ObservedSummary, build_predictions, score_batch, summarize_observed
from .runs import ModelRun, resolve_run_id, validate_run
from .selection import observation_horizon, select_held_out_window, window_bounds
from .store import write_partition

_INT32_MAX = 2**31 - 1


@dataclass(frozen=True)
class ScoringConfig:
    gold_dir: Path
    run_date: str  # "YYYYMMDD"
    output_dir: Path = Path("data/predictions")
    tracking_dir: Path = Path("mlflow")
    experiment_name: str = "readmission-risk"  # the trainer's default; the real 2b runs need "readmission-risk-2b"
    model_name: str | None = None  # None and run_id None -> DEFAULT_MODEL
    run_id: str | None = None  # when given, experiment_name is ignored
    top_n: int = DEFAULT_TOP_N
    window_days: int | None = None  # None -> the gold metadata's readmission_window_days
    show_observed_outcomes: bool = False


@dataclass(frozen=True, eq=False)  # holds DataFrames/Series: no generated __eq__/__hash__
class ScoringResult:
    predictions: pd.DataFrame  # exactly what was handed to the writer
    partition_path: Path  # what the writer returned
    run: ModelRun
    window_start: pd.Timestamp
    window_end: pd.Timestamp
    window_days: int  # resolved
    observed: ObservedSummary | None  # None unless show_observed_outcomes


def _validate_config(config: ScoringConfig) -> date:
    """Pure; runs first and holds every config check itself, so a bad config is a ValueError before any I/O. Returns the
    parsed run date."""
    run_date = parse_yyyymmdd(config.run_date, "run_date")
    if type(config.top_n) is not int or not 1 <= config.top_n <= _INT32_MAX:
        raise ValueError(f"top_n must be an int in [1, {_INT32_MAX}]; got {config.top_n!r}")  # the column is int32
    if config.window_days is not None and (type(config.window_days) is not int or not 1 <= config.window_days <= _INT32_MAX):
        raise ValueError(f"window_days must be None or an int in [1, {_INT32_MAX}]; got {config.window_days!r}")
    if config.model_name is not None and config.model_name not in MODEL_NAMES:
        raise ValueError(f"model name must be one of {MODEL_NAMES}; got {config.model_name!r}")
    if config.run_id is not None and (not isinstance(config.run_id, str) or not config.run_id):
        raise ValueError(f"run_id must be a non-empty string; got {config.run_id!r}")
    if config.model_name is not None and config.run_id is not None:
        raise ValueError("--model and --run-id are mutually exclusive")
    return run_date


def score_run_date(config: ScoringConfig, *, loader=load_logged_model, writer=write_partition) -> ScoringResult:
    """`loader` and `writer` are injection points: the BigQuery swap replaces `writer`; tests replace both to prove that
    nothing is loaded or written until every guard has passed."""
    # 1. config
    run_date = _validate_config(config)

    # 2-3. gold (its own checks stand: sidecar present, label definition, counts), reference date, window
    gold, metadata = load_gold_table_with_metadata(config.gold_dir)
    reference_date = parse_yyyymmdd(metadata.reference_date, "reference_date")
    if run_date > reference_date:
        raise ValueError(f"run_date {config.run_date} is after the gold table's reference_date {metadata.reference_date}")
    window_days = config.window_days if config.window_days is not None else metadata.readmission_window_days
    window_start, window_end = window_bounds(run_date, window_days)

    # 4. resolve and validate the run (all guards; nothing is unpickled)
    run_id = config.run_id
    if run_id is None:
        run_id = resolve_run_id(config.tracking_dir, config.experiment_name, config.model_name or DEFAULT_MODEL)
    run = validate_run(config.tracking_dir, run_id, gold=gold, metadata=metadata)

    # 5. the held-out batch (the completeness guard; may refuse)
    observed_until = observation_horizon(reference_date, metadata.readmission_window_days)
    batch = select_held_out_window(
        gold, run.partition_of, run_date=run_date, window_days=window_days, observed_until=observed_until
    )

    # 6. ONLY NOW load the model
    model = loader(config.tracking_dir, run.run_id)

    # 7. score a label-free frame (the same one goes to both calls, which both refuse a frame that still has the label)
    label_free = batch.drop(columns=[LABEL_COLUMN])
    scores = score_batch(model, label_free)
    predictions = build_predictions(
        label_free,
        scores,
        run_date=run_date,
        top_n=config.top_n,
        window_days=window_days,
        model_run_id=run.run_id,
        model_name=run.model_name,
        label_definition=metadata.label_definition,
        gold_fingerprint=run.gold_fingerprint,
        gold_reference_date=metadata.reference_date,
    )
    if len(predictions) != len(batch) or set(predictions["encounter_id"]) != set(batch["encounter_id"]):
        raise RuntimeError("scored predictions do not match the selected batch (one row per batch row expected)")

    # 8. the only place the label is read, and only after scoring
    observed = None
    if config.show_observed_outcomes:
        observed = summarize_observed(predictions, gold.set_index("encounter_id")[LABEL_COLUMN], top_n=config.top_n)

    # 9. the first side effect
    partition_path = writer(predictions, config.output_dir, run_date)
    return ScoringResult(
        predictions=predictions,
        partition_path=partition_path,
        run=run,
        window_start=window_start,
        window_end=window_end,
        window_days=window_days,
        observed=observed,
    )
