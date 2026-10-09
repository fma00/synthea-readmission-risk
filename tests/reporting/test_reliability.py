from __future__ import annotations

import pytest
from matplotlib.collections import LineCollection
from PIL import Image

from readmission_risk.reporting.reliability import (
    TITLE,
    bin_points,
    build_reliability_figure,
    wilson_interval,
)
from readmission_risk.reporting.style import THEMES, house_style, save_figure
from tests.reporting.conftest import HIGH_CURVE, LOW_CURVE, make_curve


def test_wilson_zero_positives_known_value():
    low, high = wilson_interval(0, 154)
    assert low == 0.0
    assert high == pytest.approx(0.02434, abs=1e-4)  # z^2 / (n + z^2)


def test_wilson_is_symmetric_about_one_half():
    low, high = wilson_interval(77, 154)
    assert low + high == pytest.approx(1.0, abs=1e-12)


def test_wilson_brackets_the_estimate_for_every_small_k_and_n():
    for n in range(1, 401):
        for k in range(n + 1):
            low, high = wilson_interval(k, n)
            assert 0.0 <= low <= k / n <= high <= 1.0, (k, n, low, high)
            _, other_high = wilson_interval(n - k, n)
            assert abs(low - (1 - other_high)) < 1e-12, (k, n)
            # the yerr half-lengths errorbar receives must be non-negative
            assert k / n - low >= 0 and high - k / n >= 0
    assert wilson_interval(0, 7)[0] == 0.0
    assert wilson_interval(4, 4)[1] == 1.0


@pytest.mark.parametrize("k, n", [(0, 0), (-1, 5), (6, 5)])
def test_wilson_rejects_invalid_arguments(k, n):
    with pytest.raises(ValueError, match="n >= 1"):
        wilson_interval(k, n)


def test_bin_points_reconstructs_the_positive_count():
    (point,) = bin_points([{"mean_predicted": 0.0128, "observed_fraction": 2 / 154, "n": 154}])
    assert point.n_positive == 2 and point.n == 154
    assert point.ci_low <= point.observed_fraction <= point.ci_high


def test_bin_points_rejects_a_non_integral_count():
    with pytest.raises(ValueError, match="not an integer count"):
        bin_points([{"mean_predicted": 0.01, "observed_fraction": 0.013, "n": 154}])  # 0.013 * 154 = 2.002


def test_bin_points_sorts_ascending_by_mean_predicted():
    points = bin_points(make_curve([(0.05, 1, 10), (0.01, 0, 10), (0.03, 1, 10)]))
    assert [p.mean_predicted for p in points] == [0.01, 0.03, 0.05]


@pytest.mark.parametrize(
    "curve",
    [
        [],
        [{"mean_predicted": 0.1, "n": 10}],
        [{"mean_predicted": float("nan"), "observed_fraction": 0.1, "n": 10}],
        [{"mean_predicted": 0.1, "observed_fraction": 0.1, "n": 0}],
        [{"mean_predicted": 1.5, "observed_fraction": 0.1, "n": 10}],
    ],
)
def test_bin_points_rejects_malformed_curves(curve):
    with pytest.raises(ValueError, match="calibration"):
        bin_points(curve)


def _figure(theme_name="light", curves=None):
    theme = THEMES[theme_name]
    curves = curves or {"logistic_regression": LOW_CURVE, "xgboost_calibrated": HIGH_CURVE}
    return theme, build_reliability_figure(curves, theme)


def _drawn_tick_labels(ax):
    xlim, ylim = ax.get_xlim(), ax.get_ylim()
    xs = [t for t, x in zip(ax.get_xticklabels(), ax.get_xticks(), strict=True) if xlim[0] <= x <= xlim[1] and t.get_text()]
    ys = [t for t, y in zip(ax.get_yticklabels(), ax.get_yticks(), strict=True) if ylim[0] <= y <= ylim[1] and t.get_text()]
    return xs, ys


def _assert_layout_inside(fig, ax):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    xs, ys = _drawn_tick_labels(ax)
    assert len(xs) >= 2 and len(ys) >= 2, "no vacuous pass: the tick labels must have been collected"
    artists = [ax.title, ax.xaxis.label, ax.yaxis.label, ax.get_legend(), *xs, *ys]
    box = fig.bbox
    for artist in artists:
        extent = artist.get_window_extent(renderer)
        assert extent.x0 >= box.x0 - 0.5 and extent.y0 >= box.y0 - 0.5, artist
        assert extent.x1 <= box.x1 + 0.5 and extent.y1 <= box.y1 + 0.5, artist


def test_figure_structure_and_house_widths():
    with house_style(THEMES["light"]):
        _, fig = _figure()
        (ax,) = fig.axes
        assert ax.get_title() == TITLE
        assert all(line.get_linewidth() == 1 for line in ax.lines)
        assert all(set(c.get_linewidth()) == {1} for c in ax.collections if isinstance(c, LineCollection))
        assert ax.get_xlim()[0] == 0 and ax.get_ylim()[0] == 0 and ax.get_xlim()[1] == ax.get_ylim()[1]
        assert [t.get_text() for t in ax.get_legend().get_texts()] == ["Logistic regression", "Calibrated XGBoost", "perfectly calibrated"]
        diagonal = next(line for line in ax.lines if line.get_label() == "perfectly calibrated")
        assert list(diagonal.get_xdata()) == list(diagonal.get_ydata())


