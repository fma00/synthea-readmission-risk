"""scripts/run_promotion_gate.py and scripts/prereg_append.py (v2 slice 6b): exit codes, reports, the gate table, the
no-overwrite rule, preconditions; the append-only lock and Phase B with every refusal. run_gate is injected (its own tests
are in tests/ops/test_gate_run.py); git is a fake."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess

import pytest
import yaml

from readmission_risk.ops.prereg import PHASE_B_REPORT_PATHS, parse_prereg
from tests.ops.conftest import REPO

PREREG_BYTES = (REPO / "prereg.yaml").read_bytes()
ENV = {"CLOUDSDK_CORE_PROJECT": "synthea-readmission-risk"}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_git(head_blob=PREREG_BYTES, *, dirty=False, head="a" * 40):
    def run_git(argv, **kwargs):
        args = argv[3:]
        if args == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(argv, 0, f"{head}\n".encode(), b"")
        if args == ["show", "HEAD:prereg.yaml"]:
            return subprocess.CompletedProcess(argv, 0, head_blob, b"")
        if args == ["status", "--porcelain"]:
            return subprocess.CompletedProcess(argv, 0, b"?? x\n" if dirty else b"", b"")
        return subprocess.CompletedProcess(argv, 128, b"", b"fatal")

    return run_git


def _rule(name, value, threshold, passed, stat="s"):
    return {"name": name, "statistic": stat, "value": value, "threshold": threshold, "passed": passed}


def body(decision="REFUSE", *, rehearsal=False, mode="main", lock=None):
    entry = {"run_id": "r" * 32, "point": {"roc_auc": 0.87, "brier_score": 0.0123, "observed_expected_ratio": 1.05},
             "intervals": {"roc_auc": [0.85, 0.89], "brier_score": [0.011, 0.0135], "observed_expected_ratio": [0.95, None]},
             "roc_auc_ci_lower_ok": True, "observed_expected_overlap_ok": True, "accepted": True}  # fmt: skip
    return {
        "run": "pilot_20k" if rehearsal else "main_200k", "mode": mode, "rehearsal": rehearsal, "analysis_lock": lock,
        "lock_time_utc": None, "phase_a_commit": "1" * 40, "power": {"n_test": 31659, "n_test_positive": 616, "min_test_positives": 400},
        "gold_fingerprint": "f" * 64, "split_summary": {"n_test": 31659},
        "runs_on_this_gold": None if rehearsal else {"challenger-test": {"active": 2, "deleted": 0}},
        "challenger": {"run_id": "c" * 32, "bundle_id": "b" * 64, "git_commit": "2" * 40,
                       "replaced_by": "constant_train_prevalence" if mode == "negative_control" else None},
        "champion": {"bundle_id": "d" * 64, "run_id": "e" * 32},
        "gate": {"point": {}, "differences": {}, "decision": decision, "lookup_still_wins": decision == "REFUSE",
                 "n_resamples_used": 2000, "n_resamples_skipped": 0,
                 "rules": [_rule("roc_auc_vs_champion", 0.0123, 0.0, True, "lower bound of the 95% paired CI of roc_auc(challenger) - roc_auc(champion)"),
                           _rule("roc_auc_vs_reason_code_lookup", -0.004, 0.0, decision == "PROMOTE"),
                           _rule("brier_noninferior_vs_champion", None, 0.0007, decision == "PROMOTE")]},
        "frozen_v1": {"rows": "modeling_rows", "n_rows": 157827, "n_positive": 2540,
                      "models": {"logistic_regression": entry, "xgboost_calibrated": {**entry, "accepted": False}}},
        "xgboost_reported": {"run_id": "x" * 32, "point": {}},
    }  # fmt: skip


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "prereg.yaml").write_bytes(PREREG_BYTES)
    return tmp_path


GATE_ARGV = ["--run", "main_200k", "--challenger-experiment", "x", "--challenger-bundle", "bundles/y"]


def _gate(repo, *, result=None, raises=None, argv=GATE_ARGV, git=None, environ=ENV):
    cli = _load("run_promotion_gate")

    def run_gate(config, **kwargs):
        if raises:
            raise raises
        return result

    return cli.main(argv, run_gate=run_gate, run_git=git or fake_git(), repo_root=repo, environ=environ)


def _report(repo, stem="gate", run="main_200k"):
    return json.loads((repo / "data" / "gate" / run / f"{stem}_report.json").read_text())


def test_promote_exits_0_and_refuse_exits_3(repo):
    assert _gate(repo, result=body("PROMOTE")) == 0
    report = _report(repo)
    assert report["error"] is None and report["head_commit"] == "a" * 40
    assert report["prereg_sha256"] == hashlib.sha256(PREREG_BYTES).hexdigest() and report["generated_at_utc"].endswith("Z")
    (repo / "data" / "gate" / "main_200k" / "gate_report.json").unlink()
    assert _gate(repo, result=body("REFUSE")) == 3


def test_gate_table_lines(repo):
    _gate(repo, result=body("REFUSE"))
    lines = (repo / "data" / "gate" / "main_200k" / "gate_table.md").read_text().split("\n")
    assert lines[:6] == ["# Promotion gate: main_200k (main)", "", "Decision: **REFUSE**", "",
                         "| Rule | Statistic | Value | Threshold | Result |", "|---|---|---|---|---|"]  # fmt: skip
    assert lines[6] == ("| roc_auc_vs_champion | lower bound of the 95% paired CI of roc_auc(challenger) - roc_auc(champion) "
                        "| 0.0123 | > 0.0000 | PASS |")  # fmt: skip
    assert lines[8] == "| brier_noninferior_vs_champion | s | n/a | <= 0.0007 | FAIL |"
    assert lines[10] == "## Frozen v1 (all modeling rows, n=157827, positives=2540)"
    assert lines[12] == "| Model | ROC-AUC [95% CI] | Brier [95% CI] | O/E [95% CI] | Accepted |"
    assert lines[14] == "| logistic_regression | 0.8700 [0.8500, 0.8900] | 0.0123 [0.0110, 0.0135] | 1.0500 [0.9500, n/a] | yes |"
    assert lines[15].endswith("| no |") and lines[-1] == ""


def test_rehearsal_banner_and_file_names(repo):
    argv = ["--run", "pilot_20k", "--challenger-experiment", "x", "--challenger-bundle", "y", "--rehearsal", "--negative-control"]
    assert _gate(repo, result=body(rehearsal=True, mode="negative_control"), argv=argv, git=fake_git(dirty=True)) == 3
    table = (repo / "data" / "gate" / "pilot_20k" / "negative_control_table.md").read_text()
    assert table.startswith("REHEARSAL: pipeline sanity only, not a result\n\n# Promotion gate: pilot_20k (negative_control)")


def test_error_after_preconditions_exits_2_with_an_error_report_and_no_table(repo):
    table = repo / "data" / "gate" / "main_200k" / "gate_table.md"
    table.parent.mkdir(parents=True)
    table.write_text("stale")
    assert _gate(repo, raises=RuntimeError("boom")) == 2
    report = _report(repo)
    assert set(report) == {"run", "mode", "rehearsal", "head_commit", "prereg_sha256", "generated_at_utc", "error"}
    assert report["error"] == "RuntimeError: boom" and not table.exists()


def test_successful_main_report_is_never_overwritten_but_an_error_report_is(repo):
    assert _gate(repo, raises=RuntimeError("first")) == 2
    assert _gate(repo, result=body("REFUSE")) == 3  # the error report is overwritten
    before = (repo / "data" / "gate" / "main_200k" / "gate_report.json").read_bytes()
    assert _gate(repo, result=body("PROMOTE")) == 2
    assert (repo / "data" / "gate" / "main_200k" / "gate_report.json").read_bytes() == before


@pytest.mark.parametrize(
    ("case", "kwargs"),
    [
        ("project", {"environ": {}}),
        ("dirty", {"git": fake_git(dirty=True)}),
        ("prereg_mismatch", {"git": fake_git(PREREG_BYTES + b"# x\n")}),
    ],
)
def test_preconditions_exit_2_without_a_report(repo, case, kwargs, capsys):
    assert _gate(repo, result=body(), **kwargs) == 2
    assert not (repo / "data").exists() and "ERROR:" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[*GATE_ARGV, "--rehearsal"],
                                  ["--run", "pilot_20k", "--challenger-experiment", "x", "--challenger-bundle", "y"]])
def test_mode_and_run_mismatch_is_refused_before_any_report(repo, argv):
    assert _gate(repo, result=body("REFUSE")) == 3
    before = (repo / "data" / "gate" / "main_200k" / "gate_report.json").read_bytes()
    assert _gate(repo, raises=RuntimeError("must not be called"), argv=argv) == 2
    assert (repo / "data" / "gate" / "main_200k" / "gate_report.json").read_bytes() == before
    assert not (repo / "data" / "gate" / "pilot_20k").exists()


def test_unparsable_prereg_exits_2(repo, capsys):
    (repo / "prereg.yaml").write_bytes(b"prereg_version: 1\n")
    assert _gate(repo, result=body(), git=fake_git(b"prereg_version: 1\n")) == 2
    assert "invalid" in capsys.readouterr().err


# --- prereg_append --------------------------------------------------------------------------------------------------------


def _append(repo, argv, git=None, monkeypatch=None):
    cli = _load("prereg_append")
    return cli, cli.main(argv, repo_root=repo, run_git=git or fake_git(head="2" * 40), today=__import__("datetime").date(2026, 10, 17))


LOCK_ARGV = ["lock", "--experiment-name", "readmission-risk-v2-main200k", "--tracking-dir", "mlflow"]


def test_lock_appends_and_keeps_phase_a_as_prefix(repo):
    _, code = _append(repo, LOCK_ARGV)
    text = (repo / "prereg.yaml").read_bytes()
    assert code == 0 and text.startswith(PREREG_BYTES)
    lock = parse_prereg(text.decode()).analysis_lock
    assert lock == {"locked_on": "2026-10-17", "challenger_model": "logistic_regression",
                    "experiment_name": "readmission-risk-v2-main200k", "frozen_v1_rows": "modeling_rows",
                    "negative_control": "constant_train_prevalence", "code_commit": "2" * 40}  # fmt: skip
    _, again = _append(repo, LOCK_ARGV)
    assert again == 2  # an existing lock is refused


def test_lock_refuses_a_dirty_tree(repo):
    _, code = _append(repo, LOCK_ARGV, git=fake_git(dirty=True))
    assert code == 2 and (repo / "prereg.yaml").read_bytes() == PREREG_BYTES


def _store(repo):
    from mlflow.tracking import MlflowClient

    from readmission_risk.models.tracking import mlflow_tracking_uri

    (repo / "mlflow").mkdir()
    return MlflowClient(tracking_uri=mlflow_tracking_uri(repo / "mlflow"))


def test_lock_refuses_an_existing_experiment(repo):
    client = _store(repo)
    client.create_experiment("readmission-risk-v2-main200k")
    _, code = _append(repo, LOCK_ARGV)
    assert code == 2 and (repo / "prereg.yaml").read_bytes() == PREREG_BYTES


def test_lock_refuses_a_deleted_run_on_the_locked_gold(repo):
    from readmission_risk.models.constants import ROWHASH_TAG

    client = _store(repo)
    experiment = client.create_experiment("peek")
    run = client.create_run(experiment, tags={ROWHASH_TAG: "80edd13dcc967d69b6fa4cfa17b4d7fa40188f8e7e17b2031b833332a59c63f3"})
    client.set_terminated(run.info.run_id)
    client.delete_run(run.info.run_id)
    _, code = _append(repo, LOCK_ARGV)
    assert code == 2 and (repo / "prereg.yaml").read_bytes() == PREREG_BYTES


def test_lock_refuses_a_p1b_failure(repo, monkeypatch):
    from readmission_risk.ops.prereg import PreregError

    cli = _load("prereg_append")

    def boom(prereg):
        raise PreregError("training.seed differs")

    monkeypatch.setattr(cli, "check_prereg_against_code", boom)
    assert cli.main(LOCK_ARGV, repo_root=repo, run_git=fake_git(head="2" * 40)) == 2


def _lock_repo(repo):
    _append(repo, LOCK_ARGV)
    return parse_prereg((repo / "prereg.yaml").read_text()).analysis_lock


def _write_reports(repo, lock, *, main_changes=None, nc_changes=None):
    main = {**body("REFUSE", lock=lock), "head_commit": "a" * 40, "prereg_sha256": "p" * 64, "generated_at_utc": "t", "error": None}
    negative = copy.deepcopy(main)
    negative.update(mode="negative_control")
    negative["challenger"]["replaced_by"] = "constant_train_prevalence"
    main.update(main_changes or {})
    negative.update(nc_changes or {})
    out = repo / "data" / "gate" / "main_200k"
    out.mkdir(parents=True, exist_ok=True)
    (out / "gate_report.json").write_text(json.dumps(main))
    (out / "negative_control_report.json").write_text(json.dumps(negative))


def test_phase_b_appends_and_copies_the_reports(repo):
    lock = _lock_repo(repo)
    before = (repo / "prereg.yaml").read_bytes()
    _write_reports(repo, lock)
    _, code = _append(repo, ["phase-b"])
    after = (repo / "prereg.yaml").read_bytes()
    assert code == 0 and after.startswith(before)
    phase_b = parse_prereg(after.decode()).phase_b
    assert phase_b["decision"] == "REFUSE" and phase_b["negative_control_decision"] == "REFUSE"
    assert phase_b["power"] == {"n_test": 31659, "n_test_positive": 616, "min_test_positives": 400}
    assert phase_b["frozen_v1"]["logistic_regression"]["observed_expected_ratio"] == [1.05, 0.95, None]
    for name, path in PHASE_B_REPORT_PATHS.items():
        copied = (repo / path).read_bytes()
        source = repo / "data" / "gate" / "main_200k" / ("gate_report.json" if name == "gate" else "negative_control_report.json")
        assert copied == source.read_bytes() and hashlib.sha256(copied).hexdigest() == phase_b["reports"][name]["sha256"]


@pytest.mark.parametrize(
    ("main_changes", "nc_changes"),
    [
        ({"rehearsal": True}, None),
        ({"error": "RuntimeError: x"}, None),
        ({"analysis_lock": None}, None),
        (None, {"gate": {**body()["gate"], "decision": "PROMOTE"}}),
        (None, {"head_commit": "b" * 40}),
    ],
)
def test_phase_b_refusals(repo, main_changes, nc_changes):
    lock = _lock_repo(repo)
    before = (repo / "prereg.yaml").read_bytes()
    _write_reports(repo, lock, main_changes=main_changes, nc_changes=nc_changes)
    _, code = _append(repo, ["phase-b"])
    assert code == 2 and (repo / "prereg.yaml").read_bytes() == before


def test_failed_phase_b_leaves_no_copied_reports(repo):
    lock = _lock_repo(repo)
    bad_gate = {**body()["gate"], "rules": body()["gate"]["rules"][::-1]}  # the appended block would not parse
    _write_reports(repo, lock, main_changes={"gate": bad_gate})
    _, code = _append(repo, ["phase-b"])
    assert code == 2 and not any((repo / path).exists() for path in PHASE_B_REPORT_PATHS.values())


def test_failed_replace_removes_the_copies_and_the_temporary_file(repo, monkeypatch):
    lock = _lock_repo(repo)
    before = (repo / "prereg.yaml").read_bytes()
    _write_reports(repo, lock)
    cli = _load("prereg_append")

    def fail(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(cli.os, "replace", fail)
    code = cli.main(["phase-b"], repo_root=repo, run_git=fake_git(head="2" * 40))
    assert code == 2 and (repo / "prereg.yaml").read_bytes() == before
    assert not (repo / "prereg.yaml.tmp").exists()
    assert not any((repo / path).exists() for path in PHASE_B_REPORT_PATHS.values())


def test_prereg_changed_while_appending_is_refused(repo, monkeypatch):
    lock = _lock_repo(repo)
    _write_reports(repo, lock)
    cli = _load("prereg_append")
    real = cli._validate

    def validate_then_edit(old, addition):
        new = real(old, addition)
        (repo / "prereg.yaml").write_bytes(old + b"# concurrent edit\n")
        return new

    monkeypatch.setattr(cli, "_validate", validate_then_edit)
    assert cli.main(["phase-b"], repo_root=repo, run_git=fake_git(head="2" * 40)) == 2
    assert (repo / "prereg.yaml").read_bytes().endswith(b"# concurrent edit\n")
    assert not any((repo / path).exists() for path in PHASE_B_REPORT_PATHS.values())


@pytest.mark.parametrize("broken", [b'{"run": "main_200k", "mode": "ma', b"[1, 2]"])
def test_phase_b_refuses_an_unparsable_report(repo, broken, capsys):
    lock = _lock_repo(repo)
    _write_reports(repo, lock)
    (repo / "data" / "gate" / "main_200k" / "gate_report.json").write_bytes(broken)
    _, code = _append(repo, ["phase-b"])
    assert code == 2 and "ERROR:" in capsys.readouterr().err


@pytest.mark.parametrize("damage", ["no_gate", "models_as_list"])
def test_phase_b_refuses_a_report_missing_its_gate(repo, capsys, damage):
    lock = _lock_repo(repo)
    before = (repo / "prereg.yaml").read_bytes()
    _write_reports(repo, lock)
    path = repo / "data" / "gate" / "main_200k" / "gate_report.json"
    report = json.loads(path.read_text())
    if damage == "no_gate":
        del report["gate"]
    else:
        report["frozen_v1"]["models"] = []
    path.write_text(json.dumps(report))
    _, code = _append(repo, ["phase-b"])
    assert code == 2 and "incomplete or malformed" in capsys.readouterr().err
    assert not any((repo / p).exists() for p in PHASE_B_REPORT_PATHS.values())
    assert (repo / "prereg.yaml").read_bytes() == before


def test_gate_refuses_to_overwrite_a_non_object_report(repo):
    path = repo / "data" / "gate" / "main_200k" / "gate_report.json"
    path.parent.mkdir(parents=True)
    path.write_text("[1, 2]")
    assert _gate(repo, result=body()) == 2 and path.read_text() == "[1, 2]"


def test_phase_b_needs_a_lock(repo):
    _write_reports(repo, None)
    _, code = _append(repo, ["phase-b"])
    assert code == 2


def test_phase_b_yaml_round_trips_none_and_floats(repo):
    lock = _lock_repo(repo)
    _write_reports(repo, lock)
    _append(repo, ["phase-b"])
    doc = yaml.safe_load((repo / "prereg.yaml").read_text())
    assert doc["phase_b"]["rules"][2]["value"] is None and doc["phase_b"]["rules"][0]["value"] == 0.0123
