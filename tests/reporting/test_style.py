from __future__ import annotations

import io
import logging

import matplotlib
import pytest
from matplotlib import font_manager
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from PIL import Image

from readmission_risk.reporting.style import (
    CM,
    HOUSE_RC,
    THEMES,
    figure_size_cm,
    house_style,
    new_axes,
    save_figure,
)

LIGHT = THEMES["light"]


def test_house_rc_matches_the_owners_values():
    owner = {
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
    }
    assert {k: HOUSE_RC[k] for k in owner} == owner
    for key in ("lines.linewidth", "axes.linewidth", "xtick.major.width", "ytick.major.width", "patch.linewidth"):
        assert HOUSE_RC[key] == 1


def test_figure_size_cm():
    assert figure_size_cm("full", 7) == (17.0, 7.0)
    assert figure_size_cm("half", 7) == (9.0, 7.0)


def test_house_style_leaves_rcparams_and_logger_filters_unchanged():
    logger = logging.getLogger("matplotlib.font_manager")
    before_rc, before_filters = dict(matplotlib.rcParams), list(logger.filters)
    with house_style(LIGHT):
        assert matplotlib.rcParams["svg.fonttype"] == "none"
        assert len(logger.filters) == len(before_filters) + 1
    assert dict(matplotlib.rcParams) == before_rc
    assert logger.filters == before_filters


def test_filter_drops_only_the_light_weight_warning():
    logger = logging.getLogger("matplotlib.font_manager")
    with house_style(LIGHT):
        log_filter = logger.filters[-1]
        light = logging.LogRecord("x", logging.WARNING, "f", 1, "findfont: Failed to find font weight light for DejaVu Sans, now using 400.", (), None)
        other = logging.LogRecord("x", logging.WARNING, "f", 1, "findfont: Font family 'Foo' not found.", (), None)
        assert log_filter.filter(light) is False
        assert log_filter.filter(other) is True


def _render_light_text():
    with matplotlib.rc_context({"font.weight": "light"}):
        fig = Figure()
        fig.add_subplot(111).set_title("t")
        fig.savefig(io.BytesIO(), format="svg")


def test_no_light_weight_warning_inside_house_style(caplog):
    clear = font_manager.fontManager._findfont_cached.cache_clear
    caplog.set_level(logging.WARNING, logger="matplotlib.font_manager")
    clear()
    _render_light_text()
    if not any("Failed to find font weight light" in r.getMessage() for r in caplog.records):
        pytest.skip("this machine has a light font face, so the warning never fires")
    caplog.clear()
    clear()
    with house_style(LIGHT):
        _render_light_text()
    assert [r for r in caplog.records if r.name == "matplotlib.font_manager" and "weight light" in r.getMessage()] == []


def test_new_axes_settings():
    with house_style(LIGHT):
        fig, ax = new_axes(LIGHT, "half", 7.0)
        assert fig.dpi == 1200
        assert tuple(fig.get_size_inches()) == pytest.approx((9 * CM, 7 * CM))
        assert not ax.spines["right"].get_visible()
        assert not ax.spines["top"].get_visible()
        assert ax.spines["left"].get_visible()
        assert ax.spines["bottom"].get_visible()
        for axis in (ax.xaxis, ax.yaxis):
            locator = axis.get_major_locator()
            assert isinstance(locator, MaxNLocator)
            assert locator._nbins == 5
        assert fig.patch.get_alpha() == 0
        assert ax.patch.get_alpha() == 0


def test_save_figure_png_is_opaque_when_asked_and_alpha_is_restored(tmp_path):
    with house_style(LIGHT):
        fig, ax = new_axes(LIGHT, "half", 7.0)
        ax.plot([0, 1], [0, 1])
        save_figure(fig, tmp_path / "a.png", dpi=200, facecolor="#0d1117")
        assert fig.patch.get_alpha() == 0
        save_figure(fig, tmp_path / "b.png", dpi=200)
    opaque, transparent = Image.open(tmp_path / "a.png"), Image.open(tmp_path / "b.png")
    assert opaque.size == (int(9 * CM * 200), int(7 * CM * 200)) == (708, 551)
    assert opaque.getpixel((0, 0)) == (13, 17, 23, 255)
    assert transparent.getpixel((0, 0))[3] == 0
