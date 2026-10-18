"""scripts/tick_issues.py (the issues job, v2 slice 7): one issue per kind, create vs comment, explicit --repo."""

from __future__ import annotations

import json
import sys

import pytest

from tests.scripts.stdlib_loader import Done, FakeGh, imported_modules, load_isolated

OK = {job: {"result": "success"} for job in ("clock", "score", "evaluate", "ledger-render", "ledger-commit")}


@pytest.fixture
def module(tmp_path):
    return load_isolated("tick_issues", tmp_path)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))


def test_stdlib_only():
    assert imported_modules("tick_issues") <= set(sys.stdlib_module_names) | {"__future__"}


@pytest.mark.parametrize(
    ("changes", "kinds"),
    [
        ({}, []),
        ({"score": {"result": "failure"}}, ["failure"]),
        ({"ledger-commit": {"result": "cancelled"}}, ["failure"]),
        ({"evaluate": {"result": "skipped"}}, []),  # a skip follows an upstream failure that is counted itself
    ],
)
def test_failure_planning(module, changes, kinds):
    assert [a.kind for a in module.plan_issue_actions({**OK, **changes}, None, "u")] == kinds


def test_alert_planning(module):
    evaluation = {"verdict": "alert", "sim_date": "2023-09-04", "oe_ratio": 1.6, "band_low": 0.7, "band_high": 1.3}
    actions = module.plan_issue_actions(OK, evaluation, "u")
    assert [a.kind for a in actions] == ["alert"] and "2023-09-04" in actions[0].body and "1.6" in actions[0].body
    assert "band [0.7, 1.3]" in actions[0].body  # the band comes from the evaluation (prereg), never a literal


def test_create_then_comment(module, tmp_path):
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(json.dumps({"verdict": "alert", "sim_date": "2023-09-04"}))
    state = {"open": []}

    def answer(argv):
        assert argv[-2:] == ["--repo", "o/r"]
        if argv[1:3] == ["issue", "list"]:
            return Done(stdout=json.dumps(state["open"]))
        if argv[1:3] == ["issue", "create"]:
            state["open"] = [{"number": 7}]
            return Done(stdout="https://github.com/o/r/issues/7\n")
        return Done()

    gh = FakeGh(answer)
    args = ["--results-json", json.dumps({**OK, "score": {"result": "failure"}}), "--evaluation", str(evaluation), "--run-url", "u"]
    assert module.main(args, run=gh) == 0
    assert module.main(args, run=gh) == 0
    verbs = [c[1:3] for c in gh.calls if c[1] == "issue" and c[2] in ("create", "comment")]
    assert verbs == [["issue", "create"], ["issue", "comment"], ["issue", "comment"], ["issue", "comment"]]
    labels = [c[3] for c in gh.calls if c[1:3] == ["label", "create"]]
    assert set(labels) == {"monitoring-failure", "monitoring-alert"}
    assert (tmp_path / "summary.md").read_text().splitlines()[-1].startswith("Issues: commented #7")


def test_missing_evaluation_and_gh_errors(module, tmp_path):
    def broken(argv):
        return Done(returncode=1, stderr="HTTP 403")

    args = ["--results-json", json.dumps({**OK, "clock": {"result": "failure"}}), "--evaluation", str(tmp_path / "none.json"), "--run-url", "u"]
    assert module.main(args, run=FakeGh(broken)) == 1
    assert "Issues: failed" in (tmp_path / "summary.md").read_text()
