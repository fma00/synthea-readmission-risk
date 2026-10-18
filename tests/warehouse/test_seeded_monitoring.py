"""M5 for serving.monitoring (v2 slice 7): synthetic seeded copies, each mutation touching only the base row, every copy
dropped. FakeBigQuery executes no SQL, so this proves the routing and the generated literals; make m5-monitoring proves
the SQL live."""

from __future__ import annotations

import re

import pytest

from readmission_risk.warehouse import seeded_check as sc
from readmission_risk.warehouse.bigquery import load_schema
from readmission_risk.warehouse.data_tests import assertion_descriptions
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob, assertion_error

LABELS = assertion_descriptions("monitoring")


def _world(fire=lambda table_label: table_label):
    def on_query(sql, config, job_id):
        match = re.search(r"m5m_(mon_\d{2}|clean)_[0-9a-f]{8}", sql)
        if "DECLARE c DEFAULT" in sql and match and match.group(1) != "clean":
            label = fire(match.group(1).upper().replace("_", "-"))
            return FakeJob(raise_on_result=assertion_error(LABELS[label]))
        return FakeJob()

    return FakeBigQuery(on_query=on_query)


def test_every_assertion_fires_and_every_copy_is_dropped():
    fake = _world()
    results = sc.seeded_monitoring_check(fake)
    assert list(results["monitoring"]) == ["clean", *LABELS]
    created = {m.group(0) for _, sql, *_ in fake.calls if isinstance(sql, str) for m in re.finditer(r"scratch\.m5m_\w+", sql)}
    dropped = {c[1] for c in fake.calls if c[0] == "delete_table"}
    assert len(created) == 13 and {d.split(".", 1)[1] for d in dropped} == created


def test_a_mutation_firing_the_wrong_assertion_is_an_error():
    fake = _world(fire=lambda label: "MON-01")
    with pytest.raises(sc.SeededCheckError, match="MON-02 mutation should fail with MON-02"):
        sc.seeded_monitoring_check(fake)


def test_mutations_touch_only_the_base_row_and_nulls_are_typed():
    assert set(sc.MONITORING_MUTATIONS) == set(LABELS)
    assert all("WHERE `monitoring_run_id` = 'm5base'" in sql for sql in sc.MONITORING_MUTATIONS.values())
    literal = sc.monitoring_struct("m5base")
    names = re.findall(r" AS `(\w+)`", literal)
    assert names == [f["name"] for f in load_schema("monitoring")]
    assert re.search(r"(?<!CAST\()NULL AS `", literal) is None  # every NULL is CAST(NULL AS <type>)
    rows = sc.monitoring_clean_rows_sql()
    assert rows.count("STRUCT(") == 5 and "'m5clock'" in rows and "'m5incomplete'" in rows
