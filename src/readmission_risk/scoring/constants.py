"""Constants shared by the scoring modules. Deliberately imports nothing from the rest of the package, so that
store.py (which stamps DEMO_NOTICE into the file metadata) and pipeline.py (which imports store.write_partition)
cannot form an import cycle. See notes/eg-new-feature/batch-scoring-cli-2026-09-20.md.
"""

from __future__ import annotations

DEFAULT_MODEL = "logistic_regression"
DEFAULT_TOP_N = 10
PARTITION_TRAIN = "train"
PARTITION_TEST = "test"

# Qualitative on purpose: model metrics live in docs/results.md and go stale on retraining. It is dataset-specific -- it
# says the rows are a development set -- so it must be revisited before this scorer is pointed at the untouched v2
# population or used for real as-of scoring (design note, limit (e) and follow-up 11).
DEMO_NOTICE = (
    "DEMO, not clinical guidance. Scores come from a weak model trained on synthetic Synthea data, and the ranking "
    "mostly reflects the admission reason (a reason-code-only lookup does as well). The rows are held-out "
    "development-set discharges: the test window was used while designing the models and the label, so it is not a "
    "virgin holdout. Not clinically validated."
)
