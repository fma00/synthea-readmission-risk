"""prereg.yaml: the committed file is valid and agrees with the code; the loader is strict; the commit check refuses every way of
launching from uncommitted or unpushed state. Design: notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md
(Interfaces section 9)."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
import yaml

from readmission_risk.ops.prereg import (
    PreregError,
    check_prereg_against_code,
    check_prereg_committed,
    load_prereg,
    parse_prereg,
    resolve_test_start,
)
from tests.ops.conftest import REPO, git

TEXT = (REPO / "prereg.yaml").read_text(encoding="utf-8")


def doc() -> dict:
    return yaml.safe_load(TEXT)


def parse(d: dict):
    return parse_prereg(yaml.safe_dump(d, sort_keys=False))


def test_repo_prereg_is_valid():
    prereg = load_prereg(REPO / "prereg.yaml")
    assert set(prereg.populations) == {"pilot_20k", "main_200k"}
    assert prereg.populations["pilot_20k"].seed == 17 and prereg.populations["main_200k"].seed == 29


def test_prereg_matches_code_constants():
    check_prereg_against_code(load_prereg(REPO / "prereg.yaml"))


def test_check_prereg_against_code_reports_every_mismatch():
    d = doc()
    d["label"]["readmission_window_days"] = 31
    d["training"]["xgboost"]["max_depth"] = 5
    d["metrics"]["reported_quantities"].append("n_imaginary")
    with pytest.raises(PreregError) as excinfo:
        check_prereg_against_code(parse(d))
    message = str(excinfo.value)
    assert "label.readmission_window_days" in message
    assert "training.xgboost" in message
    assert "n_imaginary" in message


def test_prereg_jar_sha_matches_setup_script():
    expected = re.search(r'EXPECTED_SHA256="([0-9a-f]{64})"', (REPO / "scripts" / "setup_synthea.sh").read_text()).group(1)
    assert doc()["generation"]["synthea_jar_sha256"] == expected


def test_population_seeds_distinct_and_not_v1():
    d = doc()
    d["populations"]["main_200k"]["seed"] = 17
    with pytest.raises(PreregError, match="must differ"):
        parse(d)
    d = doc()
    d["populations"]["pilot_20k"]["seed"] = 42
    with pytest.raises(PreregError, match="v1's population seed"):
        parse(d)


def test_reference_date_not_v1():
    d = doc()
    d["generation"].update(reference_date="20260916", end_date="20260917")
    with pytest.raises(PreregError, match="v1's reference date"):
        parse(d)


def test_end_date_must_be_reference_date_plus_one():
    d = doc()
    d["generation"]["end_date"] = d["generation"]["reference_date"]
    with pytest.raises(PreregError, match=r"reference_date \+ 1 day"):
        parse(d)


def test_jvm_timezone_must_be_utc():
    d = doc()
    d["generation"]["jvm_timezone"] = "Europe/Zurich"
    with pytest.raises(PreregError, match="jvm_timezone"):
        parse(d)


def test_test_start_rule_resolves():
    assert resolve_test_start("20261012", 3) == "20230101"
    d = doc()
    d["split"]["test_start_date_resolved"] = "20220101"
    with pytest.raises(PreregError, match="the rule gives 20230101"):
        parse(d)


LITERALS = [
    ("phase", "B"),
    ("prereg_version", 2),
    ("split.test_start_rule.kind", "jan1_of_something"),
    ("bootstrap.kind", "naive"),
    ("promotion.roc_auc_rule", "point_estimate"),
    ("promotion.comparators", ["champion"]),
    ("monitoring.alert_rule.metric", "auc"),
    ("monitoring.alert_rule.ci", "normal"),
    ("monitoring.alert_rule.condition", "any"),
    ("monitoring.auc", "alert"),
    ("metrics.primary", "brier_score"),
    ("metrics.definitions.observed_expected_ratio", "mean(y)/mean(p)"),
    ("metrics.definitions.expected_calibration_error.binning", "uniform"),
    ("generation.jdk.distribution", "zulu"),
    ("split.patient_disjoint", False),
    ("split.label_horizon_purge", False),
    ("training.calibration.ensemble", False),
]


def _set(d: dict, dotted: str, value) -> None:
    *path, last = dotted.split(".")
    for part in path:
        d = d[part]
    d[last] = value


@pytest.mark.parametrize("dotted, value", LITERALS)
def test_literal_fields_enforced(dotted, value):
    d = doc()
    _set(d, dotted, value)
    with pytest.raises(PreregError, match=re.escape(dotted)):
        parse(d)


def test_unknown_key_refused():
    d = doc()
    d["bootstrap"]["surprise"] = 1
    with pytest.raises(PreregError, match=r"bootstrap: unknown key\(s\) \['surprise'\]"):
        parse(d)


def test_missing_key_refused():
    d = doc()
    del d["monitoring"]["min_positives_floor"]
    with pytest.raises(PreregError, match=r"monitoring: missing key\(s\) \['min_positives_floor'\]"):
        parse(d)


@pytest.mark.parametrize(
    "dotted, value",
    [
        ("promotion.brier_noninferiority.margin_fraction_of_champion_brier", 1.5),
        ("bootstrap.n_resamples", 999),
        ("bootstrap.ci_level", 1.0),
        ("monitoring.alert_rule.band", [1.1, 1.25]),
        ("power.population_adjustment.trigger_below", 400),
        ("power.population_adjustment.cap", 100000),
        ("populations.pilot_20k.population", 0),
        ("generation.synthea_jar_sha256", "ABC"),
    ],
)
def test_numeric_bounds(dotted, value):
    d = doc()
    _set(d, dotted, value)
    with pytest.raises(PreregError):
        parse(d)


def test_duplicate_key_refused_top_level():
    with pytest.raises(PreregError, match="duplicate key 'phase'"):
        parse_prereg(TEXT.replace("phase: A\n", "phase: A\nphase: A\n", 1))


def test_duplicate_key_refused_nested():
    with pytest.raises(PreregError, match="duplicate key 'seed'"):
        parse_prereg(TEXT.replace("bootstrap: {kind:", "bootstrap: {seed: 1, kind:", 1))


def test_deviation_entry_schema():
    d = doc()
    good = {"date": "2026-10-20", "commit": "a" * 40, "field": "populations.main_200k.population", "old": 200000,
            "new": 250000, "reason": "power: pilot projected 430 positives"}  # fmt: skip
    d["deviations"] = [good]
    parse(d)
    for broken in ({**good, "commit": "abc"}, {**good, "field": "nope.nope"}, {**good, "reason": ""}, {**good, "date": "20261020"}):
        d["deviations"] = [broken]
        with pytest.raises(PreregError, match=r"deviations\[0\]"):
            parse(d)


def test_deviation_pending_only_last():
    d = doc()
    entry = {"date": "2026-10-20", "commit": "pending", "field": "populations.main_200k.population", "old": 1, "new": 2,
             "reason": "x"}  # fmt: skip
    d["deviations"] = [copy.deepcopy(entry)]
    parse(d)
    d["deviations"] = [copy.deepcopy(entry), {**entry, "commit": "b" * 40}]
    with pytest.raises(PreregError, match="only on the last entry"):
        parse(d)


# --- P1: committed, clean, pushed ---


def test_committed_check_passes(git_repo):
    record = check_prereg_committed(git_repo)
    head = git(git_repo, "rev-parse", "HEAD").strip()
    assert record.head_commit == record.prereg_commit == head
    assert len(record.sha256) == 64 and record.prereg_committed_at


def test_refuses_untracked(git_repo):
    git(git_repo, "rm", "-q", "--cached", "prereg.yaml")
    git(git_repo, "commit", "-q", "-m", "untrack")
    git(git_repo, "push", "-q", "origin", "HEAD:main")
    with pytest.raises(PreregError, match="not tracked"):
        check_prereg_committed(git_repo)


def test_refuses_modified(git_repo):
    path = Path(git_repo) / "prereg.yaml"
    path.write_text(path.read_text() + "\n# edit\n")
    with pytest.raises(PreregError, match="not clean"):
        check_prereg_committed(git_repo)


def test_refuses_dirty_tree(git_repo):
    (Path(git_repo) / "other.txt").write_text("x")
    with pytest.raises(PreregError, match="not clean"):
        check_prereg_committed(git_repo)


def test_refuses_head_not_on_remote(git_repo):
    (Path(git_repo) / "other.txt").write_text("x")
    git(git_repo, "add", "other.txt")
    git(git_repo, "commit", "-q", "-m", "local only")
    with pytest.raises(PreregError, match="not on any origin"):
        check_prereg_committed(git_repo)
