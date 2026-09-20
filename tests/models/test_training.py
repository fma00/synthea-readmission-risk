from __future__ import annotations

import dataclasses
import json
import subprocess
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient
from sklearn.linear_model import LogisticRegression

import mlflow
from readmission_risk.models import training as training_module
from readmission_risk.models.data import (
    FEATURE_COLUMNS,
    gold_fingerprint,
    load_gold_table,
    prepare_features,
)
from readmission_risk.models.evaluate import BOOTSTRAP_METRICS
from readmission_risk.models.split import chronological_group_split
from readmission_risk.models.tracking import load_logged_model, mlflow_tracking_uri
from readmission_risk.models.training import (
    MODEL_NAMES,
    TrainingConfig,
    train_and_evaluate,
)
from readmission_risk.pipeline.gold_metadata import LABEL_DEFINITION
from tests.models.helpers import (
    REFERENCE_DATE,
    REFERENCE_DATE_STR,
    TEST_START,
    TEST_START_STR,
    make_gold_frame,
    write_test_gold_metadata,
)

EXPERIMENT = "readmission-risk"


def _write_gold(directory: Path, df: pd.DataFrame, **metadata_overrides) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    half = len(df) // 2
    df.iloc[:half].to_parquet(directory / "part-00000.parquet", index=False)
    df.iloc[half:].to_parquet(directory / "part-00001.parquet", index=False)
    (directory / "_SUCCESS").write_text("")
    write_test_gold_metadata(directory, df, **metadata_overrides)
    return directory


def _config(gold_dir: Path, tracking_dir: Path, **overrides) -> TrainingConfig:
    base = {
        "gold_dir": gold_dir,
        "reference_date": REFERENCE_DATE_STR,
        "test_start_date": TEST_START_STR,
        "tracking_dir": tracking_dir,
        "n_bootstrap": 50,
    }
    base.update(overrides)
    return TrainingConfig(**base)


def _autolog_is_off(uri: str) -> None:
    """A plain scikit-learn fit creates NO run and no active run iff autolog is disabled. The baseline run count
    is taken NOW (after whatever the test just did), so a FAILED run left behind by a failing call is not 'new'."""
    mlflow.set_tracking_uri(uri)
    client = MlflowClient(tracking_uri=uri)
    exp = client.get_experiment_by_name(EXPERIMENT)
    before = len(client.search_runs([exp.experiment_id]))
    LogisticRegression().fit(np.array([[0.0], [1.0], [2.0], [3.0]]), np.array([0, 0, 1, 1]))
    assert mlflow.active_run() is None
    assert len(client.search_runs([exp.experiment_id])) == before


@pytest.fixture(scope="module")
def e2e(tmp_path_factory):
    """One expensive end-to-end run reused by the assertions below. Yield-based: the pre-fixture tracking URI is
    restored (and autolog disabled) after the last test in this module, so nothing leaks into other modules."""
    original_uri = mlflow.get_tracking_uri()
    root = tmp_path_factory.mktemp("e2e")
    df = make_gold_frame(600, seed=0)
    gold_dir = _write_gold(root / "gold", df)
    tracking_dir = root / "tracking"
    result = train_and_evaluate(_config(gold_dir, tracking_dir))
    uri_after_call = mlflow.get_tracking_uri()
    yield {
        "result": result,
        "tracking_dir": tracking_dir,
        "gold_dir": gold_dir,
        "uri_after_call": uri_after_call,
        "client": MlflowClient(tracking_uri=mlflow_tracking_uri(tracking_dir)),
    }
    mlflow.autolog(disable=True)
    mlflow.set_tracking_uri(original_uri)


def _runs(e2e):
    client = e2e["client"]
    exp = client.get_experiment_by_name(EXPERIMENT)
    return exp, client.search_runs([exp.experiment_id])


