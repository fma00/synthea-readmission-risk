"""ops/gate_run.py (v2 slice 6b): the promotion gate's verification steps against a real local MLflow store. One module fixture
trains three experiments on disjoint synthetic golds -- `frozen-test` on gold A (standing in for v1) and `challenger-test` on gold
B (standing in for main_200k, sized for >= 400 test positives so the real power floor applies) -- exports both LR bundles, and
writes a champion pointer to the frozen LR bundle. git is a fake. The prereg is the real Phase A plus a lock; the tests run the
two bootstrap-based evaluations with 50 resamples (the schema requires >= 1000 in the file) by wrapping them."""

from __future__ import annotations

import copy
import dataclasses
import json
import shutil
import subprocess
import warnings
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import pytest
import yaml

from readmission_risk.bundles.champion import champion_json
from readmission_risk.models.bundle_export import export_bundle
from readmission_risk.models.tracking import mlflow_tracking_uri, resolve_run_id
from readmission_risk.models.training import TrainingConfig, train_and_evaluate
from readmission_risk.ops import gate_run as gr
from readmission_risk.ops.prereg import parse_prereg
from tests.gold_helpers import write_gold_metadata_for_frame
from tests.models.helpers import make_gold_frame
from tests.ops.conftest import REPO

PHASE_A_COMMIT = "1" * 40
CODE_COMMIT = "2" * 40
LOCK_COMMIT = "3" * 40
REFERENCE = "20261012"
PHASE_A_TEXT = (REPO / "prereg.yaml").read_text(encoding="utf-8")
TEST_RESAMPLES = 50
LOCK = {
    "locked_on": "2026-10-17",
    "challenger_model": "logistic_regression",
    "experiment_name": "challenger-test",
    "frozen_v1_rows": "modeling_rows",
    "negative_control": "constant_train_prevalence",
    "code_commit": CODE_COMMIT,
}
PREREG_TEXT = PHASE_A_TEXT + "\n" + yaml.safe_dump({"analysis_lock": LOCK}, sort_keys=False, default_flow_style=False)


def _write_gold(directory: Path, df) -> Path:
    directory.mkdir(parents=True)
    df.to_parquet(directory / "part-00000.parquet", index=False)
    summary = {"reference_date": REFERENCE, "provenance": {"prereg_commit": PHASE_A_COMMIT}}
    write_gold_metadata_for_frame(directory, df, version=2, reference_date=REFERENCE, generation_summary=summary, n_planned_stays=3)
    return directory


