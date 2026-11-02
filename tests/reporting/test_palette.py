"""The dependency-free colour tokens (v3 slice 3, Verification V-P): one source for the matplotlib figures and the dashboard's
Altair charts. The README figures regenerate byte-identically after the move (recorded in the design note)."""

from __future__ import annotations

import subprocess
import sys

from readmission_risk.reporting import style
from readmission_risk.reporting.palette import THEMES, Theme, chart_colors


def test_the_ten_hex_tokens_are_unchanged():
    light, dark = THEMES["light"], THEMES["dark"]
    assert (light.ink, light.grey, light.preview_background) == ("#1f2328", "#6e7781", "#ffffff")
    assert light.series == {"logistic_regression": "#0072B2", "xgboost_calibrated": "#D55E00"}
    assert (dark.ink, dark.grey, dark.preview_background) == ("#e6edf3", "#8b949e", "#0d1117")
    assert dark.series == {"logistic_regression": "#56B4E9", "xgboost_calibrated": "#E69F00"}
    assert light.markers == dark.markers == {"logistic_regression": "o", "xgboost_calibrated": "s"}


def test_chart_colors():
    assert chart_colors(THEMES["light"]) == {
        "ink": "#1f2328", "muted": "#6e7781", "ok": "#0072B2", "alert": "#D55E00", "background": "#ffffff",
    }  # fmt: skip
    assert chart_colors(THEMES["dark"]) == {
        "ink": "#e6edf3", "muted": "#8b949e", "ok": "#56B4E9", "alert": "#E69F00", "background": "#0d1117",
    }  # fmt: skip


def test_style_uses_the_palette_theme():
    assert style.Theme is Theme


def test_palette_imports_no_matplotlib():
    code = "import sys, readmission_risk.reporting.palette\nassert 'matplotlib' not in sys.modules\n"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
