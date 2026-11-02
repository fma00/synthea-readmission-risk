"""The hardened Streamlit configuration (PRD v3 FR17; Verification V-S): exact equality, so every change is deliberate."""

from __future__ import annotations

import tomllib
from pathlib import Path

from streamlit import config as st_config

REPO = Path(__file__).resolve().parents[2]
EXPECTED = {
    "global": {"developmentMode": False},
    "client": {"showErrorDetails": "none", "toolbarMode": "viewer", "showSidebarNavigation": False},
    "browser": {"gatherUsageStats": False},
    "server": {
        "headless": True, "fileWatcherType": "none", "runOnSave": False, "enableXsrfProtection": True, "enableCORS": True,
        "enableStaticServing": False, "maxUploadSize": 1, "maxMessageSize": 16,
        # owner decision S9: the dev hosts are a recorded deviation from FR17's "service host only"
        "allowedHosts": ["dashboard-271458030145.us-central1.run.app", "localhost", "127.0.0.1"],
    },
}  # fmt: skip


def test_config_is_exactly_the_hardened_set():
    assert tomllib.loads((REPO / ".streamlit" / "config.toml").read_text()) == EXPECTED


def test_every_key_is_a_streamlit_option():
    options = st_config._config_options_template
    missing = [f"{section}.{key}" for section, keys in EXPECTED.items() for key in keys if f"{section}.{key}" not in options]
    assert missing == []
