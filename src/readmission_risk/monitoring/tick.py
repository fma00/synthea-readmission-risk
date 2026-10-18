"""One monitoring tick's clock and evaluate stages (the score stage is the unchanged warehouse.run.score_to_bigquery).
Neither function raises an Exception: the result carries the error and whether the row was recorded, and the CLI decides
the exit code. See notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md (Interfaces > monitoring/tick.py).
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from readmission_risk import gcp
from readmission_risk.bundles.champion import read_champion
from readmission_risk.models.data import load_gold_table_with_metadata
from readmission_risk.ops.prereg import load_prereg
from readmission_risk.pipeline.gold_metadata import parse_yyyymmdd
from readmission_risk.scoring.bundle_guard import compute_gold_fingerprint
from readmission_risk.scoring.constants import DEFAULT_CHAMPION_PATH
from readmission_risk.warehouse.data_tests import run_data_tests
from readmission_risk.warehouse.gold_source import materialized_gold_dir
from readmission_risk.warehouse.run import _classify, _git_state, _trigger_and_url

from . import store
from .clock import ClockError, next_sim_date
from .cohort import (
    Evaluation,
    cohort_spec,
    evaluate_cohort,
    read_window,
    required_ticks,
)
from .config import (
    CADENCES,
    DEFAULT_CONFIG_PATH,
    cadence_for,
    check_champion_matches,
    on_grid,
    read_monitoring_config,
)
from .rule import alert_rule_from_prereg

DEFAULT_PREREG_PATH = Path("prereg.yaml")


class TickCheckError(RuntimeError):
    """A check of the tick's own state failed (no exception from a library): recorded as failed at `stage`."""

    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True)
class ClockRequest:
    event_name: str  # GITHUB_EVENT_NAME, or "laptop"
    schedule: str | None  # github.event.schedule ("" -> None)
    requested: str | None  # dispatch input / --sim-date ("" -> None)
    config_path: Path = DEFAULT_CONFIG_PATH
    champion_path: Path = DEFAULT_CHAMPION_PATH


@dataclass(frozen=True, eq=False)
class ClockResult:
    sim_date: date | None
    cadence: str | None
    head: date | None
    record: store.MonitoringRecord | None  # written only on a refusal or failure
    error: BaseException | None
    recorded: bool


@dataclass(frozen=True)
class EvaluateRequest:
    sim_date: str
    cadence: str
    scoring_run_id: str | None
    score_result: str  # needs.score.result: success | failure | cancelled | skipped; "success" on the laptop
    config_path: Path = DEFAULT_CONFIG_PATH
    prereg_path: Path = DEFAULT_PREREG_PATH


@dataclass(frozen=True, eq=False)
class EvaluateResult:
    record: store.MonitoringRecord
    evaluation: Evaluation | None
    error: BaseException | None
    recorded: bool


def _new_record() -> tuple[store.MonitoringRecord, float]:
    trigger, url = _trigger_and_url()
    record = store.new_record(uuid.uuid4().hex, datetime.now(UTC), trigger)
    record.github_run_url = url
    record.git_commit, record.git_dirty = _git_state()
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT") if os.environ.get("GITHUB_ACTIONS") == "true" else None
    record.github_run_attempt = int(attempt) if attempt and attempt.isdigit() else None
    return record, time.monotonic()


def _finish(record: store.MonitoringRecord, start: float, exc: BaseException | None) -> None:
    record.finished_at = record.started_at + timedelta(seconds=time.monotonic() - start)
    if exc is not None:
        if isinstance(exc, TickCheckError):
            record.status, record.failed_stage = "failed", exc.stage
        else:
            record.status = _classify(exc, publish_attempted=False) if isinstance(exc, Exception) else "failed"
        record.error_type = type(exc).__name__
        record.error_message = str(exc)[: store.ERROR_MESSAGE_LIMIT]


def _parse_date(text: str | None) -> date | None:
    return parse_yyyymmdd(text, "sim_date") if text else None


def _head_or_none(succeeded, config) -> date | None:
    """The real clock head (None once the clock is exhausted), independent of a requested replay date."""
    try:
        return next_sim_date(succeeded, config)
    except ClockError:
        return None


