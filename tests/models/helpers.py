"""Shared test helpers for the model-training slice: a synthetic gold-table generator with a planted signal,
plus tiny hand-written-frame builders for the split boundary tests. (Plain module, not conftest.py, which
holds fixtures only.) See notes/eg-new-feature/model-training-2026-09-19.md (Verification criteria).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from readmission_risk.models.data import EXPECTED_GOLD_COLUMNS, LABEL_COLUMN
from readmission_risk.pipeline.gold_metadata import (
    LABEL_DEFINITION,
    PLANNED_PROCEDURE_CODES,
    GoldMetadata,
    write_gold_metadata,
)

REFERENCE_DATE = date(2026, 9, 16)
TEST_START = date(2023, 1, 1)
REFERENCE_DATE_STR = "20260916"
TEST_START_STR = "20230101"


def make_gold_frame(
    n_patients: int = 1200,
    seed: int = 0,
    *,
    planted_signal: bool = True,
    first_admission: str = "2018-01-01",
    last_admission: str = "2026-06-30",
) -> pd.DataFrame:
    """Schema-exact 23-column gold frame. ONE rng, drawn in a pinned order (each draw vectorized over all rows):
    admission counts per patient, index_start offsets, LOS, the numeric features in spec order, the categoricals,
    and the label LAST. planted_signal=True: readmission probability rises with prior_inpatient_count and depends
    on the reason code (a null reason strongly implies negative), mirroring the real data; False: label is
    Bernoulli(0.2) independent of every feature."""
    rng = np.random.default_rng(seed)
    k = 1 + np.minimum(rng.poisson(1.2, size=n_patients), 5)
    patient_index = np.repeat(np.arange(n_patients), k)
    n_rows = len(patient_index)

    first_ts = pd.Timestamp(first_admission, tz="UTC")
    last_ts = pd.Timestamp(last_admission, tz="UTC")
    span_seconds = int((last_ts - first_ts).total_seconds())
    starts = first_ts + pd.to_timedelta(rng.integers(0, span_seconds, size=n_rows), unit="s")
    los_days = 1 + np.minimum(rng.exponential(3.0, size=n_rows), 29)
    stops = starts + pd.to_timedelta(np.round(los_days * 86400).astype("int64"), unit="s")

    prior_inpatient = rng.poisson(1.0, size=n_rows)
    prior_encounter = prior_inpatient + rng.poisson(3, size=n_rows)
    prior_emergency = rng.poisson(0.5, size=n_rows)
    active_condition = rng.poisson(8, size=n_rows)
    active_medication = rng.poisson(4, size=n_rows)
    procedure_count = rng.poisson(10, size=n_rows)
    active_careplan = rng.poisson(2, size=n_rows)
    observation_count = rng.poisson(100, size=n_rows)
    age = rng.uniform(18, 90, size=n_rows)
    org_utilization = rng.integers(200, 40001, size=n_rows)

    payer = rng.choice(["GOVERNMENT", "PRIVATE", "NO_INSURANCE"], size=n_rows, p=[0.55, 0.30, 0.15])
    gender = rng.choice(["M", "F"], size=n_rows, p=[0.5, 0.5])
    race = rng.choice(["white", "black", "asian"], size=n_rows, p=[0.7, 0.2, 0.1])
    ethnicity = rng.choice(["nonhispanic", "hispanic"], size=n_rows, p=[0.85, 0.15])
    marital = rng.choice(np.array(["M", "S", "D", "W", None], dtype=object), size=n_rows, p=[0.45, 0.25, 0.12, 0.08, 0.10])
    code = rng.choice(np.array(["R_HIGH", "R_MID", "R_LOW", None], dtype=object), size=n_rows, p=[0.2, 0.3, 0.3, 0.2])

    if planted_signal:
        logit = (
            -2.5
            + 0.8 * prior_inpatient
            + 1.6 * (code == "R_HIGH")
            + 0.6 * (code == "R_MID")
            - 4.0 * np.array([c is None for c in code])
        )
        p = 1.0 / (1.0 + np.exp(-logit))
    else:
        p = np.full(n_rows, 0.2)
    label = (rng.random(n_rows) < p).astype("int64")

    df = pd.DataFrame(
        {
            "patient_id": [f"p{i:05d}" for i in patient_index],
            "encounter_id": [f"e{i:07d}" for i in range(n_rows)],
            "index_start": starts,
            "index_stop": stops,
            "is_readmitted": label,
            "admission_reason_code": pd.Series(code, dtype=object),
            "admission_reason_description": pd.Series(
                [None if c is None else f"desc {c}" for c in code], dtype=object
            ),
            "payer_ownership": pd.Series(payer, dtype=object),
            "provider_specialty": pd.Series(["GENERAL PRACTICE"] * n_rows, dtype=object),
            "organization_utilization": org_utilization.astype("int64"),
            "prior_encounter_count": prior_encounter.astype("int64"),
            "prior_inpatient_count": prior_inpatient.astype("int64"),
            "prior_emergency_count": prior_emergency.astype("int64"),
            "active_condition_count": active_condition.astype("int64"),
            "active_medication_count": active_medication.astype("int64"),
            "procedure_count_window": procedure_count.astype("int64"),
            "active_careplan_count": active_careplan.astype("int64"),
            "observation_count_window": observation_count.astype("int64"),
            "age_at_index_years": age.astype("float64"),
            "gender": pd.Series(gender, dtype=object),
            "race": pd.Series(race, dtype=object),
            "ethnicity": pd.Series(ethnicity, dtype=object),
            "marital_status": pd.Series(marital, dtype=object),
        }
    )
    return df[list(EXPECTED_GOLD_COLUMNS)]


def row(
    patient_id: str, start: str, stop: str, label: int = 0, encounter_id: str | None = None
) -> dict:
    """One hand-written gold row (dict of ALL 23 columns). start/stop are ISO-8601 UTC strings with a trailing
    "Z". encounter_id defaults to f"e-{patient_id}-{start}" (unique per patient and start instant)."""
    return {
        "patient_id": patient_id,
        "encounter_id": encounter_id if encounter_id is not None else f"e-{patient_id}-{start}",
        "index_start": start,
        "index_stop": stop,
        "is_readmitted": label,
        "admission_reason_code": "R_LOW",
        "admission_reason_description": "desc R_LOW",
        "payer_ownership": "PRIVATE",
        "provider_specialty": "GENERAL PRACTICE",
        "organization_utilization": 1000,
        "prior_encounter_count": 0,
        "prior_inpatient_count": 0,
        "prior_emergency_count": 0,
        "active_condition_count": 0,
        "active_medication_count": 0,
        "procedure_count_window": 0,
        "active_careplan_count": 0,
        "observation_count_window": 0,
        "age_at_index_years": 50.0,
        "gender": "F",
        "race": "white",
        "ethnicity": "nonhispanic",
        "marital_status": "M",
    }


def frame(rows: list[dict]) -> pd.DataFrame:
    """Builds a gold-shaped frame from row() dicts, converting the timestamps to tz-aware UTC."""
    df = pd.DataFrame(rows)[list(EXPECTED_GOLD_COLUMNS)]
    for col in ("index_start", "index_stop"):
        df[col] = pd.to_datetime(df[col], utc=True)
    return df


def pad_rows() -> list[dict]:
    """Four rows that keep every hand-written split fixture valid (the split raises ValueError on an empty or
    single-class partition): one negative and one positive TRAIN row and one negative and one positive TEST row,
    all far from any boundary under test."""
    return [
        row("pad_tr_0", "2019-01-01T00:00:00Z", "2019-01-05T00:00:00Z", 0),
        row("pad_tr_1", "2019-01-01T00:00:00Z", "2019-01-05T00:00:00Z", 1),
        row("pad_te_0", "2024-01-01T00:00:00Z", "2024-01-05T00:00:00Z", 0),
        row("pad_te_1", "2024-01-01T00:00:00Z", "2024-01-05T00:00:00Z", 1),
    ]


def write_test_gold_metadata(directory: Path, df: pd.DataFrame, **overrides) -> Path:
    """Writes a valid _gold_metadata.json for `df` via the PRODUCTION write_gold_metadata. Defaults: label_definition=LABEL_DEFINITION,
    reference_date=REFERENCE_DATE_STR, readmission_window_days=30, lookback_years=1, planned_procedure_codes=PLANNED_PROCEDURE_CODES,
    n_rows=len(df), n_positive=the number of label==1 rows (0 if the frame has no label column), n_planned_stays=0,
    n_continuation_stays=0, n_nonterminal_stays=0, and n_inpatient_stays = the FINAL n_rows + the FINAL n_planned_stays (both after
    overrides are applied, so a test planting n_rows=len(df)+1 or a nonzero n_planned_stays still yields metadata that passes
    validation). Any keyword in `overrides` replaces the matching default (used to plant mismatches). Counts come from the frame
    actually written, so the malformed-frame cases still reach their intended load_gold_table error."""
    values = {
        "label_definition": LABEL_DEFINITION,
        "reference_date": REFERENCE_DATE_STR,
        "readmission_window_days": 30,
        "lookback_years": 1,
        "planned_procedure_codes": PLANNED_PROCEDURE_CODES,
        "n_rows": len(df),
        "n_positive": int((df[LABEL_COLUMN] == 1).sum()) if LABEL_COLUMN in df.columns else 0,
        "n_planned_stays": 0,
        "n_continuation_stays": 0,
        "n_nonterminal_stays": 0,
    }
    values.update(overrides)
    values.setdefault("n_inpatient_stays", values["n_rows"] + values["n_planned_stays"])
    return write_gold_metadata(Path(directory), GoldMetadata(**values))