def test_plotted_data_equal_the_bin_points():
    with house_style(THEMES["light"]):
        _, fig = _figure()
        (ax,) = fig.axes
        by_label = {c.get_label(): c for c in ax.containers}
        for label, curve in (("Logistic regression", LOW_CURVE), ("Calibrated XGBoost", HIGH_CURVE)):
            points = bin_points(curve)
            data_line, _, (bars,) = by_label[label].lines
            assert list(data_line.get_xdata()) == [p.mean_predicted for p in points]
            assert list(data_line.get_ydata()) == [p.observed_fraction for p in points]
            segments = bars.get_segments()
            assert [s[0][1] for s in segments] == pytest.approx([p.ci_low for p in points])
            assert [s[1][1] for s in segments] == pytest.approx([p.ci_high for p in points])


def test_layout_stays_inside_the_figure_at_both_dpis():
    with house_style(THEMES["light"]):
        _, fig = _figure()
        (ax,) = fig.axes
        _assert_layout_inside(fig, ax)  # the layout the SVG uses
        fig.set_dpi(200)
        _assert_layout_inside(fig, ax)  # the layout of the PNG preview


def test_title_fits_the_panel():
    with house_style(THEMES["light"]):
        _, fig = _figure()
        (ax,) = fig.axes
        fig.canvas.draw()
        extent = ax.title.get_window_extent(fig.canvas.get_renderer())
        assert extent.x0 >= 0 and extent.x1 <= fig.bbox.x1


def test_themes_differ_in_ink_but_not_in_plotted_data(tmp_path):
    outputs = {}
    for name in ("light", "dark"):
        theme = THEMES[name]
        with house_style(theme):
            fig = build_reliability_figure({"logistic_regression": LOW_CURVE, "xgboost_calibrated": HIGH_CURVE}, theme)
            fig.canvas.draw()
            save_figure(fig, tmp_path / f"{name}.svg")
        outputs[name] = (tmp_path / f"{name}.svg").read_text(encoding="utf-8")
    assert "#1f2328" in outputs["light"] and "#1f2328" not in outputs["dark"]
    assert "#e6edf3" in outputs["dark"] and "#e6edf3" not in outputs["light"]


def test_svg_has_real_text_the_github_font_stack_a_transparent_background_and_no_date(tmp_path):
    with house_style(THEMES["light"]):
        _, fig = _figure()
        fig.canvas.draw()
        save_figure(fig, tmp_path / "a.svg", metadata={"Title": TITLE, "Description": "d", "Date": None})
        _, again = _figure()
        again.canvas.draw()
        save_figure(again, tmp_path / "b.svg", metadata={"Title": TITLE, "Description": "d", "Date": None})
    svg = (tmp_path / "a.svg").read_text(encoding="utf-8")
    assert "<text" in svg
    assert "font-weight: 200" in svg
    families = {line.split("font-family:")[1].split(";")[0].strip() for line in svg.splitlines() if "font-family:" in line}
    assert families
    for family in families:
        assert "'Segoe UI'" in family and family.endswith(", sans-serif")
    assert svg.count("fill: none; opacity: 0") >= 2  # figure and axes backgrounds
    assert "dc:date" not in svg.lower()
    assert (tmp_path / "a.svg").read_bytes() == (tmp_path / "b.svg").read_bytes()


def test_png_preview_has_the_expected_size_and_an_opaque_background(tmp_path):
    theme = THEMES["dark"]
    with house_style(theme):
        _, fig = _figure("dark")
        save_figure(fig, tmp_path / "p.png", dpi=200, facecolor=theme.preview_background)
    image = Image.open(tmp_path / "p.png")
    assert image.size == (708, 551)
    assert image.getpixel((0, 0)) == (13, 17, 23, 255)


def test_a_single_curve_still_plots():
    theme = THEMES["light"]
    with house_style(theme):
        fig = build_reliability_figure({"logistic_regression": LOW_CURVE}, theme)
        assert len(fig.axes[0].containers) == 1


def test_no_curves_is_an_error():
    with house_style(THEMES["light"]), pytest.raises(ValueError, match="no curves"):
        build_reliability_figure({}, THEMES["light"])


def test_layout_check_detects_a_clipped_title():
    """Negative control: the bounds helper must fail on a figure whose title cannot fit the panel."""
    with house_style(THEMES["light"]):
        _, fig = _figure()
        (ax,) = fig.axes
        ax.set_title("A deliberately absurdly long title that cannot possibly fit inside a nine centimetre panel " * 2)
        with pytest.raises(AssertionError):
            _assert_layout_inside(fig, ax)
