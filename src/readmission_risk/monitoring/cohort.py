"""Cohorts on the simulated clock (pure). The matured cohort of sim date S is every scored discharge whose 30-day outcome
window closed during the 13 simulated weeks before S; membership depends only on the discharge date, never on the
outcome. The immature cohort is the last 30 days' discharges (label-free expected positives). A verdict other than
incomplete_cohort needs all 18 ticks those ranges come from. See
notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md (Interfaces > monitoring/cohort.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from readmission_risk.pipeline.gold_metadata import GoldMetadata

from .config import MonitoringConfig
from .rule import AlertRule, evaluate_rule, ratio_and_ci

LABEL_WINDOW_DAYS = 30  # the design (18 ticks, warm-up, preflight) is sized for these two values
COHORT_DAYS = 91


@dataclass(frozen=True)
class CohortSpec:
    label_window_days: int
    cohort_days: int


@dataclass(frozen=True)
class RequiredTicks:
    matured: tuple[date, ...]
    immature: tuple[date, ...]

    @property
    def all(self) -> tuple[date, ...]:
        return tuple(sorted(set(self.matured) | set(self.immature)))


@dataclass(frozen=True)
class Evaluation:
    sim_date: date
    cohort_start: date
    cohort_end: date  # exclusive
    n_matured: int
    observed: int
    expected: float
    ratio: float | None
    ci_low: float | None
    ci_high: float | None
    auc: float | None
    n_label_after_sim: int
    n_immature: int | None
    expected_immature: float | None
    coverage_required: int
    coverage_found: int
    verdict: str


def cohort_spec(rule: AlertRule, metadata: GoldMetadata) -> CohortSpec:
    spec = CohortSpec(label_window_days=metadata.readmission_window_days, cohort_days=7 * rule.matured_cohort_weeks)
    if (spec.label_window_days, spec.cohort_days) != (LABEL_WINDOW_DAYS, COHORT_DAYS):
        raise ValueError(
            f"the monitoring design is sized for a {LABEL_WINDOW_DAYS}-day label window and a {COHORT_DAYS}-day cohort; got "
            f"{spec.label_window_days} and {spec.cohort_days} (a design change)"
        )
    return spec


def _sim_end(sim_date: date) -> pd.Timestamp:
    return pd.Timestamp((sim_date + timedelta(days=1)).isoformat(), tz="UTC")


def ranges(sim_date: date, spec: CohortSpec) -> tuple[tuple[pd.Timestamp, pd.Timestamp], tuple[pd.Timestamp, pd.Timestamp]]:
    """((matured_start, matured_end), (immature_start, immature_end)), each [start, end)."""
    end = _sim_end(sim_date)
    label = pd.Timedelta(days=spec.label_window_days)
    return (end - label - pd.Timedelta(days=spec.cohort_days), end - label), (end - label, end)


def required_ticks(sim_date: date, spec: CohortSpec, config: MonitoringConfig) -> RequiredTicks:
    """Grid-spaced dates S - 7k (k >= 0) whose window [S_j - 6 d, S_j + 1 d) overlaps each range. Dates before
    first_sim_date are included; they can never be found."""
    (m_lo, m_hi), (i_lo, i_hi) = ranges(sim_date, spec)
    matured, immature = [], []
    k = 0
    while True:
        s_j = sim_date - timedelta(days=config.step_days * k)
        w_lo = pd.Timestamp((s_j - timedelta(days=config.window_days - 1)).isoformat(), tz="UTC")
        w_hi = _sim_end(s_j)
        if w_hi <= m_lo:
            break
        if w_lo < m_hi and w_hi > m_lo:
            matured.append(s_j)
        if w_lo < i_hi and w_hi > i_lo:
            immature.append(s_j)
        k += 1
    return RequiredTicks(matured=tuple(sorted(matured)), immature=tuple(sorted(immature)))


def read_window(sim_date: date, spec: CohortSpec, config: MonitoringConfig) -> tuple[date, date]:
    required = required_ticks(sim_date, spec, config).all
    return required[0], sim_date


def evaluate_cohort(
    predictions: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    sim_date: date,
    rule: AlertRule,
    spec: CohortSpec,
    found_ticks: set[date],
    required: RequiredTicks,
) -> Evaluation:
    """predictions: encounter_id, discharge_time (tz-aware UTC), risk_score, one row per encounter; rows outside both
    ranges (e.g. dates after sim_date) are ignored. labels: encounter_id, is_readmitted, label_horizon (gold). Neither is
    mutated."""
    from sklearn.metrics import roc_auc_score

    missing = [c for c in ("encounter_id", "discharge_time", "risk_score") if c not in predictions.columns]
    if missing:
        raise ValueError(f"predictions are missing column(s) {missing}")
    if predictions["encounter_id"].duplicated().any():
        raise ValueError("predictions hold more than one row for an encounter_id")
    if not isinstance(predictions["discharge_time"].dtype, pd.DatetimeTZDtype):
        raise ValueError(f"discharge_time must be tz-aware; got {predictions['discharge_time'].dtype}")  # noqa: TRY004 -- one exception type for bad input
    (m_lo, m_hi), (i_lo, i_hi) = ranges(sim_date, spec)
    stop = predictions["discharge_time"]
    matured = predictions.loc[(stop >= m_lo) & (stop < m_hi), ["encounter_id", "risk_score"]].sort_values("encounter_id")
    immature = predictions.loc[(stop >= i_lo) & (stop < i_hi), ["encounter_id", "risk_score"]].sort_values("encounter_id")

    joined = matured.merge(labels[["encounter_id", "is_readmitted", "label_horizon"]], on="encounter_id", how="left")
    unlabelled = int(joined["is_readmitted"].isna().sum())
    if unlabelled:
        raise ValueError(f"{unlabelled} matured prediction(s) have no gold label")
    n_matured = len(joined)
    observed = int(joined["is_readmitted"].astype("int64").sum())
    expected = math.fsum(joined["risk_score"].astype("float64"))
    n_label_after_sim = int((pd.to_datetime(joined["label_horizon"], utc=True) >= _sim_end(sim_date)).sum())
    y = joined["is_readmitted"].astype("int64")
    auc = float(roc_auc_score(y, joined["risk_score"])) if y.nunique() == 2 else None

    found = set(found_ticks)
    complete = set(required.all) <= found
    immature_complete = set(required.immature) <= found
    ratio = ci_low = ci_high = None
    if not complete:
        verdict = "incomplete_cohort"
        computed = ratio_and_ci(observed, expected)
        if computed is not None:
            ratio, ci_low, ci_high = computed
    elif n_matured == 0:
        verdict = "insufficient_data"
    else:
        result = evaluate_rule(observed, expected, rule)
        ratio, ci_low, ci_high, verdict = result.ratio, result.ci_low, result.ci_high, result.verdict
    return Evaluation(
        sim_date=sim_date,
        cohort_start=m_lo.date(),
        cohort_end=m_hi.date(),
        n_matured=n_matured,
        observed=observed,
        expected=expected,
        ratio=ratio,
        ci_low=ci_low,
        ci_high=ci_high,
        auc=auc,
        n_label_after_sim=n_label_after_sim,
        n_immature=len(immature) if immature_complete else None,
        expected_immature=math.fsum(immature["risk_score"].astype("float64")) if immature_complete else None,
        coverage_required=len(required.all),
        coverage_found=len(set(required.all) & found),
        verdict=verdict,
    )
