"""The dashboard's import boundary and source bans (PRD FR18; v3 slice 2's V-B, extended in slice 3). cloudpickle is not in
the sys.modules list: `import pandas` loads it through pyarrow (measured 2026-10-31), so "never unpickles" rests on the source
ban below, the trap in conftest.py and the in-memory resource cache (owner decision S5). The bans match the WHOLE file text,
comments and docstrings included (design section 9, Rules)."""

from __future__ import annotations

import inspect
import re
import subprocess
import sys
from pathlib import Path

import streamlit

_REPO = Path(__file__).resolve().parents[2]
_ENTRYPOINT = _REPO / "scripts" / "dashboard_app.py"
_SOURCES = [*sorted((_REPO / "src/readmission_risk/dashboard").rglob("*.py")), _ENTRYPOINT]
_BANNED_MODULES = (
    "mlflow", "pyspark", "sklearn", "xgboost", "matplotlib", "readmission_risk.bundles", "readmission_risk.scoring.pipeline",
    "readmission_risk.models", "readmission_risk.reporting.style", "readmission_risk.monitoring.config",
    "readmission_risk.monitoring.ledger",
)  # fmt: skip
_WIDGETS = (
    "text_input", "text_area", "chat_input", "number_input", "date_input", "datetime_input", "time_input", "file_uploader",
    "camera_input", "audio_input", "color_picker", "slider", "select_slider", "checkbox", "toggle", "radio", "multiselect",
    "pills", "segmented_control", "button", "download_button", "link_button", "form", "form_submit_button", "feedback",
    "data_editor", "menu_button", "pagination", "popover",
)  # fmt: skip
_ALLOWED_INTERACTIVE = {"selectbox", "tabs", "expander"}  # the one widget, and two layout elements kept inert (no on_change)
_ON_SELECT_DISPLAY = {"altair_chart", "dataframe", "plotly_chart", "pydeck_chart", "vega_lite_chart"}  # inert without on_select=
_WALL_CLOCK = re.compile(
    r"\b(datetime\.(now|utcnow|today|fromtimestamp|utcfromtimestamp)|date\.(today|fromtimestamp)|Timestamp\.(now|utcnow|today))\b"
)


def _text(path: Path) -> str:
    return path.read_text()


def test_dashboard_modules_import_nothing_heavy():
    code = (
        "import importlib, pkgutil, sys\n"
        "import streamlit, altair\n"
        "import readmission_risk.dashboard as dashboard\n"
        "for m in pkgutil.iter_modules(dashboard.__path__):\n"
        "    importlib.import_module(f'readmission_risk.dashboard.{m.name}')\n"
        "import readmission_risk.warehouse.dashboard_views\n"
        f"banned = {_BANNED_MODULES!r}\n"
        "loaded = sorted(m for m in sys.modules if any(m == b or m.startswith(b + '.') for b in banned))\n"
        "assert not loaded, loaded\n"
        "assert 'readmission_risk.dashboard.app' in sys.modules\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False, cwd=_REPO)
    assert done.returncode == 0, done.stderr


def test_dashboard_source_bans():
    assert len(_SOURCES) >= 8  # the package's modules and the entrypoint
    patterns = {
        "pickle import": r"^\s*(import|from)\s+(cloud)?pickle\b",
        "frame download (would build a BigQuery Storage client unless create_bqstorage_client=False)": r"\.to_(dataframe|arrow)\(",
        "file uploader": r"file_uploader",
        "input widget other than the selectbox": r"\.(" + "|".join(_WIDGETS) + r")\(",
        "widget-making keyword": r"\bon_(select|change|click)\s*=",
        "URL-bound widget state": r"\bbind\s*=",
        "query parameters": r"\bquery_params\b",
        "pickling data cache": r"\bcache_data\b",
        "raw HTML": r"\bst\.html\(|unsafe_allow_html|\bcomponents\b|\.iframe\(",
        "markdown of arbitrary values": r"\bst\.write(_stream)?\(",
        "exception element": r"\bst\.exception\(",
        "traceback import": r"^\s*(import|from)\s+traceback\b",
        "time module (a wall clock)": r"^\s*(import\s+time\b|from\s+time\s+import)",
        "string wall clock": r"to_datetime\(\s*[\"'](now|today)|\bnp\.datetime64\(\s*[\"'](now|today)|Timestamp\(\s*[\"'](now|today)",
        "aliased datetime/pandas import": r"^\s*import\s+datetime\s+as\b|^\s*from\s+datetime\s+import\s+[^\n]*\bas\b|^\s*import\s+pandas\s+as\s+(?!pd\b)",
    }  # fmt: skip
    for label, pattern in patterns.items():
        compiled = re.compile(pattern, re.MULTILINE)
        assert [str(p.relative_to(_REPO)) for p in _SOURCES if compiled.search(_text(p))] == [], label


def test_the_only_wall_clock_read_is_heartbeat_wall_now():
    hits = [(p.name, line) for p in _SOURCES for line in _text(p).splitlines() if _WALL_CLOCK.search(line)]
    assert hits == [("heartbeat.py", "    return datetime.now(UTC)")]
    source = _text(_REPO / "src/readmission_risk/dashboard/heartbeat.py")
    assert "def wall_now() -> datetime:\n    return datetime.now(UTC)\n" in source


def _callables_with(parameter: str) -> set[str]:
    found = set()
    for name in dir(streamlit):
        if name.startswith("_"):
            continue
        member = getattr(streamlit, name)
        if not callable(member):
            continue
        try:
            parameters = inspect.signature(member).parameters
        except (TypeError, ValueError):
            continue
        if parameter in parameters:
            found.add(name)
    return found


def test_every_streamlit_widget_is_classified():
    interactive = set().union(*(_callables_with(p) for p in ("on_change", "on_click", "on_submit")))
    assert sorted(interactive - _ALLOWED_INTERACTIVE - set(_WIDGETS)) == [], "a new Streamlit widget: classify it"
    assert _callables_with("on_select") == _ON_SELECT_DISPLAY, "a new on_select element: classify it"


def test_the_entrypoint_is_two_code_lines():
    code = [line for line in _text(_ENTRYPOINT).split('"""')[-1].splitlines() if line.strip()]
    assert code == ["from readmission_risk.dashboard.app import main", "main()"]
