"""The failure classes and panels (v3 slice 3, Verification V-E; owner decision S4: the PRD's four panels)."""

from __future__ import annotations

import concurrent.futures

import pytest
import requests
from google.api_core import exceptions as api
from google.auth import exceptions as auth

from readmission_risk.dashboard import errors
from readmission_risk.warehouse import gold


def _reason(cls, reason):
    return cls("x", errors=[{"reason": reason, "message": "SECRET"}])


def _chained(outer, inner):
    outer.__cause__ = inner  # what `raise outer from inner` records
    return outer


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_reason(api.BadRequest, "bytesBilledLimitExceeded"), "bytes_cap"),
        (_reason(api.Forbidden, "quotaExceeded"), "quota"),
        (_reason(api.Forbidden, "rateLimitExceeded"), "quota"),
        (api.TooManyRequests("x"), "quota"),
        (api.RetryError("x", cause=_reason(api.Forbidden, "rateLimitExceeded")), "quota"),
        (_reason(api.Forbidden, "accessDenied"), "unreachable"),
        (api.ServiceUnavailable("x"), "unreachable"),
        (api.RetryError("x", cause=api.ServiceUnavailable("y")), "unreachable"),
        (requests.ConnectionError("x"), "unreachable"),
        (auth.RefreshError("x"), "unreachable"),
        (concurrent.futures.TimeoutError(), "unreachable"),
        (KeyError("x"), "unreachable"),
    ],
    ids=lambda v: v if isinstance(v, str) else type(v).__name__,
)
def test_classify(exc, expected):
    assert errors.classify(exc) == expected


def test_a_quota_error_is_found_through_the_cause_chain():
    assert errors.classify(_chained(RuntimeError("wrapper"), _reason(api.Forbidden, "quotaExceeded"))) == "quota"


def test_a_cyclic_chain_terminates():
    exc = RuntimeError("loop")
    exc.__context__ = exc
    assert errors.exception_chain(exc) == [exc]
    assert errors.classify(exc) == "unreachable"


def test_quota_reasons_equal_the_gold_publish_rule():
    assert gold._QUOTA_REASONS == errors.QUOTA_REASONS


@pytest.mark.parametrize("kind", ["unreachable", "bytes_cap", "quota", "empty"])
def test_panel_text(kind):
    text = errors.panel_text(kind, what="The thing", still="Other views may still work.")
    assert text.startswith("The thing") and text.endswith("Other views may still work.") and "{" not in text
    if kind == "bytes_cap":
        assert "100 MiB" in text
