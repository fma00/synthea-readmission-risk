"""The dashboard never unpickles (PRD FR18, design E2): every test here runs with the model-loading trap armed."""

from __future__ import annotations

import pytest

from tests.scoring.helpers import forbid_model_loading


@pytest.fixture(autouse=True)
def _no_unpickling(monkeypatch):
    forbid_model_loading(monkeypatch)