def prepare_tick(request: ClockRequest, *, client_factory: Callable | None = None) -> ClockResult:
    record, start = _new_record()
    client = None
    sim_date = cadence = head = None
    error: BaseException | None = None
    try:
        client = (client_factory or gcp.bigquery_client)()
        config = read_monitoring_config(request.config_path)
        record.model_bundle_id = config.model_bundle_id
        check_champion_matches(config, read_champion(request.champion_path))
        cadence = cadence_for(request.event_name, request.schedule or None, config)
        record.cadence = cadence
        requested = _parse_date(request.requested)
        succeeded = store.succeeded_sim_dates(store.read_ticks(client, config), config)
        sim_date = next_sim_date(succeeded, config, requested=requested)
        head = _head_or_none(succeeded, config)
    except Exception as exc:  # noqa: BLE001 -- recorded below; the CLI decides the exit code
        error = exc
    if error is None:
        return ClockResult(sim_date=sim_date, cadence=cadence, head=head, record=None, error=None, recorded=True)
    record.failed_stage = "clock"
    _finish(record, start, error)
    if record.status == "succeeded":  # pragma: no cover - _classify never returns it
        record.status = "failed"
    recorded = False
    if client is not None:
        recorded, _ = store.record_monitoring(client, record)
    return ClockResult(sim_date=None, cadence=None, head=None, record=record, error=error, recorded=recorded)


def evaluate_tick(request: EvaluateRequest, *, client_factory: Callable | None = None) -> EvaluateResult:
    record, start = _new_record()
    record.scoring_run_id = request.scoring_run_id or None
    client = None
    evaluation: Evaluation | None = None
    error: BaseException | None = None
    try:
        # 1. client
        client = (client_factory or gcp.bigquery_client)()
        evaluation = _evaluate_steps(request, client, record)
        record.status = "succeeded"
    except Exception as exc:  # noqa: BLE001 -- recorded below; the CLI decides the exit code
        error = exc
        evaluation = None
        _clear_evaluation(record)
        if not isinstance(exc, TickCheckError):
            record.failed_stage = "evaluate"
    _finish(record, start, error)
    if error is not None and record.failed_stage is None:
        record.failed_stage = "evaluate"
    recorded = False
    if client is not None:
        recorded, insert_error = store.record_monitoring(client, record)
        if not recorded and error is None:
            error = insert_error
    return EvaluateResult(record=record, evaluation=evaluation, error=error, recorded=recorded)