def _train(gold: Path, tracking: Path, experiment: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        train_and_evaluate(TrainingConfig(gold_dir=gold, reference_date=REFERENCE, test_start_date="20230101",
                                          tracking_dir=tracking, experiment_name=experiment, n_bootstrap=0))  # fmt: skip


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    from mlflow.tracking import MlflowClient

    root = tmp_path_factory.mktemp("gate")
    tracking = root / "mlflow"
    gold_a = _write_gold(root / "gold_a", make_gold_frame(1200, seed=1))
    frame_b = make_gold_frame(2400, seed=2)
    frame_b["patient_id"] = "b-" + frame_b["patient_id"]
    gold_b = _write_gold(root / "gold_b", frame_b)
    frame_c = make_gold_frame(300, seed=3)
    frame_c["patient_id"] = "c-" + frame_c["patient_id"]
    gold_c = _write_gold(root / "gold_c", frame_c)
    _train(gold_a, tracking, "frozen-test")
    _train(gold_b, tracking, "challenger-test")
    _train(gold_c, tracking, "small-test")
    client = MlflowClient(tracking_uri=mlflow_tracking_uri(tracking))
    run_ids = {(e, n): resolve_run_id(tracking, e, n) for e in ("frozen-test", "challenger-test", "small-test")
               for n in ("logistic_regression", "xgboost_calibrated")}  # fmt: skip
    for (experiment, _), run_id in run_ids.items():
        # set explicitly so the store does not depend on whether the test checkout is clean (CI) or not (a dev tree)
        client.set_tag(run_id, "git_dirty", "clean")
        if experiment != "frozen-test":
            client.set_tag(run_id, "mlflow.source.git.commit", CODE_COMMIT)
    bundles = root / "bundles"
    frozen = export_bundle(tracking, resolve_run_id(tracking, "frozen-test", "logistic_regression"), bundles)
    challenger = export_bundle(tracking, resolve_run_id(tracking, "challenger-test", "logistic_regression"), bundles)
    small = export_bundle(tracking, resolve_run_id(tracking, "small-test", "logistic_regression"), bundles)
    (root / "deploy").mkdir()
    (root / "deploy" / "champion.json").write_text(champion_json(f"gs://fake-bundles/{frozen.bundle_id}", frozen.manifest_sha256))
    first_start = min(gr._read_run(tracking, resolve_run_id(tracking, e, "logistic_regression")).start_time
                      for e in ("challenger-test", "small-test"))  # fmt: skip
    return {"run_ids": run_ids, "root": root, "tracking": tracking, "gold_a": gold_a, "gold_b": gold_b, "gold_c": gold_c, "bundles": bundles,
            "challenger": challenger, "small": small, "lock_time": first_start // 1000 - 100}  # fmt: skip


def fake_git(store, *, diff_rc=0, on_remote=True, phase_a_text=PHASE_A_TEXT, lock_time=None, locked_text=PREREG_TEXT,
             later_commits=b"", is_ancestor=True):
    def run_git(argv, **kwargs):
        args = argv[3:]
        if args[:2] == ["diff", "--quiet"]:
            return subprocess.CompletedProcess(argv, diff_rc, b"", b"")
        if args == ["show", "--end-of-options", f"{LOCK_COMMIT}:prereg.yaml"]:
            return subprocess.CompletedProcess(argv, 0, locked_text.encode(), b"")
        if args == ["log", "--format=%H", f"{LOCK_COMMIT}..HEAD", "--", "prereg.yaml"]:
            return subprocess.CompletedProcess(argv, 0, later_commits, b"")
        if args[:3] == ["merge-base", "--is-ancestor", LOCK_COMMIT]:
            return subprocess.CompletedProcess(argv, 0 if is_ancestor else 1, b"", b"")
        if args[:1] == ["log"]:
            t = store["lock_time"] if lock_time is None else lock_time
            return subprocess.CompletedProcess(argv, 0, f"{LOCK_COMMIT} {t}\n".encode(), b"")
        if args == ["fetch", "origin"]:
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if args == ["branch", "-r", "--contains", LOCK_COMMIT]:
            return subprocess.CompletedProcess(argv, 0, b"  origin/v2\n" if on_remote else b"", b"")
        if args == ["show", "--end-of-options", f"{PHASE_A_COMMIT}:prereg.yaml"]:
            return subprocess.CompletedProcess(argv, 0, phase_a_text.encode(), b"")
        return subprocess.CompletedProcess(argv, 128, b"", b"fatal: unexpected")

    return run_git


def config(store, **overrides):
    values = {
        "run": "main_200k", "challenger_experiment": "challenger-test",
        "challenger_bundle": store["bundles"] / store["challenger"].bundle_id, "repo_root": store["root"],
        "champion_path": Path("deploy/champion.json"), "bundles_root": Path("bundles"), "frozen_experiment": "frozen-test",
        "tracking_dir": Path("mlflow"), "v1_gold_dir": Path("gold_a"),
    }  # fmt: skip
    values.update(overrides)
    return gr.GateRunConfig(**values)


def _fewer_resamples(fn):
    def wrapped(*args, **kwargs):
        return fn(*args, **{**kwargs, "n_resamples": TEST_RESAMPLES})

    return wrapped


def run(store, *, prereg_text=PREREG_TEXT, gold=None, git=None, **overrides):
    with warnings.catch_warnings(), pytest.MonkeyPatch.context() as mp:
        warnings.simplefilter("ignore")
        mp.setattr(gr, "evaluate_gate", _fewer_resamples(gr.evaluate_gate))
        mp.setattr(gr, "evaluate_frozen_v1", _fewer_resamples(gr.evaluate_frozen_v1))
        return gr.run_gate(config(store, **overrides), prereg=parse_prereg(prereg_text),
                           run_git=git or fake_git(store), gold_opener=lambda uri: nullcontext(gold or store["gold_b"]))  # fmt: skip


@pytest.fixture(scope="module")
def report(store):
    return run(store)


# --- the happy path -------------------------------------------------------------------------------------------------------


def test_happy_path_report(report, store):
    assert report["run"] == "main_200k" and report["mode"] == "main" and report["rehearsal"] is False
    assert report["analysis_lock"] == LOCK and report["phase_a_commit"] == PHASE_A_COMMIT
    assert report["lock_time_utc"].endswith("Z")
    assert report["power"]["n_test_positive"] >= 400 and report["power"]["min_test_positives"] == 400
    assert report["challenger"]["bundle_id"] == store["challenger"].bundle_id and report["challenger"]["replaced_by"] is None
    assert report["challenger"]["git_commit"] == CODE_COMMIT
    assert report["runs_on_this_gold"] == {"challenger-test": {"active": 2, "deleted": 0}}
    gate = report["gate"]
    assert gate["decision"] in ("PROMOTE", "REFUSE") and [r["name"] for r in gate["rules"]] == list(gr_rule_names())
    assert gate["n_resamples_used"] + gate["n_resamples_skipped"] == TEST_RESAMPLES
    frozen = report["frozen_v1"]
    assert frozen["rows"] == "modeling_rows" and list(frozen["models"]) == ["logistic_regression", "xgboost_calibrated"]
    assert set(frozen["models"]["logistic_regression"]["point"]) == {"roc_auc", "brier_score", "observed_expected_ratio"}
    assert all(isinstance(v, int) for v in report["split_summary"].values())
    json.dumps(report, allow_nan=False)  # strict JSON: every NaN was mapped to null


def gr_rule_names():
    from readmission_risk.models.gate import RULE_NAMES

    return RULE_NAMES


def test_rerun_is_deterministic(report, store):
    again = run(store)
    assert again["gate"] == report["gate"] and again["frozen_v1"] == report["frozen_v1"]


def test_negative_control_is_refused(store):
    result = run(store, negative_control=True)
    assert result["mode"] == "negative_control" and result["gate"]["decision"] == "REFUSE"
    assert result["challenger"]["replaced_by"] == "constant_train_prevalence"
    assert not result["gate"]["rules"][0]["passed"]


def test_rehearsal_ignores_the_lock(store):
    result = run(store, run="pilot_20k", rehearsal=True)
    assert result["rehearsal"] is True and result["analysis_lock"] is None and result["lock_time_utc"] is None
    assert result["runs_on_this_gold"] is None


# --- each verification step refusing ---------------------------------------------------------------------------------------


def _refused(store, match, **kwargs):
    with pytest.raises(gr.GateRunError, match=match):
        run(store, **kwargs)


def test_mode_rules(store):
    _refused(store, "only for pilot_20k", rehearsal=True)
    _refused(store, "runs only on main_200k", run="pilot_20k")


def test_missing_or_mismatched_lock(store):
    _refused(store, "no analysis_lock", prereg_text=PHASE_A_TEXT)
    other = PHASE_A_TEXT + "\n" + yaml.safe_dump({"analysis_lock": {**LOCK, "experiment_name": "other"}}, sort_keys=False)
    _refused(store, "experiment_name", prereg_text=other)


def test_code_at_head_differs_from_the_lock(store):
    _refused(store, "differs from the locked code_commit", git=fake_git(store, diff_rc=1))


def test_lock_edited_after_the_lock_commit(store):
    edited = PHASE_A_TEXT + "\n" + yaml.safe_dump({"analysis_lock": {**LOCK, "code_commit": "5" * 40}}, sort_keys=False)
    _refused(store, "the lock was edited", git=fake_git(store, locked_text=edited))


def test_prereg_changed_after_the_lock_commit(store):
    _refused(store, "changed after the lock commit", git=fake_git(store, later_commits=b"6" * 40 + b"\n"))


def test_run_commit_must_contain_the_lock_commit(store):
    _refused(store, "does not contain the lock commit", git=fake_git(store, is_ancestor=False))


def test_lock_commit_not_on_a_remote(store):
    _refused(store, "not on any remote branch", git=fake_git(store, on_remote=False))


def test_runs_started_before_the_lock(store):
    _refused(store, "before the analysis lock", git=fake_git(store, lock_time=4_000_000_000))


def test_phase_a_changed_since_generation(store):
    changed = PHASE_A_TEXT.replace("margin_fraction_of_champion_brier: 0.05", "margin_fraction_of_champion_brier: 0.1")
    assert changed != PHASE_A_TEXT
    _refused(store, "Phase A changed", git=fake_git(store, phase_a_text=changed))


def test_changed_deviations_are_refused(store):
    entry = {"date": "2026-10-13", "commit": "4" * 40, "field": "power.min_test_positives", "old": 400, "new": 400, "reason": "x"}
    doc = yaml.safe_load(PHASE_A_TEXT)
    doc["deviations"] = [entry]
    _refused(store, "Phase A changed", git=fake_git(store, phase_a_text=yaml.safe_dump(doc, sort_keys=False)))


def test_p1b_failure(store, monkeypatch):
    from readmission_risk.ops.prereg import PreregError

    def boom(prereg):
        raise PreregError("training.seed differs")

    monkeypatch.setattr(gr, "check_prereg_against_code", boom)
    _refused(store, "P1b")


def test_power_floor(store):
    small_text = PREREG_TEXT.replace("experiment_name: challenger-test", "experiment_name: small-test")
    _refused(store, "min_test_positives", gold=store["gold_c"], challenger_experiment="small-test", prereg_text=small_text,
             git=fake_git(store, locked_text=small_text), challenger_bundle=store["bundles"] / store["small"].bundle_id)  # fmt: skip


def test_metadata_must_match_prereg(store, tmp_path):
    gold = tmp_path / "gold"
    shutil.copytree(store["gold_b"], gold)
    meta = json.loads((gold / "_gold_metadata.json").read_text())
    meta["lookback_years"] = 2
    (gold / "_gold_metadata.json").write_text(json.dumps(meta))
    _refused(store, "lookback_years", gold=gold)


def _patched_read(monkeypatch, store, *, params=None, tags=None, metrics=None):
    """Alters what the gate reads for the challenger LR run only (selected by run id, not by tags)."""
    original = gr._read_run
    target = store["run_ids"][("challenger-test", "logistic_regression")]

    def read(tracking_dir, run_id):
        record = original(tracking_dir, run_id)
        if run_id == target:
            return dataclasses.replace(
                record,
                params={**record.params, **(params or {})},
                tags={**record.tags, **(tags or {})},
                metrics={**record.metrics, **(metrics or {})},
            )
        return record

    monkeypatch.setattr(gr, "_read_run", read)


def test_seed_param_must_match_prereg(store, monkeypatch):
    _patched_read(monkeypatch, store, params={"seed": "7"})
    _refused(store, "param seed")


def test_dirty_tag_is_refused(store, monkeypatch):
    _patched_read(monkeypatch, store, tags={"git_dirty": "dirty"})
    _refused(store, "git_dirty is 'dirty'")


def test_python_tag_mismatch(store, monkeypatch):
    _patched_read(monkeypatch, store, tags={"python_version": "3.12.11"})
    _refused(store, "guard 14")


def test_library_tag_mismatch(store, monkeypatch):
    _patched_read(monkeypatch, store, tags={"sklearn_version": "0.0.1"})
    _refused(store, "sklearn_version")


def test_reproduction_mismatch(store, monkeypatch):
    _patched_read(monkeypatch, store, metrics={"test_roc_auc": 0.123})
    _refused(store, "does not reproduce")


def test_other_gold_fingerprint(store, monkeypatch):
    from readmission_risk.models.constants import ROWHASH_TAG

    _patched_read(monkeypatch, store, tags={ROWHASH_TAG: "f" * 64})
    _refused(store, "trained on gold")


def test_another_experiment_on_this_gold(store, monkeypatch):
    real = gr.runs_on_gold

    def with_extra(tracking_dir, fingerprint):
        return [*real(tracking_dir, fingerprint), {"experiment": "peek", "experiment_stage": "deleted", "run_id": "9" * 32,
                                                   "run_stage": "deleted", "start_time": 0}]  # fmt: skip

    monkeypatch.setattr(gr, "runs_on_gold", with_extra)
    _refused(store, "in experiment 'peek'")


def test_deleted_runs_are_seen(store, tmp_path):
    """runs_on_gold sees deleted runs and deleted experiments (ViewType.ALL), so deleting a peek does not hide it."""
    from mlflow.tracking import MlflowClient

    tracking = tmp_path / "mlflow"
    shutil.copytree(store["tracking"], tracking)
    client = MlflowClient(tracking_uri=mlflow_tracking_uri(tracking))
    run_id = resolve_run_id(tracking, "challenger-test", "xgboost_calibrated")
    fingerprint = gr._read_run(tracking, run_id).tags[__import__("readmission_risk.models.constants", fromlist=["x"]).ROWHASH_TAG]
    client.delete_run(run_id)
    client.delete_experiment(client.get_experiment_by_name("frozen-test").experiment_id)
    stages = {e["run_id"]: e["run_stage"] for e in gr.runs_on_gold(tracking, fingerprint)}
    assert stages[run_id] == "deleted"


def test_champion_pin_and_run(store, tmp_path, monkeypatch):
    root = tmp_path / "root"
    shutil.copytree(store["root"], root, ignore=shutil.ignore_patterns("gold_*"))
    champion = json.loads((root / "deploy" / "champion.json").read_text())
    (root / "deploy" / "champion.json").write_text(json.dumps({**champion, "sha256": "0" * 64}))
    _refused(store, "does not hash", repo_root=root, v1_gold_dir=store["gold_a"],
             challenger_bundle=root / "bundles" / store["challenger"].bundle_id)  # fmt: skip


def test_tampered_bundle_model(store, tmp_path):
    bundles = tmp_path / "bundles"
    shutil.copytree(store["bundles"], bundles)
    target = bundles / store["challenger"].bundle_id / "model.pkl"
    target.chmod(0o644)
    target.write_bytes(target.read_bytes() + b"x")
    _refused(store, "does not match its manifest entry", challenger_bundle=bundles / store["challenger"].bundle_id)


def test_inconsistent_version_warning_on_load_is_refused(store, monkeypatch):
    from sklearn.exceptions import InconsistentVersionWarning

    def warn(data):
        warnings.warn(InconsistentVersionWarning(estimator_name="x", current_sklearn_version="1", original_sklearn_version="0"), stacklevel=1)

    monkeypatch.setattr(gr.cloudpickle, "loads", warn)
    _refused(store, "load")


def test_tampered_split_assignment(store, tmp_path):
    from readmission_risk.bundles.manifest import (
        compute_bundle_id,
        manifest_bytes,
        sha256_hex,
    )

    source = store["bundles"] / store["challenger"].bundle_id
    manifest = json.loads((source / "manifest.json").read_text())
    data = (source / "split_assignment.csv").read_bytes().replace(b",test\n", b",train\n", 1)
    manifest = copy.deepcopy(manifest)
    manifest["files"]["split_assignment.csv"].update(sha256=sha256_hex(data), size=len(data))
    manifest["bundle_id"] = compute_bundle_id(manifest)
    target = tmp_path / manifest["bundle_id"]
    target.mkdir()
    (target / "manifest.json").write_bytes(manifest_bytes(manifest))
    (target / "model.pkl").write_bytes((source / "model.pkl").read_bytes())
    (target / "split_assignment.csv").write_bytes(data)
    _refused(store, "partition differs", challenger_bundle=target)


def test_frozen_reproduction_on_v1_gold(store, monkeypatch):
    original = gr._read_run
    target = store["run_ids"][("frozen-test", "xgboost_calibrated")]

    def read(tracking_dir, run_id):
        record = original(tracking_dir, run_id)
        if run_id == target:
            return dataclasses.replace(record, metrics={**record.metrics, "test_brier_score": 0.5})
        return record

    monkeypatch.setattr(gr, "_read_run", read)
    _refused(store, "frozen xgboost_calibrated")


def test_missing_v1_gold(store):
    _refused(store, "no v1 gold", v1_gold_dir=Path("nowhere"))


def test_verify_bundle_dir_returns_the_verified_bytes(store):
    manifest, model, assignment = gr.verify_bundle_dir(store["bundles"] / store["challenger"].bundle_id)
    assert manifest["bundle_id"] == store["challenger"].bundle_id and model and assignment.startswith(b"encounter_id,partition")


def test_sanitize_maps_nan_and_numpy():
    out = gr._sanitize({"a": (np.float64("nan"), np.int64(3)), "b": np.bool_(True)})
    assert out == {"a": [None, 3], "b": True}
