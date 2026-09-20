"""The plain-text report the CLI prints (pure; no rich). See notes/eg-new-feature/batch-scoring-cli-2026-09-20.md
(Interfaces > report.py). Exact column widths and blank lines are not pinned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .constants import DEMO_NOTICE

if TYPE_CHECKING:
    from .pipeline import ScoringResult

_DESCRIPTION_WIDTH = 48
_HEADER = ("rank", "risk_score", "discharged", "admission_reason", "patient_id", "encounter_id")


def _table(result: ScoringResult, n_shown: int) -> list[str]:
    top = result.predictions.head(n_shown)
    rows = []
    for record in top.itertuples(index=False):
        description = record.admission_reason_description
        description = "(none)" if description is None else str(description)[:_DESCRIPTION_WIDTH]
        rows.append(
            (
                str(record.rank),
                f"{record.risk_score:.4f}",
                record.discharge_time.strftime("%Y-%m-%d"),
                description,
                str(record.patient_id),
                str(record.encounter_id),
            )
        )
    widths = [max(len(_HEADER[i]), *(len(r[i]) for r in rows)) for i in range(len(_HEADER))]
    return ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in (_HEADER, *rows)]


def format_report(result: ScoringResult) -> str:
    run, predictions = result.run, result.predictions
    n = len(predictions)
    top_n = int(predictions["top_n"].iloc[0])
    n_shown = min(top_n, n)
    commit = run.git_commit[:7] if run.git_commit else "unknown"
    lines = [
        DEMO_NOTICE,
        "",
        f"model: {run.model_name} run_id={run.run_id} (trained at commit {commit}, working tree {run.git_dirty or 'unknown'})",
        (
            f"window: {result.window_start.isoformat()} to {result.window_end.isoformat()} (end exclusive), "
            f"{n} held-out discharges scored"
        ),
        "",
        f"Top {n_shown} of {n} scored discharges:",
        *_table(result, n_shown),
        "",
        f"Wrote {result.partition_path}",
    ]
    observed = result.observed
    if observed is not None:
        lift = "n/a" if observed.lift is None else f"{observed.lift:.1f}"
        lines += [
            "",
            (
                "Observed outcomes (opt-in development-set sanity check; NOT written to the output file; labels are from the "
                "gold table, i.e. later than the run date):"
            ),
            f"  whole batch: {observed.n_positive_batch} readmitted of {observed.n_batch} ({observed.batch_positive_rate:.1%})",
            (
                f"  Top {observed.n_top}: {observed.n_positive_top} readmitted, precision@{observed.n_top} "
                f"{observed.precision_at_n:.2f}, lift {lift}"
            ),
            "  Counts this small are not evidence of model quality.",
        ]
    return "\n".join(lines) + "\n"
