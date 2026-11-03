"""The dashboard's cost guard (v3 slice 4, PRD FR24/V18; Interfaces §8 in notes/eg-new-feature/v3-slice4-cloud-run-deploy-2026-11-03.md).

Each Cloud Billing notification of the dashboard budget arrives here through Pub/Sub and Eventarc. Once the current budget month's
cost reaches the budget, the dashboard's Cloud Run service is disabled: manual scaling with 0 instances (Cloud Run cannot set max
instances to 0; manual 0 creates no revision; F1). Only the dashboard is touched, and only that budget's messages count. Every
call logs exactly one `dashboard-kill: <outcome> ...` line. Standard library only (T3): the buildpack provides
functions-framework, which main.py alone imports. Re-enable: docs/setup.md > Dashboard on Cloud Run > Kill switch.
"""

from __future__ import annotations

import base64
import binascii
import functools
import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

LOG = logging.getLogger("dashboard-kill")
METADATA_TOKEN_URL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"  # noqa: S105 -- a URL, not a secret
RUN_API = "https://run.googleapis.com/v2/"
DISABLED = {"scalingMode": "MANUAL", "manualInstanceCount": 0}
TIMEOUT_S = 10


class Malformed(ValueError):
    """The message or the function's configuration cannot be read."""


class KillError(RuntimeError):
    """An HTTP status outside 2xx, or a network failure, talking to the metadata server or the Cloud Run API."""

    def __init__(self, message: str, *, method: str, status: int | None, body: str):
        super().__init__(message)
        self.method, self.status, self.body = method, status, body
        self.msg: BudgetMessage | None = None  # set by _process once the message is parsed, for the outcome line


@dataclass(frozen=True)
class BudgetMessage:
    budget: str  # billingAccounts/{billingAccountId}/budgets/{budgetId}, from the message attributes
    cost: float
    budget_amount: float
    currency: str
    period_start: datetime  # costIntervalStart

    def fields(self) -> str:
        return (
            f"budget={self.budget} cost={self.cost} budget_amount={self.budget_amount} currency={self.currency} "
            f"period_start={self.period_start.isoformat()}"
        )


@functools.cache
def pacific() -> ZoneInfo:
    """Built lazily (F17, P13): a runtime without zone data then yields an `error` outcome line, not a failed deploy."""
    return ZoneInfo("America/Los_Angeles")


