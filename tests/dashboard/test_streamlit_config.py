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
}  # fmt: skip


def test_config_is_exactly_the_hardened_set():
    assert tomllib.loads((REPO / ".streamlit" / "config.toml").read_text()) == EXPECTED


def test_every_key_is_a_streamlit_option():
    options = st_config._config_options_template
    missing = [f"{section}.{key}" for section, keys in EXPECTED.items() for key in keys if f"{section}.{key}" not in options]
    assert missing == []


def test_allowed_hosts_are_the_service_hosts_plus_dev_hosts():
    """S9's test (v3 slice 4): the list minus the dev hosts is exactly the Cloud Run service's hosts."""
    hosts = tomllib.loads((REPO / ".streamlit" / "config.toml").read_text())["server"]["allowedHosts"]
    assert set(hosts) - {"localhost", "127.0.0.1"} == set(SERVICE_HOSTS)
