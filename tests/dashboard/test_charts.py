"""The O/E chart's spec (v3 slice 3, Verification V-C; owner decisions S10, S14)."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

import pytest

from readmission_risk.dashboard import charts
from readmission_risk.dashboard import present as p
from readmission_risk.reporting.palette import THEMES, chart_colors

VERDICTS = ["ok", "alert", "incomplete_cohort", "incomplete_cohort", "insufficient_data", "ok"]
RATIOS = [1.0, 1.9, None, 0.9, 1.2, 1.05]


def _rows(extra=()):
    rows = []
    for i, (verdict, ratio) in enumerate([*zip(VERDICTS, RATIOS, strict=True), *extra], start=1):
        rows.append({
            "monitoring_run_id": f"m{i}", "status": "succeeded", "started_at": datetime(2026, 10, 20, i, tzinfo=UTC),
            "sim_date": date(2023, 9, 4 + 7 * (i - 1)) if i < 5 else date(2023, 10, 2 + 7 * (i - 5)), "verdict": verdict,
            "oe_ratio": ratio, "oe_ci_low": None if ratio is None else ratio - 0.3, "oe_ci_high": None if ratio is None else ratio + 0.3,
            "band_low": 0.8, "band_high": 1.25, "observed": 21, "expected": 20.0, "min_positives_floor": 20, "cadence": "weekly",
            "trigger": "schedule", "model_bundle_id": "b" * 64,
        })  # fmt: skip
    return rows


def _spec(theme="light", extra=()):
    points = p.monitoring_points(_rows(extra), bundle="b" * 64)
    return charts.oe_chart(points, chart_colors(THEMES[theme])).to_dict(), points


def _layer_weeks(spec, layer):
    return [d["sim_week"] for d in spec["datasets"][layer["data"]["name"]]]


def _colours(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _colours(value)
    elif isinstance(node, list):
        for value in node:
            yield from _colours(value)
    elif isinstance(node, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", node):
        yield node


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_layers_marks_shapes_and_colours(theme):
    spec, _ = _spec(theme)
    layers = spec["layer"]
    assert len(layers) == len(charts.LAYERS) == 8
    assert [layer["mark"]["type"] for layer in layers] == ["rule", "line", "line", "point", "point", "point", "point", "text"]
    points = layers[3:6]
    assert [layer["mark"]["shape"] for layer in points] == ["circle", "diamond", "circle"]
    assert [layer["mark"]["filled"] for layer in points] == [True, True, False]
    oneofs = [layer["transform"][0]["filter"]["oneOf"] for layer in (layers[0], *points, layers[7])]
    assert oneofs == [list(charts.KNOWN_VERDICTS), ["ok"], ["alert"], list(charts.MUTED_VERDICTS), ["alert"]]
    colors = chart_colors(THEMES[theme])
    scale = layers[0]["encoding"]["color"]["scale"]
    assert scale == {"domain": list(charts.KNOWN_VERDICTS), "range": [colors["ok"], colors["alert"], colors["muted"], colors["muted"]]}
    assert set(_colours(spec)) <= set(colors.values())
    assert spec["config"]["background"] == colors["background"]
    rug = layers[6]
    assert rug["mark"]["filled"] is False and rug["encoding"]["y"] == {"value": charts.RUG_Y}
    assert "min_positives_floor" in [t["field"] for t in layers[3]["encoding"]["tooltip"]]


def test_which_weeks_each_layer_draws():
    spec, points = _spec()
    layers = spec["layer"]
    weeks = list(points["sim_week"])
    plotted = [w for w, ok in zip(weeks, points["plotted"], strict=True) if ok]
    assert _layer_weeks(spec, layers[0]) == plotted == [weeks[i] for i in (0, 1, 3, 4, 5)]
    assert _layer_weeks(spec, layers[1]) == _layer_weeks(spec, layers[2]) == weeks
    assert _layer_weeks(spec, layers[6]) == [weeks[2]]
    for layer in layers[3:6]:
        assert _layer_weeks(spec, layer) == plotted  # each point layer selects its verdicts with oneOf


def test_dates_are_utc_midnight_and_the_x_axis_uses_a_utc_time_unit():
    spec, _ = _spec()
    for rows in spec["datasets"].values():
        for row in rows:
            assert row["sim_date"].endswith("T00:00:00+00:00") and row["sim_date"][:10] == row["sim_week"]
    x = spec["layer"][3]["encoding"]["x"]
    assert x["timeUnit"] == "utcyearmonthdate" and x["axis"]["format"] == "%Y-%m-%d"


def test_an_unknown_verdict_is_excluded_by_every_point_ci_and_label_layer():
    spec, _ = _spec(extra=[("other", 1.1)])
    layers = spec["layer"]
    assert "other" in [d["verdict"] for d in spec["datasets"][layers[3]["data"]["name"]]]
    for layer in (layers[0], *layers[3:6], layers[7]):
        assert "other" not in layer["transform"][0]["filter"]["oneOf"]
    assert "other" in [d["verdict"] for d in spec["datasets"][layers[1]["data"]["name"]]]
