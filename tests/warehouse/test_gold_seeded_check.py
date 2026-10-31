"""M5 for GOLD (v3 slice 1): the fixture and mutations are right by construction (a pure-Python evaluation of GOLD-01..07 in
gold.sql's order), every statement targets scratch_gold only, and errors carry the partial results."""

from __future__ import annotations

import copy

import pytest
from google.api_core import exceptions as api

from readmission_risk.warehouse.data_tests import DataTestResult, assertion_descriptions
from readmission_risk.warehouse.gold import GOLD_NOT_NULL_COLUMNS
from readmission_risk.warehouse.gold_seeded_check import (
    FIXTURE_ROWS,
    MUTATIONS,
    N_POSITIVE,
    N_ROWS,
    GoldSeededCheckError,
    expected,
    insert_sql,
    prediction_row,
    seed_names,
    seeded_gold_check,
)
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob, assertion_error

# The mutations, applied in Python to (gold rows, prediction rows); kept in step with MUTATIONS by the test below.
_PY_MUTATIONS = {
    "GOLD-01": lambda g, p: _set(g, "seed-e10", index_stop=None),
    "GOLD-02": lambda g, p: g.remove(next(r for r in g if r["encounter_id"] == "seed-e10")),
    "GOLD-03": lambda g, p: _set(g, "seed-e10", is_readmitted=1),
    "GOLD-04": lambda g, p: _set(g, "seed-e10", encounter_id="seed-e09"),
    "GOLD-05": lambda g, p: _set(g, "seed-e10", label_horizon=None),
    "GOLD-06": lambda g, p: (_set(g, "seed-e01", is_readmitted=2), _set(g, "seed-e02", is_readmitted=0)),
    "GOLD-07": lambda g, p: p[0].update(encounter_id="seed-absent"),
}


def _set(rows, eid, **values):
    for row in rows:
        if row["encounter_id"] == eid:
            row.update(values)


def _first_failure(gold, preds) -> str | None:
    """gold.sql's assertions in order, over Python rows (None = all pass)."""
    encounters = {r["encounter_id"] for r in gold}
    predicted = [p for p in preds if p["gold_fingerprint"] == "seedfp"]
    checks = [
        ("GOLD-01", all(r["index_stop"] is not None for r in gold)),
        ("GOLD-02", len(gold) == N_ROWS),
        ("GOLD-03", sum(r["is_readmitted"] for r in gold) == N_POSITIVE),
        ("GOLD-04", len(encounters) == len(gold) and None not in encounters),
        ("GOLD-05", all(r[c] is not None for r in gold for c in GOLD_NOT_NULL_COLUMNS)),
        ("GOLD-06", all(r["is_readmitted"] in (0, 1) for r in gold)),
        ("GOLD-07", len(predicted) >= 1 and all(p["encounter_id"] in encounters for p in predicted)),
    ]
    return next((label for label, ok in checks if not ok), None)


def test_every_gold_assertion_has_exactly_one_mutation():
    assert list(MUTATIONS) == list(assertion_descriptions("gold")) == list(_PY_MUTATIONS)


def test_the_clean_fixture_passes_and_each_mutation_breaks_exactly_its_own_assertion():
    assert _first_failure(list(FIXTURE_ROWS), [prediction_row()]) is None
    for key, mutate in _PY_MUTATIONS.items():
        gold, preds = copy.deepcopy(list(FIXTURE_ROWS)), [prediction_row()]
        mutate(gold, preds)
        assert _first_failure(gold, preds) == key


def test_the_fixture_has_the_counts_the_parameters_claim():
    assert len(FIXTURE_ROWS) == N_ROWS and sum(r["is_readmitted"] for r in FIXTURE_ROWS) == N_POSITIVE
    assert prediction_row()["encounter_id"] in {r["encounter_id"] for r in FIXTURE_ROWS}


def test_seed_names_are_valid_scratch_gold_identifiers():
    gold, pred = seed_names("1234abcd", "GOLD-01")
    assert gold == "synthea-readmission-risk.scratch_gold.gold_seed_1234abcd_gold_01" and pred == gold + "_pred"


def _client(fail_on: str | None = None):
    def on_query(sql, config, job_id):
        if "DECLARE c DEFAULT" in sql:  # gold.sql (it starts with its comment header)
            key = next((k for k in MUTATIONS if f"_{k.lower().replace('-', '_')}`" in sql), None)
            if key is not None:
                return FakeJob(raise_on_result=assertion_error(assertion_descriptions("gold")[key]))
        if fail_on and fail_on in sql:
            return FakeJob(raise_on_result=api.ServiceUnavailable("503"))
        return FakeJob()

    return FakeBigQuery(on_query=on_query)


def test_seeded_gold_check_with_a_fake_client():
    client = _client()
    results = seeded_gold_check(client)
    assert expected(results)
    created = [c[1] for c in client.calls if c[0] == "create_table"]
    names = [f"{t.project}.{t.dataset_id}.{t.table_id}" for t in created]
    assert len(names) == 16 and all(".scratch_gold.gold_seed_" in n for n in names)
    deleted = {c[1] for c in client.calls if c[0] == "delete_table"}
    assert set(names) <= deleted
    for _, sql, *_ in (c for c in client.calls if c[0] == "query"):
        assert "serving.gold`" not in sql and "scratch.`" not in sql  # only scratch_gold tables are written


def test_an_error_carries_the_partial_results_and_still_drops_the_tables():
    client = _client(fail_on="_gold_03`")
    with pytest.raises(GoldSeededCheckError) as raised:
        seeded_gold_check(client)
    assert set(raised.value.results) == {"clean", "GOLD-01", "GOLD-02"}
    created = {f"{t.project}.{t.dataset_id}.{t.table_id}" for _, t in (c for c in client.calls if c[0] == "create_table")}
    assert created <= {c[1] for c in client.calls if c[0] == "delete_table"}


def test_expected_rules():
    ok = {"clean": DataTestResult("gold", True, None)}
    ok |= {i: DataTestResult("gold", False, d) for i, d in assertion_descriptions("gold").items()}
    assert expected(ok)
    assert not expected({**ok, "GOLD-03": DataTestResult("gold", True, None)})
    assert not expected({**ok, "GOLD-03": DataTestResult("gold", False, assertion_descriptions("gold")["GOLD-04"])})
    assert not expected({**ok, "clean": DataTestResult("gold", False, "GOLD-01: x")})


def test_insert_sql_refuses_quotes_in_fixture_strings():
    with pytest.raises(ValueError, match="quotes"):
        insert_sql("synthea-readmission-risk.scratch_gold.t", "predictions", [{**prediction_row(), "model_name": "a'b"}])
