"""The 5-row sample Top-N table as GitHub Markdown.
See notes/eg-new-feature/readme-reliability-plot-and-sample-table-2026-10-09.md (Interfaces > topn_table.py).
"""

from __future__ import annotations

import math
import textwrap
from itertools import pairwise

import pandas as pd

REQUIRED_COLUMNS = ("rank", "risk_score", "discharge_time", "admission_reason_description", "patient_id")
_DESCRIPTION_WIDTH = 48
_PATIENT_WIDTH = 8
_HEADER = "| Rank | Risk score | Discharged | Admission reason | Patient |"
_ALIGNMENT = "|---:|---:|---|---|---|"


def _description(value: object) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return "(none)"
    text = " ".join(str(value).split())
    if len(text) > _DESCRIPTION_WIDTH:
        # Cut at a word boundary and mark the cut; a single over-long word is cut hard.
        shortened = textwrap.shorten(text, _DESCRIPTION_WIDTH, placeholder="…")
        text = shortened if shortened != "…" else text[: _DESCRIPTION_WIDTH - 1] + "…"
    return text.replace("|", "\\|")


def format_topn_markdown(predictions: pd.DataFrame, n: int = 5) -> str:
    """The n smallest-rank rows (ranks must be exactly 1..n, scores non-increasing). is_top_n/top_n are ignored."""
    missing = [c for c in REQUIRED_COLUMNS if c not in predictions.columns]
    if missing:
        raise ValueError(f"predictions lack required columns: {missing}")
    if len(predictions) < n:
        raise ValueError(f"need at least {n} predictions rows; got {len(predictions)}")
    top = predictions.sort_values("rank", kind="stable").head(n)
    if list(top["rank"]) != list(range(1, n + 1)):
        raise ValueError(f"the {n} smallest ranks must be exactly 1..{n}; got {list(top['rank'])}")
    scores = list(top["risk_score"])
    if any(math.isnan(score) for score in scores):
        raise ValueError(f"risk_score must not be NaN over ranks 1..{n}; got {scores}")
    if any(later > earlier for earlier, later in pairwise(scores)):
        raise ValueError(f"risk_score must be non-increasing over ranks 1..{n}; got {scores}")
    rows = [
        f"| {int(row.rank)} | {row.risk_score:.4f} | {row.discharge_time:%Y-%m-%d} | {_description(row.admission_reason_description)} "
        f"| {str(row.patient_id)[:_PATIENT_WIDTH]} |"
        for row in top.itertuples(index=False)
    ]
    return "\n".join([_HEADER, _ALIGNMENT, *rows]) + "\n"
