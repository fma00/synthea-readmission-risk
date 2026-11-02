"""The house colour tokens (Okabe-Ito series on GitHub's light/dark ink and grey), shared by the matplotlib figures (style.py)
and the dashboard's Altair charts (PRD v3 FR19). Standard library only. See
notes/eg-new-feature/v3-slice3-dashboard-app-2026-11-01.md (Interfaces section 2).
"""

from __future__ import annotations

from dataclasses import dataclass


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


def chart_colors(theme: Theme) -> dict[str, str]:
    """The dashboard chart roles: ink, muted (grey), ok (the champion's series colour), alert, and the opaque background."""
    return {
        "ink": theme.ink,
        "muted": theme.grey,
        "ok": theme.series["logistic_regression"],
        "alert": theme.series["xgboost_calibrated"],
        "background": theme.preview_background,
    }
