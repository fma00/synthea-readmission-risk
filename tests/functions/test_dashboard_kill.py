"""The dashboard kill switch (v3 slice 4, Verification V-K in notes/eg-new-feature/v3-slice4-cloud-run-deploy-2026-11-03.md).

kill.py is loaded from an isolated copy (proving it is self-contained and stdlib-only), and every call goes through a fake `http`:
no network, no Google Cloud. main.py imports functions_framework, which only the function's buildpack provides, so it is checked
statically.
"""

from __future__ import annotations

import ast
import base64
import io
import json
import logging
import subprocess
import sys
import urllib.error
import zoneinfo
from datetime import UTC, datetime

import pytest

from tests.scripts.stdlib_loader import ROOT, imported_modules, load_isolated

FUNCTION_DIR = ROOT / "infra" / "functions" / "dashboard_kill"
BUDGET = "billingAccounts/AAAAAA-BBBBBB-CCCCCC/budgets/1234-abcd"
TARGET = "projects/synthea-readmission-risk/locations/us-central1/services/dashboard"
ENV = {"TARGET_SERVICE": TARGET, "EXPECTED_BUDGET": BUDGET}
URL = "https://run.googleapis.com/v2/" + TARGET
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
NOVEMBER = "2026-11-01T07:00:00Z"  # Billing's form: midnight US Pacific (PDT) in UTC


@pytest.fixture
def kill(tmp_path):
    return load_isolated("kill", tmp_path, directory=FUNCTION_DIR)


def message(*, cost=1.0, amount=3.0, start=NOVEMBER, budget_id="1234-abcd", account="AAAAAA-BBBBBB-CCCCCC", payload=None,
            data=None) -> dict:
    body = payload if payload is not None else {
        "budgetDisplayName": "dashboard kill switch", "costAmount": cost, "costIntervalStart": start, "budgetAmount": amount,
        "budgetAmountType": "SPECIFIED_AMOUNT", "currencyCode": "CHF",
    }
    encoded = data if data is not None else base64.b64encode(json.dumps(body).encode()).decode()
    return {"message": {"attributes": {"billingAccountId": account, "budgetId": budget_id, "schemaVersion": "1.0"},
                        "data": encoded, "messageId": "1"}, "subscription": "projects/p/subscriptions/s"}  # fmt: skip


class FakeHttp:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, method, url, body):
        self.calls.append((method, url, body))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def run(kill, data, *, http=None, environ=ENV, now=NOW):
    return kill.handle(data, environ=environ, http=http or FakeHttp(), now=lambda: now)


def outcome_records(caplog):
    return [r for r in caplog.records if r.name == "dashboard-kill" and r.getMessage().startswith("dashboard-kill: ")]


@pytest.fixture(autouse=True)
def _info(caplog):
    caplog.set_level(logging.INFO, logger="dashboard-kill")


# ---------------------------------------------------------------- files


def test_kill_is_stdlib_only():
    assert imported_modules("kill", directory=FUNCTION_DIR) <= set(sys.stdlib_module_names) | {"__future__"}


