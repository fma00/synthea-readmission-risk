import json
from dataclasses import asdict, replace
from datetime import date

import pytest

from readmission_risk.pipeline.gold_metadata import (
    _INT_FIELDS,
    GOLD_METADATA_FILENAME,
    LABEL_DEFINITION,
    PLANNED_PROCEDURE_CODES,
    GoldMetadata,
    parse_yyyymmdd,
    read_gold_metadata,
    write_gold_metadata,
)


def make_metadata(**overrides) -> GoldMetadata:
    base = {
        "label_definition": LABEL_DEFINITION,
        "reference_date": "20260916",
        "readmission_window_days": 30,
        "lookback_years": 1,
        "planned_procedure_codes": PLANNED_PROCEDURE_CODES,
        "n_rows": 100,
        "n_positive": 7,
        "n_inpatient_stays": 130,
        "n_planned_stays": 20,
        "n_continuation_stays": 5,
        "n_nonterminal_stays": 5,
    }
    base.update(overrides)
    return GoldMetadata(**base)


def raw_json(metadata: GoldMetadata) -> dict:
    return json.loads(json.dumps(asdict(metadata)))  # tuple -> list, like the file on disk


def write_raw(directory, text: str | bytes) -> None:
    path = directory / GOLD_METADATA_FILENAME
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def test_change_detector_for_label_identity():
    assert (LABEL_DEFINITION, PLANNED_PROCEDURE_CODES) == (
        "unplanned_readmission_v1",
        ("33195004", "367336001", "703423002"),
    ), (
        "The label's identity changed. Any change to PLANNED_PROCEDURE_CODES or to the label/cohort rules must bump "
        "LABEL_DEFINITION (e.g. ..._v2); then update this test and the docs "
        "(notes/eg-new-feature/readmission-label-planned-exclusion-2026-09-20.md)."
    )


def test_round_trip_all_fields(tmp_path):
    metadata = make_metadata()
    path = write_gold_metadata(tmp_path, metadata)
    assert path == tmp_path / GOLD_METADATA_FILENAME
    assert read_gold_metadata(tmp_path) == metadata
    assert len(asdict(metadata)) == 12


def test_file_is_sorted_key_json_ending_with_newline_and_leaves_no_tmp(tmp_path):
    write_gold_metadata(tmp_path, make_metadata())
    text = (tmp_path / GOLD_METADATA_FILENAME).read_text(encoding="utf-8")
    assert text.endswith("}\n")
    obj = json.loads(text)
    assert list(obj) == sorted(obj)
    assert obj["planned_procedure_codes"] == list(PLANNED_PROCEDURE_CODES)
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"planned_procedure_codes": ("703423002", "367336001")},  # unsorted
        {"planned_procedure_codes": ("367336001", "367336001")},  # duplicate
        {"planned_procedure_codes": list(PLANNED_PROCEDURE_CODES)},  # a list is rejected, not coerced
        {"n_rows": 120, "n_planned_stays": 20, "n_inpatient_stays": 130},  # n_rows + n_planned > n_inpatient
    ],
)
def test_write_refuses_invalid_object_without_creating_file(tmp_path, overrides):
    with pytest.raises(ValueError):
        write_gold_metadata(tmp_path, make_metadata(**overrides))
    assert not (tmp_path / GOLD_METADATA_FILENAME).exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_write_to_non_directory_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        write_gold_metadata(tmp_path / "does-not-exist", make_metadata())


def test_read_missing_file_mentions_rebuild(tmp_path):
    with pytest.raises(FileNotFoundError, match="rebuild"):
        read_gold_metadata(tmp_path)


def test_read_directory_named_like_the_file_is_value_error(tmp_path):
    (tmp_path / GOLD_METADATA_FILENAME).mkdir()
    with pytest.raises(ValueError, match=GOLD_METADATA_FILENAME):
        read_gold_metadata(tmp_path)


def test_read_invalid_utf8_names_the_path(tmp_path):
    write_raw(tmp_path, b"\xff\xfe\x00 not utf-8")
    with pytest.raises(ValueError, match=GOLD_METADATA_FILENAME):
        read_gold_metadata(tmp_path)


