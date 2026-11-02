"""The dashboard's failure panels (PRD v3 FR16, owner decision S4): a query failure is one of three classes, an empty result
is the fourth panel, and no panel ever carries the exception's text. google.api_core is imported inside classify only. See
notes/eg-new-feature/v3-slice3-dashboard-app-2026-11-01.md (Interfaces section 3).
"""

from __future__ import annotations

from typing import Literal

from readmission_risk.warehouse.bigquery import MAX_BYTES_BILLED

FailureClass = Literal["unreachable", "bytes_cap", "quota"]
PanelKind = Literal["unreachable", "bytes_cap", "quota", "empty"]
BYTES_CAP_REASONS = frozenset({"bytesBilledLimitExceeded"})  # BigQuery's 400 when maximum_bytes_billed would be exceeded
QUOTA_REASONS = frozenset({"quotaExceeded", "rateLimitExceeded"})  # equal to warehouse/gold.py's _QUOTA_REASONS (a test)
_MAX_CHAIN = 20

PANEL = {
    "unreachable": "{what} could not be read from BigQuery right now. {still}",
    "bytes_cap": "{what} was not loaded: the query would scan more than the {cap} MiB cost cap, so BigQuery refused it. {still}",
    "quota": "{what} was not loaded: a BigQuery quota or rate limit was reached. Try again later. {still}",
    "empty": "{what}: nothing to show yet. {still}",
}
RENDER_FAILED = "{view} could not be displayed. The other views still work."  # design E7: names no cause


def exception_chain(exc: BaseException) -> list[BaseException]:
    """exc, then breadth-first each element's __cause__, __context__ and (api_core RetryError) .cause; each once; at most 20."""
    chain, queue, seen = [], [exc], set()
    while queue and len(chain) < _MAX_CHAIN:
        current = queue.pop(0)
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        queue += [current.__cause__, current.__context__, getattr(current, "cause", None)]
    return chain


def _reasons(exc: BaseException) -> set[str]:
    errors = getattr(exc, "errors", None)
    if not isinstance(errors, list):
        return set()
    return {e["reason"] for e in errors if isinstance(e, dict) and isinstance(e.get("reason"), str)}


def classify(exc: BaseException) -> FailureClass:
    """bytes_cap, quota (also an api_core TooManyRequests), else unreachable: IAM refusals and every other exception (S4)."""
    from google.api_core import exceptions as api

    chain = exception_chain(exc)
    reasons = set().union(*(_reasons(e) for e in chain))
    if reasons & BYTES_CAP_REASONS:
        return "bytes_cap"
    if reasons & QUOTA_REASONS or any(isinstance(e, api.TooManyRequests) for e in chain):
        return "quota"
    return "unreachable"


def panel_text(kind: PanelKind, *, what: str, still: str) -> str:
    return PANEL[kind].format(what=what, still=still, cap=MAX_BYTES_BILLED // 2**20)