def test_end_to_end_two_runs_and_metrics_logged(e2e):
    client = e2e["client"]
    _, runs = _runs(e2e)
    assert len(runs) == 2  # exactly two top-level runs (extra artifacts/params autolog adds are tolerated)
    assert all(r.data.tags.get("mlflow.parentRunId") is None for r in runs)  # no nested per-fold runs
    by_name = {r.data.tags["model_name"]: r for r in runs}
    assert set(by_name) == set(MODEL_NAMES)

    expected_artifacts = {
        "calibration_curve.json",
        "split_summary.json",
        "feature_columns.json",
        "split_assignment.csv",
        "reliability_diagram.png",
        "bootstrap_summary.json",
    }
    for name, run in by_name.items():
        metrics, params, tags = run.data.metrics, run.data.params, run.data.tags
        for key in ("test_roc_auc", "test_pr_auc", "test_brier_score", "test_expected_calibration_error",
                    "test_calibration_gap", "test_abs_calibration_gap"):
            assert key in metrics, (name, key)
        for m in BOOTSTRAP_METRICS:
            assert f"test_{m}_ci_lower" in metrics and f"test_{m}_ci_upper" in metrics, (name, m)
        assert {"seed", "test_start_date", "split_n_train"} <= set(params)
        for tag in ("gold_fingerprint", "model_uri", "sklearn_version", "xgboost_version", "mlflow_version",
                    "pandas_version", "numpy_version"):
            assert tags.get(tag), (name, tag)
        assert tags["git_dirty"] in {"clean", "dirty", "unknown"}
        assert expected_artifacts <= {a.path for a in client.list_artifacts(run.info.run_id)}

    challenger = by_name["xgboost_calibrated"].data.metrics
    assert "test_diff_vs_logistic_regression_roc_auc" in challenger
    assert "test_diff_vs_logistic_regression_roc_auc_ci_lower" in challenger
    assert "test_diff_vs_logistic_regression_roc_auc_ci_upper" in challenger
    assert "calibration_method" in by_name["xgboost_calibrated"].data.params
    assert "calibration_method" not in by_name["logistic_regression"].data.params


def test_bootstrap_status_tag_marks_completed_runs(e2e):
    _, runs = _runs(e2e)
    assert {r.data.tags["bootstrap_status"] for r in runs} == {"complete"}


def test_model_is_fitted_on_training_rows_only(e2e):
    """Guards train_and_evaluate ITSELF (not bare sklearn behaviour): the logged pipelines' fitted statistics must
    come from the training partition alone, so e.g. fitting on train+test would fail here."""
    df = load_gold_table(e2e["gold_dir"])
    split = chronological_group_split(df, reference_date=REFERENCE_DATE, test_start_date=TEST_START)
    model = load_logged_model(e2e["tracking_dir"], e2e["result"].models["logistic_regression"].run_id)
    age_scaler = model.named_steps["preprocess"].named_transformers_["age"]
    assert age_scaler.n_samples_seen_ == len(split.train)
    np.testing.assert_allclose(age_scaler.mean_, [split.train["age_at_index_years"].mean()])
    all_rows_mean = df["age_at_index_years"].mean()
    assert not np.isclose(age_scaler.mean_[0], all_rows_mean, rtol=0, atol=1e-9)


def test_train_start_date_flows_through_end_to_end(tmp_path):
    gold = _write_gold(tmp_path / "gold", make_gold_frame(600, seed=0))
    result = train_and_evaluate(_config(gold, tmp_path / "t", train_start_date="20200101", n_bootstrap=0))
    assert result.split_summary["n_dropped_before_train_start"] > 0
    client = MlflowClient(tracking_uri=mlflow_tracking_uri(tmp_path / "t"))
    run = client.get_run(result.models["logistic_regression"].run_id)
    assert run.data.params["train_start_date"] == "20200101"
    assert int(run.data.params["split_n_dropped_before_train_start"]) == result.split_summary[
        "n_dropped_before_train_start"
    ]


