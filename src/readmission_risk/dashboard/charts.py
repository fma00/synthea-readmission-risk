"""The monitoring O/E chart (PRD v3 FR13, owner decisions S10, S14): one Altair layer per role, status by shape and fill as
well as colour, dates in UTC, an opaque background from the house tokens. See
notes/eg-new-feature/v3-slice3-dashboard-app-2026-11-01.md (Interfaces section 6).
"""

from __future__ import annotations

import altair as alt
import pandas as pd

LAYERS = ("ci", "band_low", "band_high", "points_ok", "points_alert", "points_muted", "rug", "labels")
MUTED_VERDICTS = ("insufficient_data", "incomplete_cohort")
KNOWN_VERDICTS = ("ok", "alert", *MUTED_VERDICTS)
LEGEND_TEXT = (
    "● ok · ◆ alert (labelled) · ○ insufficient data or incomplete cohort (hollow, grey) · △ on the bottom edge: a week "
    "without an O/E value · shaded bar: 95% CI, in the point's colour · dashed: alert band"
)
HEIGHT = 320
RUG_Y = 312  # pixels from the top of the plot: its bottom edge
Y_TITLE = "Observed / expected"
TOOLTIP = [
    alt.Tooltip("sim_week:N", title="Simulated week"),
    alt.Tooltip("verdict_label:N", title="Verdict"),
    alt.Tooltip("oe_ratio:Q", title="O/E", format=".3f"),
    alt.Tooltip("oe_ci_low:Q", title="95% CI low", format=".3f"),
    alt.Tooltip("oe_ci_high:Q", title="95% CI high", format=".3f"),
    alt.Tooltip("observed:Q", title="Observed"),
    alt.Tooltip("expected:Q", title="Expected", format=".1f"),
    alt.Tooltip("min_positives_floor:Q", title="Positives floor"),
    alt.Tooltip("cadence_label:N", title="Cadence"),
]


def _x() -> alt.X:
    # UTC: Vega-Lite shows dates in the viewer's local zone otherwise, and a viewer west of UTC would see the previous day.
    return alt.X("sim_date:T", timeUnit="utcyearmonthdate", title="Simulated week", axis=alt.Axis(format="%Y-%m-%d"))


def _only(verdicts) -> alt.FieldOneOfPredicate:
    return alt.FieldOneOfPredicate(field="verdict", oneOf=list(verdicts))


def oe_chart(points: pd.DataFrame, colors: dict[str, str]) -> alt.LayerChart:
    plotted = alt.Chart(points[points["plotted"]])
    every = alt.Chart(points)
    unplotted = alt.Chart(points[~points["plotted"]])
    y = alt.Y("oe_ratio:Q", title=Y_TITLE)
    ci = plotted.mark_rule(strokeWidth=6, opacity=0.25).encode(
        x=_x(), y=alt.Y("oe_ci_low:Q", title=Y_TITLE), y2="oe_ci_high:Q",
        color=alt.Color("verdict:N", legend=None, scale=alt.Scale(
            domain=list(KNOWN_VERDICTS), range=[colors["ok"], colors["alert"], colors["muted"], colors["muted"]]
        )),
    ).transform_filter(_only(KNOWN_VERDICTS))  # fmt: skip
    band_low = every.mark_line(strokeDash=[4, 3], color=colors["muted"]).encode(x=_x(), y=alt.Y("band_low:Q", title=Y_TITLE))
    band_high = every.mark_line(strokeDash=[4, 3], color=colors["muted"]).encode(x=_x(), y=alt.Y("band_high:Q", title=Y_TITLE))
    points_ok = plotted.mark_point(shape="circle", filled=True, size=60, color=colors["ok"]).encode(
        x=_x(), y=y, tooltip=TOOLTIP
    ).transform_filter(_only(["ok"]))
    points_alert = plotted.mark_point(shape="diamond", filled=True, size=110, color=colors["alert"]).encode(
        x=_x(), y=y, tooltip=TOOLTIP
    ).transform_filter(_only(["alert"]))
    points_muted = plotted.mark_point(shape="circle", filled=False, size=60, color=colors["muted"]).encode(
        x=_x(), y=y, tooltip=TOOLTIP
    ).transform_filter(_only(MUTED_VERDICTS))
    rug = unplotted.mark_point(shape="triangle-up", filled=False, size=50, color=colors["muted"]).encode(
        x=_x(), y=alt.value(RUG_Y), tooltip=TOOLTIP
    )
    labels = plotted.mark_text(dy=-10, color=colors["ink"]).encode(
        x=_x(), y=y, text=alt.value("alert")
    ).transform_filter(_only(["alert"]))
    return (
        alt.layer(ci, band_low, band_high, points_ok, points_alert, points_muted, rug, labels)
        .properties(height=HEIGHT)
        .configure(background=colors["background"])
        .configure_axis(labelColor=colors["ink"], titleColor=colors["ink"], gridColor=colors["muted"], gridOpacity=0.3, domainColor=colors["ink"])
    )
