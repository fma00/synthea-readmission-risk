from __future__ import annotations

import json
from pathlib import Path

import pytest

from readmission_risk.bundles.champion import Champion, champion_json, read_champion

_REPO = Path(__file__).resolve().parents[2]


def test_round_trip(tmp_path):
    path = tmp_path / "champion.json"
    path.write_text(champion_json("bundles/" + "a" * 64, "b" * 64))
    assert read_champion(path) == Champion(bundle_uri="bundles/" + "a" * 64, sha256="b" * 64)
    assert path.read_text().endswith("}\n")


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b"\xff\xfe",
        b"[]",
        json.dumps({"bundle_uri": "x"}).encode(),
        json.dumps({"bundle_uri": "x", "sha256": "a" * 64, "extra": 1}).encode(),
        json.dumps({"bundle_uri": "", "sha256": "a" * 64}).encode(),
        json.dumps({"bundle_uri": 3, "sha256": "a" * 64}).encode(),
        json.dumps({"bundle_uri": "x", "sha256": "A" * 64}).encode(),
        json.dumps({"bundle_uri": "x", "sha256": "a" * 63}).encode(),
        json.dumps({"bundle_uri": "x", "sha256": None}).encode(),
    ],
)
def test_malformed_pointer_is_refused(tmp_path, content):
    path = tmp_path / "champion.json"
    path.write_bytes(content)
    with pytest.raises(ValueError, match=r"^champion\.json: "):
        read_champion(path)


def test_absent_pointer_is_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError, match="no champion pointer"):
        read_champion(tmp_path / "nope.json")


def test_directory_pointer_is_a_value_error(tmp_path):
    with pytest.raises(ValueError, match=r"^champion\.json: cannot read"):
        read_champion(tmp_path)


def test_committed_champion_json_is_valid():
    champion = read_champion(_REPO / "deploy" / "champion.json")  # schema only: the bundle is gitignored until slice 3
    assert champion.bundle_uri.startswith("bundles/")