def test_phase_b_failure_leaves_runs_marked_pending(tmp_path, monkeypatch):
    gold = _write_gold(tmp_path / "gold", make_gold_frame(300, seed=1))
    tracking_dir = tmp_path / "t"

    def boom(*args, **kwargs):
        raise RuntimeError("bootstrap failed")

    monkeypatch.setattr(training_module, "clustered_bootstrap", boom)
    with pytest.raises(RuntimeError, match="bootstrap failed"):
        train_and_evaluate(_config(gold, tracking_dir))
    client = MlflowClient(tracking_uri=mlflow_tracking_uri(tracking_dir))
    runs = client.search_runs([client.get_experiment_by_name(EXPERIMENT).experiment_id])
    assert len(runs) == 2
    # distinguishable from a deliberate --n-bootstrap 0 run ("skipped")
    assert {r.data.tags["bootstrap_status"] for r in runs} == {"pending"}
    assert not any("test_roc_auc_ci_lower" in r.data.metrics for r in runs)


def test_git_dirty_state_runs_in_the_modules_own_directory(monkeypatch):
    """Regression: git must run where the CODE lives, not in the process cwd (MLflow's own commit tag comes from the
    script's path, so the two must describe the same repository)."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout=fake_run.stdout, stderr="")

    monkeypatch.setattr(training_module.subprocess, "run", fake_run)
    fake_run.stdout = ""
    assert training_module._git_dirty_state() == "clean"
    fake_run.stdout = " M some/file.py\n"
    assert training_module._git_dirty_state() == "dirty"
    assert all(c["cwd"] == Path(training_module.__file__).resolve().parent for c in calls)

    monkeypatch.setattr(training_module.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 128, "", "not a repo"))
    assert training_module._git_dirty_state() == "unknown"

    def missing_git(cmd, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(training_module.subprocess, "run", missing_git)
    assert training_module._git_dirty_state() == "unknown"


def test_tracking_uri_is_explicit_sqlite(e2e):
    tracking_dir = e2e["tracking_dir"]
    expected = mlflow_tracking_uri(tracking_dir)
    assert e2e["uri_after_call"] == expected
    assert expected == f"sqlite:///{tracking_dir.resolve()}/mlflow.db"
    assert (tracking_dir / "mlflow.db").is_file()
    exp, _ = _runs(e2e)
    assert exp.artifact_location == (tracking_dir.resolve() / "artifacts").as_uri()


def test_mlflow_tracking_uri_helper_forms(tmp_path, monkeypatch):
    absolute = tmp_path / "store"
    assert mlflow_tracking_uri(absolute) == f"sqlite:///{absolute.resolve()}/mlflow.db"
    assert mlflow_tracking_uri(absolute).startswith("sqlite:////")  # four slashes: absolute path
    monkeypatch.chdir(tmp_path)
    assert mlflow_tracking_uri(Path("rel")) == f"sqlite:///{(tmp_path / 'rel').resolve()}/mlflow.db"


def test_gold_fingerprint_tag_matches_helper(e2e):
    _, runs = _runs(e2e)
    expected = gold_fingerprint(load_gold_table(e2e["gold_dir"]))
    assert {r.data.tags["gold_fingerprint"] for r in runs} == {expected}


def test_logged_model_roundtrips_via_load_logged_model(e2e, tmp_path):
    result = e2e["result"]
    df = load_gold_table(e2e["gold_dir"])
    split = chronological_group_split(df, reference_date=REFERENCE_DATE, test_start_date=TEST_START)
    X_test = prepare_features(split.test)

    for name in MODEL_NAMES:
        mr = result.models[name]
        # simulate slice 4's fresh process: point MLflow at an UNRELATED store first
        mlflow.set_tracking_uri(f"sqlite:///{tmp_path}/unrelated.db")
        with pytest.raises(MlflowException):  # the LoggedModel URI only resolves against the store that produced it
            mlflow.sklearn.load_model(mr.model_uri)
        model = load_logged_model(e2e["tracking_dir"], mr.run_id)
        proba = model.predict_proba(X_test)[:, 1]
        info = mlflow.models.get_model_info(mr.model_uri)
        assert [c.name for c in info.signature.inputs] == list(FEATURE_COLUMNS)
        assert proba.shape == (len(X_test),) and ((proba >= 0) & (proba <= 1)).all()
        logged_auc = e2e["client"].get_run(mr.run_id).data.metrics["test_roc_auc"]
        from sklearn.metrics import roc_auc_score

        assert roc_auc_score(split.test["is_readmitted"], proba) == pytest.approx(logged_auc, abs=1e-12)


def test_load_logged_model_missing_tag_raises_value_error(tmp_path):
    # its own store: creating a bare run inside the shared end-to-end experiment would pollute the other tests
    store = tmp_path / "store"
    store.mkdir()
    client = MlflowClient(tracking_uri=mlflow_tracking_uri(store))
    bare = client.create_run(client.create_experiment("bare"))
    with pytest.raises(ValueError, match="model_uri"):
        load_logged_model(store, bare.info.run_id)


def test_split_assignment_matches_split_summary(e2e, tmp_path):
    client = e2e["client"]
    _, runs = _runs(e2e)
    run = runs[0]
    local = client.download_artifacts(run.info.run_id, "split_assignment.csv", str(tmp_path))
    assignment = pd.read_csv(local)
    summary = e2e["result"].split_summary
    assert (assignment["partition"] == "train").sum() == summary["n_train"]
    assert (assignment["partition"] == "test").sum() == summary["n_test"]
    assert assignment["encounter_id"].is_unique
    summary_local = client.download_artifacts(run.info.run_id, "split_summary.json", str(tmp_path))
    assert json.loads(Path(summary_local).read_text())["n_train"] == summary["n_train"]


def test_result_shapes_and_paired_comparison(e2e):
    result = e2e["result"]
    assert set(result.models) == set(MODEL_NAMES)
    for mr in result.models.values():
        assert set(mr.confidence_intervals) == set(BOOTSTRAP_METRICS)
        assert mr.calibration_curve and mr.model_uri
    assert set(result.comparison.point) == set(BOOTSTRAP_METRICS) == set(result.comparison.intervals)
    lr, xgb = (result.models[n].test_metrics for n in MODEL_NAMES)
    assert result.comparison.point["abs_calibration_gap"] == pytest.approx(
        xgb["abs_calibration_gap"] - lr["abs_calibration_gap"]
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.comparison.point = {}  # type: ignore[misc]


def test_same_inputs_give_identical_test_metrics(tmp_path):
    gold = _write_gold(tmp_path / "gold", make_gold_frame(300, seed=1))
    a = train_and_evaluate(_config(gold, tmp_path / "a", n_bootstrap=0))
    b = train_and_evaluate(_config(gold, tmp_path / "b", n_bootstrap=0))
    for name in MODEL_NAMES:
        for key, value in a.models[name].test_metrics.items():
            # bit-identical results were observed locally; allclose tolerates BLAS-threading differences on CI
            np.testing.assert_allclose(b.models[name].test_metrics[key], value, rtol=0, atol=1e-12)


def test_autolog_is_disabled_after_success_and_after_failure(tmp_path, monkeypatch):
    gold = _write_gold(tmp_path / "gold", make_gold_frame(300, seed=1))
    tracking_dir = tmp_path / "tracking"
    uri = mlflow_tracking_uri(tracking_dir)

    train_and_evaluate(_config(gold, tracking_dir, n_bootstrap=0))
    _autolog_is_off(uri)  # (a) after a normal return

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    # a name training.py must import into its own namespace; it runs INSIDE the try, after autolog was enabled
    monkeypatch.setattr(training_module, "evaluate_probabilities", boom)
    with pytest.raises(RuntimeError, match="boom"):
        train_and_evaluate(_config(gold, tracking_dir, n_bootstrap=0))
    _autolog_is_off(uri)  # (b) after a failure: the `finally` fired (baseline taken AFTER the failing call)


def test_bad_fold_config_leaves_no_mlflow_side_effects(tmp_path):
    gold = _write_gold(tmp_path / "gold", make_gold_frame(150, seed=0))
    tracking_dir = tmp_path / "never-created"
    probe_uri = f"sqlite:///{tmp_path}/probe.db"
    mlflow.set_tracking_uri(probe_uri)  # any stray autolog run would land here and be detectable

    with pytest.raises(ValueError, match="distinct patients"):
        train_and_evaluate(_config(gold, tracking_dir, calibration_cv_folds=500))

    assert not tracking_dir.exists()
    client = MlflowClient(tracking_uri=probe_uri)
    assert client.search_runs([e.experiment_id for e in client.search_experiments()]) == []
    LogisticRegression().fit(np.array([[0.0], [1.0], [2.0], [3.0]]), np.array([0, 0, 1, 1]))
    assert mlflow.active_run() is None
    assert client.search_runs([e.experiment_id for e in client.search_experiments()]) == []


def test_deleted_experiment_raises_value_error(tmp_path):
    gold = _write_gold(tmp_path / "gold", make_gold_frame(150, seed=0))
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    client = MlflowClient(tracking_uri=mlflow_tracking_uri(tracking_dir))
    client.delete_experiment(client.create_experiment(EXPERIMENT))
    with pytest.raises(ValueError, match=EXPERIMENT):
        train_and_evaluate(_config(gold, tracking_dir))


def test_warns_when_few_test_positives(tmp_path):
    df = make_gold_frame(150, seed=0)
    split = chronological_group_split(df, reference_date=REFERENCE_DATE, test_start_date=TEST_START)
    # fixture precondition: fails loudly HERE (not as a confusing missing-warning failure) if the generator changes
    assert split.summary["n_test_positive"] < 50
    assert split.train["is_readmitted"].nunique() == split.test["is_readmitted"].nunique() == 2

    gold = _write_gold(tmp_path / "gold", df)
    with pytest.warns(UserWarning, match="unreliable"):
        result = train_and_evaluate(_config(gold, tmp_path / "t", calibration_cv_folds=3, n_bootstrap=20))
    assert set(result.models) == set(MODEL_NAMES)


def test_n_bootstrap_zero_skips_phase_b(tmp_path):
    gold = _write_gold(tmp_path / "gold", make_gold_frame(300, seed=1))
    tracking_dir = tmp_path / "tracking"
    result = train_and_evaluate(_config(gold, tracking_dir, n_bootstrap=0))
    client = MlflowClient(tracking_uri=mlflow_tracking_uri(tracking_dir))
    exp = client.get_experiment_by_name(EXPERIMENT)
    for run in client.search_runs([exp.experiment_id]):
        assert run.data.tags["bootstrap_status"] == "skipped"
        keys = set(run.data.metrics)
        assert not any(k.endswith(("_ci_lower", "_ci_upper")) for k in keys)
        assert "test_bootstrap_resamples_used" not in keys
        assert "bootstrap_summary.json" not in {a.path for a in client.list_artifacts(run.info.run_id)}
        assert "test_roc_auc" in keys
        if run.data.tags["model_name"] == "xgboost_calibrated":
            assert "test_diff_vs_logistic_regression_roc_auc" in keys  # the point difference is still logged
    for mr in result.models.values():
        assert mr.confidence_intervals == {}
    assert result.comparison.intervals == {} and result.comparison.point


@pytest.mark.parametrize(
    "overrides",
    [
        {"test_start_date": "2023-01-01"},  # wrong format
        {"train_start_date": "20230101"},  # equal to test_start_date
        {"test_start_date": "20261231"},  # after reference_date
        {"calibration_method": "platt"},
        {"calibration_cv_folds": 1},
        {"readmission_window_days": 0},
        {"n_bootstrap": -1},
        {"seed": -1},
        {"seed": 2**32},
    ],
)
def test_rejects_invalid_config(tmp_path, overrides):
    with pytest.raises(ValueError):
        train_and_evaluate(_config(tmp_path / "gold-not-read", tmp_path / "t", **overrides))
    assert not (tmp_path / "t").exists()  # rejected before any side effect


def test_warnings_from_split_are_not_swallowed(tmp_path):
    """A censoring-buffer warning raised inside chronological_group_split must surface through train_and_evaluate."""
    df = make_gold_frame(150, seed=0)
    # make >1% of rows fall in the censoring buffer: push 5 rows' stop just past the buffer cutoff
    idx = df.index[:5]
    df.loc[idx, "index_start"] = pd.Timestamp("2026-08-10T00:00:00Z")
    df.loc[idx, "index_stop"] = pd.Timestamp("2026-08-18T00:00:00Z")
    gold = _write_gold(tmp_path / "gold", df)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        train_and_evaluate(_config(gold, tmp_path / "t", calibration_cv_folds=3, n_bootstrap=0))
    assert any("reference_date" in str(w.message) for w in caught)


# --- slice 2b: config vs the gold table's own metadata ---


@pytest.mark.parametrize(
    "config_overrides",
    [
        {"reference_date": "20261231"},  # later than the Big Join's (the gap that used to be undetectable)
        {"reference_date": "20260101"},  # earlier
        {"readmission_window_days": 14},
    ],
)
def test_config_that_disagrees_with_the_gold_metadata_is_rejected_before_any_side_effect(tmp_path, config_overrides):
    gold = _write_gold(tmp_path / "gold", make_gold_frame(300, seed=1))  # metadata: reference_date 20260916, window 30
    with pytest.raises(ValueError, match="does not match the gold table"):
        train_and_evaluate(_config(gold, tmp_path / "t", **config_overrides))
    assert not (tmp_path / "t").exists()  # no MLflow tracking dir / experiment created


def test_runs_record_the_gold_metadata(tmp_path):
    gold = _write_gold(tmp_path / "gold", make_gold_frame(300, seed=1), lookback_years=2)
    tracking_dir = tmp_path / "tracking"
    train_and_evaluate(_config(gold, tracking_dir, n_bootstrap=0))
    client = MlflowClient(tracking_uri=mlflow_tracking_uri(tracking_dir))
    runs = client.search_runs([client.get_experiment_by_name(EXPERIMENT).experiment_id])
    assert len(runs) == 2
    for run in runs:
        assert run.data.tags["gold_label_definition"] == LABEL_DEFINITION
        assert run.data.params["gold_lookback_years"] == "2"
        assert "gold_metadata.json" in {a.path for a in client.list_artifacts(run.info.run_id)}


def _mixing_warnings(caught) -> list:
    return [w for w in caught if "gold_label_definition" in str(w.message)]


def test_warns_when_the_experiment_already_holds_runs_from_another_label_definition(tmp_path):
    gold = _write_gold(tmp_path / "gold", make_gold_frame(300, seed=1))
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    client = MlflowClient(tracking_uri=mlflow_tracking_uri(tracking_dir))
    # artifact_location inside tmp_path: without it MLflow defaults to ./mlruns in the working directory, and because the
    # experiment already exists train_and_evaluate would not set one either (the test would leak artifacts into the repo)
    experiment_id = client.create_experiment(EXPERIMENT, artifact_location=(tracking_dir / "artifacts").as_uri())
    client.create_run(experiment_id)  # an untagged run, like one logged before slice 2b
    with pytest.warns(UserWarning, match=r"1 run\(s\).*gold_label_definition"):
        train_and_evaluate(_config(gold, tracking_dir, n_bootstrap=0))


def test_warns_when_an_existing_run_carries_a_different_label_definition(tmp_path):
    gold = _write_gold(tmp_path / "gold", make_gold_frame(300, seed=1))
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    client = MlflowClient(tracking_uri=mlflow_tracking_uri(tracking_dir))
    experiment_id = client.create_experiment(EXPERIMENT, artifact_location=(tracking_dir / "artifacts").as_uri())
    other = client.create_run(experiment_id)
    client.set_tag(other.info.run_id, "gold_label_definition", "unplanned_readmission_v0")  # tagged, but a DIFFERENT definition
    with pytest.warns(UserWarning, match=r"1 run\(s\).*gold_label_definition"):
        train_and_evaluate(_config(gold, tracking_dir, n_bootstrap=0))


def test_no_mixing_warning_for_a_fresh_experiment_or_repeat_runs_of_the_same_label(tmp_path):
    gold = _write_gold(tmp_path / "gold", make_gold_frame(300, seed=1))
    tracking_dir = tmp_path / "tracking"
    for _ in range(2):  # the first creates the experiment, the second re-uses it (all runs tagged with the same definition)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            train_and_evaluate(_config(gold, tracking_dir, n_bootstrap=0))
        assert not _mixing_warnings(caught)
