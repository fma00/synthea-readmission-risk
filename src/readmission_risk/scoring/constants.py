"""Constants shared by the scoring modules. Deliberately imports nothing from the rest of the package (only pathlib), so that
store.py (which stamps DEMO_NOTICE into the file metadata) and pipeline.py (which imports store.write_partition)
cannot form an import cycle. See notes/eg-new-feature/batch-scoring-cli-2026-09-20.md.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_CHAMPION_PATH = Path("deploy/champion.json")  # relative to the current working directory
DEFAULT_TOP_N = 10
PARTITION_TRAIN = "train"
PARTITION_TEST = "test"

# Qualitative on purpose: model metrics live in docs/results.md and go stale on retraining. Revisited in v2 slice 7 (the
# 200k champion): route-neutral, true for both the 10k model (its window was used during design) and the 200k model (its
# window was used by the promotion gate). Revisit again before real as-of scoring (slice-4 design note, follow-up 11).
DEMO_NOTICE = (
    "DEMO, not clinical guidance. Scores come from a weak model trained on synthetic Synthea data, and the ranking "
    "mostly reflects the admission reason (a reason-code-only lookup does as well). The rows are held-out discharges "
    "from the model's own test partition; that window was already used to select models, so it is not a virgin "
    "holdout. Not clinically validated."
)
