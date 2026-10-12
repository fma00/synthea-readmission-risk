"""Gold-metadata test helpers with no mlflow/sklearn dependency (v2 slice 5): CI's pipeline-dataproc-runtime job runs
tests/pipeline under requirements-dataproc-3.0.txt, which has neither, and the FR12 agreement test there writes v2
metadata. tests/models/helpers.py re-exports these. Imports: stdlib, pandas and readmission_risk.pipeline only
(tests/pipeline/test_fingerprint.py::test_gold_helpers_import_no_mlflow checks it).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from readmission_risk.pipeline.fingerprint import rowhash_fingerprint
from readmission_risk.pipeline.gold_metadata import (
    FINGERPRINT_DEFINITION_ROWHASH_V1,
    LABEL_DEFINITION,
    PLANNED_PROCEDURE_CODES,
    GoldMetadata,
    write_gold_metadata,
)

REFERENCE_DATE_STR = "20260916"
_LABEL_COLUMN = "is_readmitted"


def write_gold_metadata_for_frame(directory: Path, df: pd.DataFrame, *, version: int = 1, **overrides) -> Path:
    """Writes a valid _gold_metadata.json for `df` via the PRODUCTION write_gold_metadata. Defaults: label_definition=LABEL_DEFINITION,
    reference_date=REFERENCE_DATE_STR, readmission_window_days=30, lookback_years=1, planned_procedure_codes=PLANNED_PROCEDURE_CODES,
    n_rows=len(df), n_positive=the number of label==1 rows (0 if the frame has no label column), n_planned_stays=0,
    n_continuation_stays=0, n_nonterminal_stays=0, and n_inpatient_stays = the FINAL n_rows + the FINAL n_planned_stays (both after
    overrides are applied, so a test planting n_rows=len(df)+1 or a nonzero n_planned_stays still yields metadata that passes
    validation). version=2 adds the five v2 fields: gold_fingerprint = rowhash_fingerprint(df) (so `df` must have exactly the 24
    gold columns), the rowhash definition, spark_version="test", generation_summary={"reference_date": <reference_date>} and
    build_provenance={}. Any keyword in `overrides` replaces the matching default (used to plant mismatches); a
    `metadata_version` override that contradicts `version` raises ValueError. Counts come from the frame actually written, so
    the malformed-frame cases still reach their intended load_gold_table error."""
    if "metadata_version" in overrides and overrides["metadata_version"] != version:
        raise ValueError(f"metadata_version={overrides['metadata_version']!r} contradicts version={version!r}")
    values = {
        "metadata_version": version,
        "label_definition": LABEL_DEFINITION,
        "reference_date": REFERENCE_DATE_STR,
        "readmission_window_days": 30,
        "lookback_years": 1,
        "planned_procedure_codes": PLANNED_PROCEDURE_CODES,
        "n_rows": len(df),
        "n_positive": int((df[_LABEL_COLUMN] == 1).sum()) if _LABEL_COLUMN in df.columns else 0,
        "n_planned_stays": 0,
        "n_continuation_stays": 0,
        "n_nonterminal_stays": 0,
    }
    if version == 2:
        values.update(
            gold_fingerprint=rowhash_fingerprint(df),
            gold_fingerprint_definition=FINGERPRINT_DEFINITION_ROWHASH_V1,
            spark_version="test",
            build_provenance={},
        )
    values.update(overrides)
    values.setdefault("n_inpatient_stays", values["n_rows"] + values["n_planned_stays"])
    if version == 2:
        values.setdefault("generation_summary", {"reference_date": values["reference_date"]})
    return write_gold_metadata(Path(directory), GoldMetadata(**values))