def test_read_duplicate_key_names_path_and_key(tmp_path):
    good = json.dumps(raw_json(make_metadata()))
    write_raw(tmp_path, good[:-1] + ', "n_rows": 5}')
    with pytest.raises(ValueError, match=rf"{GOLD_METADATA_FILENAME}.*n_rows"):
        read_gold_metadata(tmp_path)


@pytest.mark.parametrize("text", ["{not json", "[1, 2, 3]", '"a string"'])
def test_read_invalid_json_or_non_object_names_the_path(tmp_path, text):
    write_raw(tmp_path, text)
    with pytest.raises(ValueError, match=GOLD_METADATA_FILENAME):
        read_gold_metadata(tmp_path)


def test_read_missing_key_names_path_and_key(tmp_path):
    obj = raw_json(make_metadata())
    del obj["n_nonterminal_stays"]
    write_raw(tmp_path, json.dumps(obj))
    with pytest.raises(ValueError, match=rf"{GOLD_METADATA_FILENAME}.*n_nonterminal_stays"):
        read_gold_metadata(tmp_path)


def test_read_unknown_key_names_path_and_key(tmp_path):
    obj = raw_json(make_metadata())
    obj["surprise"] = 1
    write_raw(tmp_path, json.dumps(obj))
    with pytest.raises(ValueError, match=rf"{GOLD_METADATA_FILENAME}.*surprise"):
        read_gold_metadata(tmp_path)


# (key, bad value): every case must raise ValueError naming the file and the key
BAD_VALUES = [
    ("metadata_version", 2),
    ("reference_date", "2026-09-16"),
    ("reference_date", "20261399"),
    ("reference_date", "2026091"),
    ("reference_date", "２０２６０９１６"),  # full-width digits
    ("reference_date", 20260916),
    ("readmission_window_days", 0),
    ("readmission_window_days", "30"),
    ("lookback_years", 0),
    ("label_definition", ""),
    ("label_definition", 5),
    ("n_rows", -1),
    ("n_positive", 101),  # > n_rows
    ("n_planned_stays", 131),  # > n_inpatient_stays
    ("n_continuation_stays", 131),
    ("n_nonterminal_stays", 131),
    ("planned_procedure_codes", []),
    ("planned_procedure_codes", [1]),
    ("planned_procedure_codes", "703423002"),
    ("planned_procedure_codes", ["703423002", "367336001"]),  # unsorted
    ("planned_procedure_codes", ["367336001", "367336001"]),  # duplicated
    *[(name, value) for name in _INT_FIELDS for value in (True, 1.0)],  # bool/float rejected on EVERY integer field
]


@pytest.mark.parametrize("key, value", BAD_VALUES)
def test_read_rejects_bad_value_naming_file_and_key(tmp_path, key, value):
    obj = raw_json(make_metadata())
    obj[key] = value
    write_raw(tmp_path, json.dumps(obj))
    with pytest.raises(ValueError, match=rf"{GOLD_METADATA_FILENAME}.*{key}"):
        read_gold_metadata(tmp_path)


def test_read_rejects_cross_field_invariant_naming_all_three_fields(tmp_path):
    obj = raw_json(make_metadata())
    obj.update(n_rows=120, n_planned_stays=20, n_inpatient_stays=130)  # 140 > 130
    write_raw(tmp_path, json.dumps(obj))
    with pytest.raises(ValueError) as excinfo:
        read_gold_metadata(tmp_path)
    message = str(excinfo.value)
    assert GOLD_METADATA_FILENAME in message
    assert all(name in message for name in ("n_rows", "n_planned_stays", "n_inpatient_stays"))


def test_read_does_not_compare_label_definition_to_module_constants(tmp_path):
    other = replace(make_metadata(), label_definition="some_other_definition", planned_procedure_codes=("1",))
    write_gold_metadata(tmp_path, other)
    assert read_gold_metadata(tmp_path) == other


def test_parse_yyyymmdd_returns_date_and_documented_messages():
    assert parse_yyyymmdd("20260916", "reference_date") == date(2026, 9, 16)
    with pytest.raises(ValueError, match=r"reference_date must be a YYYYMMDD string; got '2026-09-16'"):
        parse_yyyymmdd("2026-09-16", "reference_date")
    with pytest.raises(ValueError, match=r"test_start_date is not a valid calendar date: '20261399'"):
        parse_yyyymmdd("20261399", "test_start_date")