def _number(payload: Mapping, key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise Malformed(f"{key} is not a number: {value!r}")
    return float(value)


def parse(data: Mapping) -> BudgetMessage:
    """The budget notification inside an Eventarc Pub/Sub CloudEvent's data."""
    message = data.get("message") if isinstance(data, Mapping) else None
    if not isinstance(message, Mapping):
        raise Malformed("no message")
    attributes = message.get("attributes")
    if not isinstance(attributes, Mapping) or not attributes.get("billingAccountId") or not attributes.get("budgetId"):
        raise Malformed("no billingAccountId/budgetId attributes")
    try:
        payload = json.loads(base64.b64decode(message.get("data") or "", validate=True))
    except (binascii.Error, ValueError, TypeError) as exc:
        raise Malformed(f"data is not base64 JSON ({type(exc).__name__})") from exc
    if not isinstance(payload, Mapping):
        raise Malformed("data is not a JSON object")
    raw_start = payload.get("costIntervalStart")
    try:
        start = datetime.fromisoformat(raw_start)
    except (TypeError, ValueError) as exc:
        raise Malformed(f"costIntervalStart is not ISO 8601: {raw_start!r}") from exc
    if start.tzinfo is None:
        raise Malformed(f"costIntervalStart has no time zone: {raw_start!r}")
    currency = payload.get("currencyCode")
    if not isinstance(currency, str) or not currency:
        raise Malformed("no currencyCode")
    return BudgetMessage(
        budget=f"billingAccounts/{attributes['billingAccountId']}/budgets/{attributes['budgetId']}",
        cost=_number(payload, "costAmount"),
        budget_amount=_number(payload, "budgetAmount"),
        currency=currency,
        period_start=start,
    )


def decide(msg: BudgetMessage, *, expected_budget: str, now: datetime) -> str:
    if msg.budget != expected_budget:
        return "ignore-other-budget"
    start, today = msg.period_start.astimezone(pacific()), now.astimezone(pacific())
    if (start.year, start.month) != (today.year, today.month):
        return "stale-period"  # a late message for an earlier budget month never kills (F17)
    if msg.budget_amount > 0 and msg.cost >= msg.budget_amount:
        return "kill"
    return "below-budget"


def is_disabled(scaling: Mapping | None) -> bool:
    """MANUAL with 0 instances; proto3 JSON omits a zero count and other keys are ignored."""
    if not isinstance(scaling, Mapping):
        return False
    return scaling.get("scalingMode") == "MANUAL" and int(scaling.get("manualInstanceCount", 0)) == 0


def _metadata_token() -> str:
    request = urllib.request.Request(METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 -- fixed http URL of the metadata server
            return json.loads(response.read())["access_token"]
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        raise KillError(f"metadata token: {exc}", method="GET", status=None, body="-") from exc


def default_http(method: str, url: str, body: dict | None) -> tuple[int, dict]:
    """Calls the Cloud Run Admin API with the function's own identity. HTTP error statuses are returned, not raised."""
    data = None if body is None else json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {_metadata_token()}", "Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310 -- RUN_API + a Terraform-set service path
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 -- fixed https URL of the Run API
            status, text = response.status, response.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        status, text = exc.code, exc.read().decode(errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        raise KillError(f"{method} {url}: {exc}", method=method, status=None, body="-") from exc
    try:
        payload = json.loads(text) if text else {}
    except ValueError:
        payload = {"raw": text[:500]}
    return status, payload if isinstance(payload, dict) else {"raw": text[:500]}


def _call(http: Callable, method: str, url: str, body: dict | None) -> dict:
    status, payload = http(method, url, body)
    if not 200 <= status < 300:
        raise KillError(f"{method} {url}: HTTP {status}", method=method, status=status, body=json.dumps(payload)[:500])
    return payload


def _process(data: Mapping, environ: Mapping[str, str], http: Callable, now: datetime) -> tuple[str, BudgetMessage, str]:
    """(outcome, message, extra log fields). Raises Malformed, KillError, or anything unexpected."""
    msg = parse(data)
    outcome = decide(msg, expected_budget=environ["EXPECTED_BUDGET"], now=now)
    if outcome != "kill":
        return outcome, msg, ""
    url = RUN_API + environ["TARGET_SERVICE"]
    try:
        service = _call(http, "GET", url, None)
        if is_disabled(service.get("scaling")):
            return "already-disabled", msg, ""
        operation = _call(http, "PATCH", url + "?updateMask=scaling", {"scaling": dict(DISABLED)})
    except KillError as exc:
        exc.msg = msg
        raise
    return "disabled", msg, f" operation={operation.get('name', '-')}"


def handle(data: Mapping, *, environ: Mapping[str, str] = os.environ, http: Callable | None = None,
           now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> str:
    """Returns the outcome word; logs exactly one line. Config is checked first, whatever the message."""
    if not environ.get("TARGET_SERVICE") or not environ.get("EXPECTED_BUDGET"):
        LOG.error("dashboard-kill: malformed reason=config")
        return "malformed"
    try:
        outcome, msg, extra = _process(data, environ, http or default_http, now())
    except Malformed as exc:
        LOG.error("dashboard-kill: malformed reason=%s", exc)  # noqa: TRY400 -- an expected outcome, not a crash: no traceback
        return "malformed"
    except KillError as exc:
        fields = f"{exc.msg.fields()} " if exc.msg else ""
        LOG.error("dashboard-kill: http-error %smethod=%s status=%s body=%s", fields, exc.method, exc.status, exc.body)  # noqa: TRY400 -- one line, no traceback text; re-raised below
        raise
    except Exception as exc:
        LOG.error("dashboard-kill: error exception=%s: %s", type(exc).__name__, str(exc)[:300])  # noqa: TRY400 -- one line, no traceback text; the re-raise lets the platform record it
        raise
    LOG.info("dashboard-kill: %s %s%s", outcome, msg.fields(), extra)
    return outcome
