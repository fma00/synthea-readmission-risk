"""scripts/publish_budget_message.py (v3 slice 4, Verification V-KT): the synthetic message is exactly what the kill function
reads, in Billing's US Pacific month form, and main() runs exactly the gcloud argv. No gcloud is run."""

from __future__ import annotations

import base64
import json
import sys
from datetime import UTC, datetime

import pytest

from tests.scripts.stdlib_loader import ROOT, Done, imported_modules, load_isolated

BUDGET = "billingAccounts/AAAAAA-BBBBBB-CCCCCC/budgets/1234-abcd"


@pytest.fixture
def mods(tmp_path):
    publish = load_isolated("publish_budget_message", tmp_path)
    kill = load_isolated("kill", tmp_path, directory=ROOT / "infra" / "functions" / "dashboard_kill")
    return publish, kill


def test_stdlib_only():
    assert imported_modules("publish_budget_message") <= set(sys.stdlib_module_names) | {"__future__"}


@pytest.mark.parametrize(("now", "start"), [
    (datetime(2026, 11, 15, tzinfo=UTC), "2026-11-01T07:00:00Z"),
    (datetime(2026, 12, 20, tzinfo=UTC), "2026-12-01T08:00:00Z"),
    (datetime(2026, 11, 1, 3, 0, tzinfo=UTC), "2026-10-01T07:00:00Z"),  # still October 31 in US Pacific time
])  # fmt: skip
def test_period_start_is_the_pacific_month_in_utc(mods, now, start):
    assert mods[0].period_start(now) == start


def test_build_message_round_trips_through_kill_parse(mods):
    publish, kill = mods
    now = datetime(2026, 11, 15, tzinfo=UTC)
    attributes, data = publish.build_message(budget_name=BUDGET, cost=3.0, budget_amount=3.0, now=now)
    assert attributes == {"billingAccountId": "AAAAAA-BBBBBB-CCCCCC", "budgetId": "1234-abcd", "schemaVersion": "1.0"}
    event = {"message": {"attributes": attributes, "data": base64.b64encode(data.encode()).decode()}}
    msg = kill.parse(event)
    assert (msg.budget, msg.cost, msg.budget_amount, msg.currency) == (BUDGET, 3.0, 3.0, "CHF")
    assert msg.period_start == datetime(2026, 11, 1, 7, tzinfo=UTC)
    assert kill.decide(msg, expected_budget=BUDGET, now=now) == "kill"
    assert json.loads(data)["alertThresholdExceeded"] == 1.0
    _, below = publish.build_message(budget_name=BUDGET, cost=0.01, budget_amount=3.0, now=now)
    assert "alertThresholdExceeded" not in json.loads(below)


@pytest.mark.parametrize("name", ["billingAccounts/A/budgets/", "projects/p/budgets/b", "x", "billingAccounts//budgets/b"])
def test_a_bad_budget_name_is_refused(mods, name):
    with pytest.raises(ValueError, match="billingAccounts/<account>/budgets/<id>"):
        mods[0].build_message(budget_name=name, cost=1, budget_amount=3, now=datetime(2026, 11, 1, tzinfo=UTC))


def test_main_runs_exactly_the_gcloud_argv_and_never_prints_the_budget(mods, capsys):
    publish, _ = mods
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Done(stdout="123456\n")

    now = datetime(2026, 11, 15, tzinfo=UTC)
    assert publish.main(["--budget-name", BUDGET, "--cost", "3", "--budget-amount", "3"], run=run, now=now) == 0
    (argv, kwargs), = calls
    _, data = publish.build_message(budget_name=BUDGET, cost=3.0, budget_amount=3.0, now=now)
    assert argv == ["gcloud", "pubsub", "topics", "publish", "dashboard-budget", "--project", "synthea-readmission-risk",
                    "--message", data, "--attribute", "billingAccountId=AAAAAA-BBBBBB-CCCCCC,budgetId=1234-abcd,schemaVersion=1.0",
                    "--format=value(messageIds)"]  # fmt: skip
    assert kwargs == {"check": False, "capture_output": True, "text": True}
    out = capsys.readouterr().out
    assert out == "published message 123456\n" and "AAAAAA" not in out


def test_main_exits_1_when_gcloud_fails(mods, capsys):
    code = mods[0].main(["--budget-name", BUDGET, "--cost", "1", "--budget-amount", "3"],
                        run=lambda argv, **kw: Done(returncode=1, stderr="denied\n"), now=datetime(2026, 11, 2, tzinfo=UTC))
    assert code == 1 and capsys.readouterr().err == "denied\n"