def _evaluate_steps(request: EvaluateRequest, client, record: store.MonitoringRecord) -> Evaluation:
    """Steps 2-11 of the evaluate stage; raises on any refusal or failure (the caller records it)."""
    # 2. config, then only VALID sim_date / cadence reach the record
    config = read_monitoring_config(request.config_path)
    record.model_bundle_id = config.model_bundle_id
    sim_date = parse_yyyymmdd(request.sim_date, "sim_date")
    if not on_grid(config, sim_date):
        raise ValueError(f"sim date {sim_date.isoformat()} is not on the simulated grid")
    if request.cadence not in CADENCES:
        raise ValueError(f"cadence must be one of {list(CADENCES)}; got {request.cadence!r}")
    record.sim_date, record.cadence = sim_date, request.cadence
    # 3. history data tests
    history = run_data_tests(client, "monitoring", table=store.monitoring_table_id())
    record.history_tests_passed = history.passed
    if not history.passed:
        raise TickCheckError("evaluate", history.failure or "monitoring data tests failed")
    # 4. the score stage's own result
    if request.score_result != "success" or not request.scoring_run_id:
        raise TickCheckError("score", f"score job result: {request.score_result}; scoring run: {request.scoring_run_id or '-'}")
    # 5. the pre-registered rule
    rule = alert_rule_from_prereg(load_prereg(request.prereg_path))
    record.min_positives_floor = rule.min_positives_floor
    record.band_low, record.band_high = rule.band
    # 6. the clock re-check (the concurrency guard)
    succeeded = store.succeeded_sim_dates(store.read_ticks(client, config), config)
    next_sim_date(succeeded, config, requested=sim_date)
    # 7. this tick's scoring run
    facts = store.scoring_run_facts(client, [request.scoring_run_id], config)
    own = facts.to_dict("records")
    if len(own) != 1:
        raise TickCheckError("score", f"scoring run {request.scoring_run_id} is not in serving.scoring_runs")
    _check_own_run(own[0], sim_date, config)
    with materialized_gold_dir(config.gold_uri) as gold_dir:
        # 10a. gold and the cohort spec
        gold, metadata = load_gold_table_with_metadata(gold_dir)
    spec = cohort_spec(rule, metadata)
    # 8. what is published for every required date
    lo, hi = read_window(sim_date, spec, config)
    required = required_ticks(sim_date, spec, config)
    published = store.published_runs(client, config, lo, hi)
    all_facts = store.scoring_run_facts(client, published["scoring_run_id"], config)
    found = store.found_ticks(published, all_facts, config, required.all)
    if found.get(sim_date) != request.scoring_run_id:
        raise TickCheckError(
            "score", f"the run published for {sim_date.isoformat()} is {found.get(sim_date) or 'none'}, not this tick's {request.scoring_run_id}"
        )
    # 9. the cohort's predictions
    predictions = store.read_cohort_predictions(client, config, lo, hi, found.values())
    # 10b. the predictions' fingerprint against the gold actually loaded
    definitions = set(predictions["gold_fingerprint_definition"])
    if not predictions.empty:
        if None in definitions or any(d != d for d in definitions):  # NaN-safe
            raise TickCheckError("evaluate", "a prediction row carries no gold_fingerprint_definition")
        if len(definitions) != 1:
            raise TickCheckError("evaluate", f"prediction rows carry several fingerprint definitions {sorted(definitions)}")
        (definition,) = definitions
        fingerprint = compute_gold_fingerprint(definition, gold)
        record.gold_fingerprint, record.gold_fingerprint_definition = fingerprint, definition
        wrong = sorted(set(predictions["gold_fingerprint"]) - {fingerprint})
        if wrong:
            raise TickCheckError("evaluate", f"predictions were scored on gold {wrong}, not on this gold ({fingerprint})")
    # 11. the evaluation
    labels = gold[["encounter_id", "is_readmitted", "label_horizon"]]
    evaluation = evaluate_cohort(
        predictions[["encounter_id", "discharge_time", "risk_score"]], labels, sim_date=sim_date, rule=rule, spec=spec,
        found_ticks=set(found), required=required,
    )  # fmt: skip
    _copy_evaluation(record, evaluation)
    return evaluation


def _check_own_run(fact: dict, sim_date: date, config) -> None:
    problems = []
    if fact["status"] != "succeeded" or fact["published"] is not True:
        problems.append(f"status {fact['status']}, published {fact['published']}")
    if fact["run_date"] != sim_date:
        problems.append(f"run_date {fact['run_date']}, expected {sim_date.isoformat()}")
    if fact["model_bundle_id"] != config.model_bundle_id:
        problems.append(f"bundle {fact['model_bundle_id']}, expected {config.model_bundle_id}")
    if fact["window_days"] != config.window_days:
        problems.append(f"window_days {fact['window_days']}, expected {config.window_days}")
    if fact["top_n"] != config.top_n:
        problems.append(f"top_n {fact['top_n']}, expected {config.top_n}")
    if problems:
        raise TickCheckError("score", f"scoring run {fact['scoring_run_id']} has " + "; ".join(problems))


_EVALUATION_FIELDS = {
    "verdict": "verdict", "cohort_start": "cohort_start", "cohort_end": "cohort_end", "n_matured": "n_matured",
    "observed": "observed", "expected": "expected", "oe_ratio": "ratio", "oe_ci_low": "ci_low", "oe_ci_high": "ci_high",
    "auc": "auc", "n_label_after_sim": "n_label_after_sim", "n_immature": "n_immature",
    "expected_immature": "expected_immature", "coverage_required": "coverage_required", "coverage_found": "coverage_found",
}  # fmt: skip


def _copy_evaluation(record: store.MonitoringRecord, evaluation: Evaluation) -> None:
    for column, attribute in _EVALUATION_FIELDS.items():
        setattr(record, column, getattr(evaluation, attribute))


def _clear_evaluation(record: store.MonitoringRecord) -> None:
    for column in _EVALUATION_FIELDS:
        setattr(record, column, None)
