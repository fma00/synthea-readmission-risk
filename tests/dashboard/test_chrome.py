"""The chrome (v3 slice 3, Verification V-K): the honesty strip from DEMO_NOTICE, the model-card rule (E5), the footer."""

from __future__ import annotations

from pathlib import Path

from readmission_risk.dashboard import chrome
from readmission_risk.scoring import constants
from readmission_risk.warehouse.bigquery import SERVING_GOLD_FINGERPRINT

REPO = Path(__file__).resolve().parents[2]


def test_the_strip_uses_the_scorers_notice():
    assert chrome.DEMO_NOTICE is constants.DEMO_NOTICE
    assert constants.DEMO_NOTICE in chrome.honesty_strip()
    assert chrome.honesty_strip().startswith(chrome.INTENDED_USE)


def test_the_model_card_link_appears_only_once_the_card_exists():
    exists = (REPO / "docs" / "model-card.md").exists()
    assert (chrome.MODEL_CARD_URL is None) == (not exists)
    if chrome.MODEL_CARD_URL is None:
        assert chrome.honesty_strip().endswith("**Model card:** in preparation (published with v3.0).")
        assert "](" not in chrome.honesty_strip()


def test_git_commit():
    for value in ("0123456", "0123456789abcdef0123456789abcdef01234567"):
        assert chrome.git_commit({chrome.GIT_COMMIT_ENV: value}) == value
    for value in ("012345", "0123456789ABCDEF", "0" * 41, "abc`def", ""):
        assert chrome.git_commit({chrome.GIT_COMMIT_ENV: value}) is None
    assert chrome.git_commit({}) is None


def test_footer():
    tail = (
        f" · Gold `{SERVING_GOLD_FINGERPRINT[:12]}…`"
        " · Image commit unknown (not a deployed image)"
        " · Synthetic patients generated with [Synthea](https://github.com/synthetichealth/synthea)"
        " ([Apache License 2.0](https://github.com/synthetichealth/synthea/blob/master/LICENSE))"
    )
    assert chrome.footer(commit=None, bundles=[]) == "Model bundle: none shown" + tail
    shown = chrome.footer(commit="0123456789abcdef", bundles=["f" * 64, "a" * 64, None, "NOT-HEX", "zz", "a" * 64])
    assert shown.startswith("Model bundle `unknown`, `aaaaaaaaaaaa…`, `ffffffffffff…` · ")
    assert "Image commit `0123456789ab`" in shown