def test_main_is_the_thin_functions_framework_entry_point():
    tree = ast.parse((FUNCTION_DIR / "main.py").read_text())
    imports = {a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names}
    assert imports == {"logging", "functions_framework", "kill"}
    assert not [n for n in tree.body if isinstance(n, ast.ImportFrom)]
    calls = [n.value for n in tree.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
    assert any(ast.unparse(c.func) == "logging.basicConfig" and "level=logging.INFO" in ast.unparse(c) for c in calls)
    (fn,) = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert fn.name == "on_budget_message"
    assert [ast.unparse(d) for d in fn.decorator_list] == ["functions_framework.cloud_event"]


def test_requirements_names_no_package():
    lines = (FUNCTION_DIR / "requirements.txt").read_text().split("\n")
    assert [line for line in lines if line.strip() and not line.lstrip().startswith("#")] == []


def test_the_folder_holds_exactly_the_three_files():
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "infra/functions/dashboard_kill"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert sorted(p.rsplit("/", 1)[-1] for p in listed) == ["kill.py", "main.py", "requirements.txt"]


def test_importing_kill_builds_no_time_zone(kill):
    assert kill.pacific.cache_info().currsize == 0  # lazy (P13): a missing zone database fails a message, not the deploy


# ---------------------------------------------------------------- parse / decide / is_disabled


def test_parse_a_real_shaped_message(kill):
    msg = kill.parse(message(cost=2.5))
    assert msg.budget == BUDGET and msg.cost == 2.5 and msg.budget_amount == 3.0 and msg.currency == "CHF"
    assert msg.period_start == datetime(2026, 11, 1, 7, 0, tzinfo=UTC)


@pytest.mark.parametrize("data", [
    {}, {"message": "x"}, {"message": {"data": "e30="}},
    message(data="not base64!"), message(data=base64.b64encode(b"not json").decode()),
    message(payload=["a"]), message(cost="1"), message(cost=None), message(amount=True),
    message(start=None), message(start="yesterday"), message(start="2026-11-01T07:00:00"),
    message(payload={"costAmount": 1, "budgetAmount": 3, "costIntervalStart": NOVEMBER}),
], ids=["empty", "message not a dict", "no attributes", "bad base64", "not json", "not an object", "string cost",
        "no cost", "boolean amount", "no start", "unparseable start", "naive start", "no currency"])  # fmt: skip
def test_parse_refuses_malformed_messages(kill, data):
    with pytest.raises(kill.Malformed):
        kill.parse(data)


@pytest.mark.parametrize(("cost", "amount", "budget", "start", "now", "expected"), [
    (3.0, 3.0, BUDGET, NOVEMBER, NOW, "kill"),
    (2.99, 3.0, BUDGET, NOVEMBER, NOW, "below-budget"),
    (9.0, 0.0, BUDGET, NOVEMBER, NOW, "below-budget"),
    (9.0, 3.0, "billingAccounts/A/budgets/other", NOVEMBER, NOW, "ignore-other-budget"),
    (9.0, 3.0, BUDGET, "2026-10-01T07:00:00Z", datetime(2026, 11, 1, 9, 0, tzinfo=UTC), "stale-period"),
    (9.0, 3.0, "billingAccounts/A/budgets/other", "2026-10-01T07:00:00Z", NOW, "ignore-other-budget"),
    # the UTC/Pacific gap: 07:30Z on Nov 1 is still Nov 1 00:30 PDT, the November budget month (F17)
    (9.0, 3.0, BUDGET, NOVEMBER, datetime(2026, 11, 1, 7, 30, tzinfo=UTC), "kill"),
    # a winter month starts at 08:00Z (PST)
    (9.0, 3.0, BUDGET, "2026-12-01T08:00:00Z", datetime(2026, 12, 20, tzinfo=UTC), "kill"),
])  # fmt: skip
def test_decide(kill, cost, amount, budget, start, now, expected):
    msg = kill.BudgetMessage(budget, cost, amount, "CHF", datetime.fromisoformat(start))
    assert kill.decide(msg, expected_budget=BUDGET, now=now) == expected


@pytest.mark.parametrize(("scaling", "expected"), [
    ({"scalingMode": "MANUAL"}, True),
    ({"scalingMode": "MANUAL", "manualInstanceCount": 0, "maxInstanceCount": 1}, True),
    ({"scalingMode": "AUTOMATIC", "maxInstanceCount": 1}, False),
    ({}, False), (None, False),
    ({"scalingMode": "MANUAL", "manualInstanceCount": 2}, False),
])  # fmt: skip
def test_is_disabled(kill, scaling, expected):
    assert kill.is_disabled(scaling) is expected


# ---------------------------------------------------------------- handle: one line per call


def test_below_budget_makes_no_call(kill, caplog):
    http = FakeHttp()
    assert run(kill, message(cost=0.01), http=http) == "below-budget" and http.calls == []
    (record,) = outcome_records(caplog)
    assert record.levelno == logging.INFO and record.getMessage().startswith(f"dashboard-kill: below-budget budget={BUDGET} cost=0.01")


def test_other_budget_and_stale_period_are_ignored(kill, caplog):
    assert run(kill, message(cost=9, budget_id="other")) == "ignore-other-budget"
    assert run(kill, message(cost=9, start="2026-10-01T07:00:00Z")) == "stale-period"
    assert [r.getMessage().split()[1] for r in outcome_records(caplog)] == ["ignore-other-budget", "stale-period"]


def test_already_disabled_makes_only_the_get(kill, caplog):
    http = FakeHttp((200, {"scaling": {"scalingMode": "MANUAL"}}))
    assert run(kill, message(cost=3), http=http) == "already-disabled"
    assert http.calls == [("GET", URL, None)]
    assert [r.getMessage().split()[1] for r in outcome_records(caplog)] == ["already-disabled"]


def test_kill_patches_the_whole_scaling_object(kill, caplog):
    http = FakeHttp((200, {"scaling": {"scalingMode": "AUTOMATIC", "maxInstanceCount": 1}}), (200, {"name": "operations/op-1"}))
    assert run(kill, message(cost=3.5), http=http) == "disabled"
    assert http.calls == [
        ("GET", URL, None),
        ("PATCH", URL + "?updateMask=scaling", {"scaling": {"scalingMode": "MANUAL", "manualInstanceCount": 0}}),
    ]
    (record,) = outcome_records(caplog)
    assert record.levelno == logging.INFO and record.getMessage().endswith(" operation=operations/op-1")


@pytest.mark.parametrize("answers", [
    [(403, {"error": {"code": 403}})],
    [(200, {"scaling": {}}), (403, {"error": {"code": 403}})],
], ids=["GET refused", "PATCH refused"])  # fmt: skip
def test_an_http_error_is_one_error_line_and_raised(kill, caplog, answers):
    with pytest.raises(kill.KillError):
        run(kill, message(cost=3), http=FakeHttp(*answers))
    (record,) = outcome_records(caplog)
    text = record.getMessage()
    assert record.levelno == logging.ERROR and text.startswith(f"dashboard-kill: http-error budget={BUDGET}")
    assert "status=403" in text and '"code": 403' in text


def test_a_network_failure_is_an_http_error_with_no_body(kill, caplog):
    failure = None

    def http(method, url, body):
        nonlocal failure
        failure = kill.KillError("GET x: timed out", method="GET", status=None, body="-")
        raise failure

    with pytest.raises(kill.KillError):
        kill.handle(message(cost=3), environ=ENV, http=http, now=lambda: NOW)
    (record,) = outcome_records(caplog)
    assert record.levelno == logging.ERROR and record.getMessage().endswith("method=GET status=None body=-")


def test_an_unexpected_exception_is_one_error_line_and_reraised(kill, caplog):
    with pytest.raises(AttributeError):
        run(kill, message(cost=3), http=FakeHttp((200, ["not", "a", "dict"])))
    (record,) = outcome_records(caplog)
    assert record.levelno == logging.ERROR and record.getMessage().startswith("dashboard-kill: error exception=AttributeError: ")


def test_missing_zone_data_is_an_error_line(kill, caplog, monkeypatch):
    def no_zone():
        raise zoneinfo.ZoneInfoNotFoundError("No time zone found")

    monkeypatch.setattr(kill, "pacific", no_zone)
    with pytest.raises(LookupError):  # ZoneInfoNotFoundError is a KeyError
        run(kill, message(cost=3))
    (record,) = outcome_records(caplog)
    assert record.getMessage().startswith("dashboard-kill: error exception=ZoneInfoNotFoundError: ")


@pytest.mark.parametrize("environ", [{}, {"EXPECTED_BUDGET": BUDGET}, {"TARGET_SERVICE": TARGET, "EXPECTED_BUDGET": ""}])
@pytest.mark.parametrize("data", [message(cost=0.01), {}], ids=["below budget", "malformed"])
def test_missing_config_wins_over_the_message(kill, caplog, environ, data):
    assert run(kill, data, environ=environ) == "malformed"
    (record,) = outcome_records(caplog)
    assert record.levelno == logging.ERROR and record.getMessage() == "dashboard-kill: malformed reason=config"


def test_a_malformed_message_is_one_error_line_and_not_raised(kill, caplog):
    assert run(kill, {"message": {}}) == "malformed"
    (record,) = outcome_records(caplog)
    assert record.levelno == logging.ERROR and record.getMessage().startswith("dashboard-kill: malformed reason=")


# ---------------------------------------------------------------- the default HTTP client's error shapes


def test_default_http_returns_error_statuses_and_wraps_non_json(kill, monkeypatch):
    monkeypatch.setattr(kill, "_metadata_token", lambda: "token")

    def refused(request, timeout):
        assert request.get_header("Authorization") == "Bearer token" and timeout == kill.TIMEOUT_S
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b"<html>no</html>"))

    monkeypatch.setattr(kill.urllib.request, "urlopen", refused)
    assert kill.default_http("GET", URL, None) == (403, {"raw": "<html>no</html>"})

    def unreachable(request, timeout):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(kill.urllib.request, "urlopen", unreachable)
    with pytest.raises(kill.KillError) as excinfo:
        kill.default_http("PATCH", URL, {"scaling": {}})
    assert excinfo.value.status is None and excinfo.value.body == "-"
