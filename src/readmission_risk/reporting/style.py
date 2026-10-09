"""The owner's house matplotlib style, applied through rc_context and the Figure API (no pyplot global state).
See notes/eg-new-feature/readme-reliability-plot-and-sample-table-2026-10-09.md (Interfaces > style.py).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import matplotlib
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

CM = 1 / 2.54
DPI = 1200
MODEL_NAMES = ("logistic_regression", "xgboost_calibrated")
MODEL_DISPLAY_NAMES = {"logistic_regression": "Logistic regression", "xgboost_calibrated": "Calibrated XGBoost"}

# The owner's parameters verbatim (matplotlib coerces the string font sizes), then "linewidth always 1", then the
# determinism setting and GitHub's body font stack (DejaVu Sans last so the local layout measuring has a real face).
HOUSE_RC: dict[str, object] = {
    "legend.fontsize": "6",
    "axes.labelsize": "6",
    "axes.titlesize": "9",
    "xtick.labelsize": "6",
    "ytick.labelsize": "6",
    "font.weight": "light",
    "font.style": "normal",
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "lines.linewidth": 1,
    "axes.linewidth": 1,
    "xtick.major.width": 1,
    "ytick.major.width": 1,
    "patch.linewidth": 1,
    "lines.markeredgewidth": 1,
    "svg.hashsalt": "readmission-risk",
    "font.family": "sans-serif",
    "font.sans-serif": ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "Noto Sans", "Helvetica", "Arial", "DejaVu Sans"],
}


@dataclass(frozen=True)
class Theme:
    name: str
    ink: str
    grey: str
    series: dict[str, str]
    markers: dict[str, str]
    preview_background: str


_MARKERS = {"logistic_regression": "o", "xgboost_calibrated": "s"}
THEMES: dict[str, Theme] = {
    "light": Theme(
        "light",
        ink="#1f2328",
        grey="#6e7781",
        series={"logistic_regression": "#0072B2", "xgboost_calibrated": "#D55E00"},
        markers=_MARKERS,
        preview_background="#ffffff",
    ),
    "dark": Theme(
        "dark",
        ink="#e6edf3",
        grey="#8b949e",
        series={"logistic_regression": "#56B4E9", "xgboost_calibrated": "#E69F00"},
        markers=_MARKERS,
        preview_background="#0d1117",
    ),
}


class _DropLightWeightWarning(logging.Filter):
    """matplotlib logs 'findfont: Failed to find font weight light for <family>, now using <n>.' on machines whose font
    has no light face (the owner's style asks for font.weight light). Drops only that record."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.getMessage().startswith("findfont: Failed to find font weight light")


def figure_size_cm(size: Literal["full", "half"], height_cm: float) -> tuple[float, float]:
    return (17.0 if size == "full" else 9.0, float(height_cm))


@contextmanager
def house_style(theme: Theme) -> Iterator[None]:
    """rc_context with the house settings and the theme colours, plus a scoped logging filter that is removed on exit.
    The whole build -> draw -> save sequence of a figure must sit inside one block: svg.fonttype, svg.hashsalt and the
    lazily created tick properties are read at draw/save time."""
    rc = {
        **HOUSE_RC,
        "text.color": theme.ink,
        "axes.edgecolor": theme.ink,
        "axes.labelcolor": theme.ink,
        "xtick.color": theme.ink,
        "ytick.color": theme.ink,
    }
    font_logger = logging.getLogger("matplotlib.font_manager")
    log_filter = _DropLightWeightWarning()
    font_logger.addFilter(log_filter)
    try:
        with matplotlib.rc_context(rc):
            yield
    finally:
        font_logger.removeFilter(log_filter)


def new_axes(theme: Theme, size: Literal["full", "half"], height_cm: float):
    """Must be called inside house_style. A canvas is attached so fig.canvas.draw() runs the constrained layout (on a
    bare Figure it is a no-op)."""
    width, height = figure_size_cm(size, height_cm)
    fig = Figure(figsize=(width * CM, height * CM), dpi=DPI, layout="constrained")
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.spines["right"].set_visible(False)
    ax.spines["top"].set_visible(False)
    ax.yaxis.set_major_locator(MaxNLocator(5))
    ax.xaxis.set_major_locator(MaxNLocator(5))
    fig.patch.set_alpha(0.0)
    ax.patch.set_alpha(0.0)
    return fig, ax


def save_figure(
    fig: Figure,
    path: Path,
    *,
    metadata: dict[str, str | None] | None = None,
    dpi: int | None = None,
    facecolor: str | None = None,
) -> None:
    """The format comes from the path suffix. savefig(facecolor=...) does not override a figure patch whose alpha is 0
    (verified on matplotlib 3.11.2), so a given facecolor is applied by temporarily making the figure patch opaque."""
    previous = (fig.patch.get_facecolor(), fig.patch.get_alpha())
    if facecolor is not None:
        fig.patch.set_facecolor(facecolor)
        fig.patch.set_alpha(1.0)
    try:
        fig.savefig(path, metadata=metadata, dpi=dpi if dpi is not None else "figure")
    finally:
        fig.patch.set_facecolor(previous[0])
        fig.patch.set_alpha(previous[1])
