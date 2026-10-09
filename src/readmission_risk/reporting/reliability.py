"""The README reliability figure: per-bin Wilson intervals and a two-model plot in the house style.
See notes/eg-new-feature/readme-reliability-plot-and-sample-table-2026-10-09.md (Interfaces > reliability.py).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from matplotlib.figure import Figure

from .style import MODEL_DISPLAY_NAMES, MODEL_NAMES, Theme, new_axes

TITLE = "Reliability, test window (development set)"
_Z = 1.959964
_K_TOLERANCE = 1e-6


@dataclass(frozen=True)
class BinPoint:
    mean_predicted: float
    observed_fraction: float
    n: int
    n_positive: int
    ci_low: float
    ci_high: float


def wilson_interval(k: int, n: int, z: float = _Z) -> tuple[float, float]:
    """The Wilson score interval (low, high) for k positives in n rows. The textbook formula leaves floating-point
    residue (a lower bound of -1.7e-18 at k = 0, an upper bound of 0.9999999999999999 at k = n), so the result is clamped:
    exactly 0 at k = 0 and exactly 1 at k = n, inside [0, 1], and always bracketing k / n (errorbar rejects a negative yerr)."""
    if n < 1 or k < 0 or k > n:
        raise ValueError(f"need n >= 1 and 0 <= k <= n; got k={k}, n={n}")
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    low = 0.0 if k == 0 else max(0.0, centre - half)
    high = 1.0 if k == n else min(1.0, centre + half)
    return min(low, p), max(high, p)


def bin_points(curve: list[dict[str, float]]) -> list[BinPoint]:
    """Validates and converts evaluate_probabilities' calibration_curve records (mean_predicted, observed_fraction, n) and
    attaches Wilson intervals, sorted ascending by mean_predicted. The positive count is reconstructed as
    round(observed_fraction * n); that per-bin check validates the record format only -- load_curves' sum check is the
    only reconciliation between a curve and its run."""
    if not curve:
        raise ValueError("calibration curve is empty")
    points = []
    for record in curve:
        try:
            mean_predicted = float(record["mean_predicted"])
            observed = float(record["observed_fraction"])
            n_raw = record["n"]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"calibration record {record!r} lacks mean_predicted/observed_fraction/n") from exc
        if not (math.isfinite(mean_predicted) and math.isfinite(observed) and math.isfinite(float(n_raw))):
            raise ValueError(f"calibration record {record!r} has a non-finite value")
        n = int(n_raw)
        if n != float(n_raw) or n < 1:
            raise ValueError(f"calibration record {record!r}: n must be an integer >= 1")
        if not (0.0 <= mean_predicted <= 1.0 and 0.0 <= observed <= 1.0):
            raise ValueError(f"calibration record {record!r}: probabilities must lie in [0, 1]")
        k = round(observed * n)
        if abs(observed * n - k) > _K_TOLERANCE:
            raise ValueError(f"calibration record {record!r}: observed_fraction * n is not an integer count")
        low, high = wilson_interval(k, n)
        points.append(BinPoint(mean_predicted, observed, n, k, low, high))
    return sorted(points, key=lambda p: p.mean_predicted)


def build_reliability_figure(
    curves: Mapping[str, list[dict[str, float]]], theme: Theme, *, size: str = "half", height_cm: float = 7.0
) -> Figure:
    """Must be called inside house_style(theme), and the same block must draw and save the figure."""
    points = {name: bin_points(curves[name]) for name in MODEL_NAMES if name in curves}
    if not points:
        raise ValueError(f"no curves for any of {MODEL_NAMES}")
    limit = 1.05 * max(max(max(p.ci_high, p.mean_predicted) for p in pts) for pts in points.values())
    fig, ax = new_axes(theme, size, height_cm)
    (diagonal,) = ax.plot([0, limit], [0, limit], linestyle="--", color=theme.grey, linewidth=1, label="perfectly calibrated")
    containers = []
    for name, pts in points.items():
        x = np.array([p.mean_predicted for p in pts])
        y = np.array([p.observed_fraction for p in pts])
        lower = np.maximum(y - np.array([p.ci_low for p in pts]), 0.0)
        upper = np.maximum(np.array([p.ci_high for p in pts]) - y, 0.0)
        containers.append(
            ax.errorbar(
                x,
                y,
                yerr=[lower, upper],
                marker=theme.markers[name],
                markersize=3,
                linewidth=1,
                elinewidth=1,
                capsize=1.5,
                capthick=1,
                color=theme.series[name],
                label=MODEL_DISPLAY_NAMES[name],
            )
        )
    ax.set_xlim(0, limit)
    ax.set_ylim(0, limit)
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Observed fraction positive")
    ax.set_title(TITLE)
    ax.legend(handles=[*containers, diagonal], loc="lower right", frameon=False)
    return fig
