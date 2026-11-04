"""The hardened Streamlit configuration (PRD v3 FR17; Verification V-S): exact equality, so every change is deliberate."""

from __future__ import annotations

import tomllib
from pathlib import Path

from streamlit import config as st_config

from tests.service_hosts import SERVICE_HOSTS

REPO = Path(__file__).resolve().parents[2]
EXPECTED = {
    "global": {"developmentMode": False},
    "client": {"showErrorDetails": "none", "toolbarMode": "viewer", "showSidebarNavigation": False},
    "browser": {"gatherUsageStats": False},
    "logger": {"hideWelcomeMessage": True},  # v3 slice 4 (F11): no startup banner, so no outbound external-IP lookup
    "server": {
        "headless": True, "fileWatcherType": "none", "runOnSave": False, "enableXsrfProtection": True, "enableCORS": True,
        "enableStaticServing": False, "maxUploadSize": 1, "maxMessageSize": 16,
        # owner decision S9: the dev hosts are a recorded deviation from FR17's "service host only"
        "allowedHosts": ["dashboard-271458030145.us-central1.run.app", "dashboard-6wf7ihuvwq-uc.a.run.app", "localhost", "127.0.0.1"],
    },
    # v3 slice 4, L7: WCAG AA contrast for captions and the footer's links (test_contrast_* below)
    "theme": {
        "light": {"textColor": "#0e1117", "linkColor": "#00137a", "linkUnderline": True},
        "dark": {"linkColor": "#a6d8ff", "linkUnderline": True},
    },
}  # fmt: skip
# Streamlit 1.66.1's defaults for what the theme leaves unset (static/js palette: bgColor white / gray100, dark bodyText gray10).
DEFAULT_BACKGROUND = {"light": "#ffffff", "dark": "#0e1117"}
DEFAULT_TEXT = {"light": "#31333f", "dark": "#fafafa"}
CAPTION_ALPHA = 0.6  # st.caption = fadedText60; reproduces the owner's L7 measurements 3.69 / 3.03 / 3.09 within 0.02


def test_config_is_exactly_the_hardened_set():
    assert tomllib.loads((REPO / ".streamlit" / "config.toml").read_text()) == EXPECTED


def expected_options(table: dict = EXPECTED, prefix: str = "") -> dict[str, object]:
    """{dotted option name: value}; a nested table (theme.light) is a section, not an option. test_app.py's fixture uses it."""
    options = {}
    for key, value in table.items():
        name = f"{prefix}{key}"
        options |= expected_options(value, f"{name}.") if isinstance(value, dict) else {name: value}
    return options


def test_every_key_is_a_streamlit_option():
    options = st_config._config_options_template
    missing = [name for name in expected_options() if name not in options]
    assert missing == []


def _luminance(hex_color: str) -> float:
    def channel(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(float(int(h[i : i + 2], 16)) for i in (0, 2, 4))


def _faded(fg: str, bg: str, alpha: float) -> str:
    return "#" + "".join(f"{round(alpha * f + (1 - alpha) * b):02x}" for f, b in zip(_rgb(fg), _rgb(bg), strict=True))


def _ratio(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_contrast_model_reproduces_the_l7_measurements():
    """The model behind the colours: Streamlit's defaults at 60 % give the owner's DevTools figures."""
    for fg, bg, measured in (("#31333F", "#ffffff", 3.69), ("#0054a3", "#ffffff", 3.03), ("#3d9df3", "#0e1117", 3.09)):
        assert abs(_ratio(_faded(fg, bg, CAPTION_ALPHA), bg) - measured) < 0.02, (fg, bg)


def test_contrast_captions_and_footer_links_meet_wcag_aa():
    theme = tomllib.loads((REPO / ".streamlit" / "config.toml").read_text())["theme"]
    for mode in ("light", "dark"):
        bg = theme[mode].get("backgroundColor", DEFAULT_BACKGROUND[mode])
        text = theme[mode].get("textColor", DEFAULT_TEXT[mode])
        for role, colour in (("text", text), ("link", theme[mode]["linkColor"])):
            assert _ratio(colour, bg) >= 4.5, (mode, role, "full strength")
            assert _ratio(_faded(colour, bg, CAPTION_ALPHA), bg) >= 4.5, (mode, role, "inside a caption")
        assert theme[mode]["linkUnderline"] is True, mode  # WCAG 1.4.1: links not told apart by colour alone


def test_allowed_hosts_are_the_service_hosts_plus_dev_hosts():
    """S9's test (v3 slice 4): the list minus the dev hosts is exactly the Cloud Run service's hosts."""
    hosts = tomllib.loads((REPO / ".streamlit" / "config.toml").read_text())["server"]["allowedHosts"]
    assert set(hosts) - {"localhost", "127.0.0.1"} == set(SERVICE_HOSTS)
