"""M5's logic against a fake client: every assertion has a mutation, every mutation must fail with exactly its own ID,
the clean copies must pass, and nothing is left behind. The SQL's real behaviour is proven live (L8)."""

from __future__ import annotations

import re
from datetime import date

import pytest

from readmission_risk.warehouse import seeded_check as m5
from readmission_risk.warehouse.bigquery import table_id
from readmission_risk.warehouse.data_tests import DataTestResult, assertion_descriptions
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob

RUN_DATE = date(2025, 6, 30)


def test_every_assertion_has_exactly_one_mutation():
    assert list(m5.PREDICTION_MUTATIONS) == list(assertion_descriptions("predictions"))
    assert list(m5.RUN_MUTATIONS) == list(assertion_descriptions("scoring_runs"))


def test_mutations_back_quote_every_column():
    for statement in [*m5.PREDICTION_MUTATIONS.values(), *m5.RUN_MUTATIONS.values()]:
        assert "{t}" in statement
        without_strings = re.sub(r"'[^']*'", "''", statement)
        for name in ("rank", "risk_score", "partition", "as_of_date", "scoring_run_id", "status", "published"):
            assert re.search(rf"(?<!`)\b{name}\b(?!`)", without_strings) is None, (name, statement)


def test_copy_names_pass_table_id():
    for assertion_id in ["clean", *m5.PREDICTION_MUTATIONS, *m5.RUN_MUTATIONS]:
        for kind in ("p", "r"):
            name = m5.copy_name(kind, assertion_id)
            dataset, table = name.split(".")[1:]
            assert table_id(dataset, table) == name and dataset == "scratch"


class _World:
    """Tracks which copy got which mutation; the data-test stub answers like a correct script would, unless told to
    misbehave for one label."""

    def __init__(self, *, misbehave: dict[str, DataTestResult] | None = None, n_ids=1, n=12, second=0.3, newest=True):
        self.mutated: dict[str, str] = {}
        self.created: set[str] = set()
        self.misbehave = misbehave or {}
        self.fake = FakeBigQuery(on_query=self.on_query)
        self.pre = {"n_ids": n_ids, "run_id": "r1", "n": n, "second": second}
        self.newest = newest

    def on_query(self, sql, config, job_id):
        if sql.startswith("CREATE TABLE"):
            self.created.add(re.search(r"`([^`]+)`", sql).group(1))
            return FakeJob()
        if sql.startswith("SELECT COUNT(DISTINCT"):
            return FakeJob(rows=[self.pre])
        if sql.startswith("SELECT `scoring_run_id`"):
            return FakeJob(rows=[{"scoring_run_id": "n1"}] if self.newest else [])
        for label, template in [*m5.PREDICTION_MUTATIONS.items(), *m5.RUN_MUTATIONS.items()]:
            prefix = template.split("{t}", 1)[0]
            if sql.startswith(prefix) and sql[len(prefix) :].startswith("`"):
                table = re.match(r"`([^`]+)`", sql[len(prefix) :]).group(1)
                if template.replace("{t}", f"`{table}`") == sql:
                    self.mutated[table] = label
                    return FakeJob()
        raise AssertionError(f"unexpected SQL: {sql[:100]}")

    def run_data_tests(self, client, name, *, table, run_date=None, scoring_run_id=None):
        label = self.mutated.get(table, "clean")
        if label in self.misbehave:
            return self.misbehave[label]
        if label == "clean":
            return DataTestResult(name, True, None)
        return DataTestResult(name, False, assertion_descriptions(name)[label])


def _run(world: _World, monkeypatch):
    monkeypatch.setattr(m5, "run_data_tests", world.run_data_tests)
    return m5.seeded_check(world.fake, run_date=RUN_DATE)


def test_all_assertions_fire_and_copies_are_dropped(monkeypatch):
    world = _World()
    results = _run(world, monkeypatch)
    assert list(results["predictions"]) == ["clean", *m5.PREDICTION_MUTATIONS]
    assert list(results["scoring_runs"]) == ["clean", *m5.RUN_MUTATIONS]
    assert all(r.passed for r in (results["predictions"]["clean"], results["scoring_runs"]["clean"]))
    assert set(world.mutated.values()) == {*m5.PREDICTION_MUTATIONS, *m5.RUN_MUTATIONS}
    dropped = {c[1] for c in world.fake.calls if c[0] == "delete_table"}
    assert dropped == world.created and len(world.created) == 2 + 17  # every copy created was dropped
    assert all(c[2].maximum_bytes_billed == 100 * 1024**2 for c in world.fake.calls if c[0] == "query")


@pytest.mark.parametrize(
    "misbehave, message",
    [
        ({"clean": DataTestResult("predictions", False, "PRED-02: x")}, "clean predictions copy failed"),
        ({"PRED-05": DataTestResult("predictions", True, None)}, "PRED-05 mutation should fail with PRED-05; it passed"),
        ({"RUNS-04": DataTestResult("scoring_runs", False, "RUNS-02: y")}, "it failed with RUNS-02: y"),
    ],
)
def test_wrong_outcomes_raise_with_partial_results(monkeypatch, misbehave, message):
    world = _World(misbehave=misbehave)
    with pytest.raises(m5.SeededCheckError, match=re.escape(message)) as info:
        _run(world, monkeypatch)
    assert info.value.results["predictions"]  # gathered before the failure
    dropped = {c[1] for c in world.fake.calls if c[0] == "delete_table"}
    assert world.created and world.created <= dropped  # leftover copies dropped in the finally


@pytest.mark.parametrize(
    "kwargs, message",
    [({"n_ids": 2}, "publish a run"), ({"n": 1}, "publish a run"), ({"second": 0.0}, "publish a run"), ({"newest": False}, "run the scorer first")],
)
def test_unmet_preconditions(monkeypatch, kwargs, message):
    with pytest.raises(m5.SeededCheckError, match=message):
        _run(_World(**kwargs), monkeypatch)
