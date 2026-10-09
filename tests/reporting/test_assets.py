from __future__ import annotations

import datetime as dt
import os
import re

import mlflow
import pytest

from readmission_risk.reporting import assets
from readmission_risk.reporting.assets import AssetConfig, build_assets, load_curves
from tests.reporting.conftest import (
    HIGH_CURVE,
    LOW_CURVE,
    LR_RUN,
    XGB_RUN,
    build_store,
    make_curve,
    make_predictions,
)

MODELS = ("logistic_regression", "xgboost_calibrated")


def _config(tmp_path, **overrides):
    values = {
        "tracking_dir": tmp_path / "store",
        "experiment_name": "reporting-test",
        "predictions_path": tmp_path / "p.parquet",
        "out_dir": tmp_path / "out",
    }
    return AssetConfig(**{**values, **overrides})


def _build(config, curves, facts, predictions):
    return build_assets(config, curve_loader=lambda *_: (curves, facts), predictions_loader=lambda _: predictions)


# ---------------------------------------------------------------- load_curves


def test_load_curves_returns_both_curves_and_facts_and_restores_the_tracking_uri(tmp_path):
    build_store(tmp_path, curves_by_model=dict.fromkeys(MODELS, LOW_CURVE))
    mlflow.set_tracking_uri("file:///tmp/some-other-uri")
    curves, facts = load_curves(tmp_path, "reporting-test")
    assert mlflow.get_tracking_uri() == "file:///tmp/some-other-uri"
    assert set(curves) == set(MODELS) and curves["logistic_regression"] == LOW_CURVE
    assert facts["logistic_regression"].test_n_rows == 100 and facts["xgboost_calibrated"].test_n_positive == 8
    assert facts["logistic_regression"].run_id != facts["xgboost_calibrated"].run_id


def test_load_curves_rejects_a_curve_that_does_not_sum_to_the_run(tmp_path):
    build_store(tmp_path, curves_by_model=dict.fromkeys(MODELS, LOW_CURVE), rows=101)
    with pytest.raises(ValueError, match="covers 100 rows"):
        load_curves(tmp_path, "reporting-test")


def test_load_curves_rejects_different_test_encounters(tmp_path):
    build_store(
        tmp_path,
        curves_by_model=dict.fromkeys(MODELS, LOW_CURVE),
        test_ids={"logistic_regression": ["e1", "e2"], "xgboost_calibrated": ["e1", "e3"]},
    )
    with pytest.raises(ValueError, match="same test encounters"):
        load_curves(tmp_path, "reporting-test")


def test_load_curves_keeps_an_id_spelled_na_as_a_string(tmp_path):
    build_store(
        tmp_path,
        curves_by_model=dict.fromkeys(MODELS, LOW_CURVE),
        test_ids={"logistic_regression": ["NA", "e1"], "xgboost_calibrated": ["e1", "NA"]},
    )
    load_curves(tmp_path, "reporting-test")  # equal sets only if "NA" survives the CSV parse as the string "NA"


def test_load_curves_raises_file_not_found_for_a_missing_store(tmp_path):
    with pytest.raises(FileNotFoundError, match="No MLflow store"):
        load_curves(tmp_path / "nothing", "reporting-test")


def test_load_curves_rejects_a_missing_experiment(tmp_path):
    build_store(tmp_path, curves_by_model=dict.fromkeys(MODELS, LOW_CURVE))
    with pytest.raises(ValueError, match="not found"):
        load_curves(tmp_path, "no-such-experiment")


def test_load_curves_rejects_two_finished_runs_for_one_model(tmp_path):
    build_store(tmp_path, curves_by_model=dict.fromkeys(MODELS, LOW_CURVE))
    experiment = mlflow.get_experiment_by_name("reporting-test")
    with mlflow.start_run(experiment_id=experiment.experiment_id):
        mlflow.set_tag("model_name", "logistic_regression")
    with pytest.raises(ValueError, match="2 FINISHED runs"):
        load_curves(tmp_path, "reporting-test")


# ---------------------------------------------------------------- build_assets


def test_build_assets_writes_the_expected_files_and_no_temp_directory(tmp_path, curves, facts, predictions):
    config = _config(tmp_path)
    written = _build(config, curves, facts, predictions)
    out = config.out_dir
    assert sorted(p.relative_to(out).as_posix() for p in written) == [
        "preview/reliability_dark.png",
        "preview/reliability_light.png",
        "reliability_dark.svg",
        "reliability_light.svg",
        "sample_topn.md",
    ]
    assert not [p for p in out.iterdir() if p.name.startswith(".tmp-")]
    svg = (out / "reliability_light.svg").read_text(encoding="utf-8")
    assert "<text" in svg and "font-weight: 200" in svg  # a save outside house_style would write glyph paths instead
    assert LR_RUN in svg and XGB_RUN in svg and "100 test rows, 8 positives" in svg and "5 quantile bins" in svg


def test_sample_table_caption_with_the_under_prediction_clause(tmp_path, curves, facts, predictions):
    _build(_config(tmp_path), curves, facts, predictions)
    lines = (tmp_path / "out" / "sample_topn.md").read_text(encoding="utf-8").splitlines()
    assert lines[0] == (
        "Sample Top-5: ranks 1-5 of 7 discharges scored in the 30-day window ending 2025-06-30, Logistic regression. "
        "Demo over held-out rows (development set), not new-data scoring."
    )
    assert lines[1] == (
        "Risk scores are rankings, not absolute risks: the test-window prevalence is 8.00% (8 of 100) "
        "and the models under-predict on average."
    )
    assert lines[2] == "" and lines[3].startswith("| Rank |")


def test_sample_table_caption_omits_the_clause_when_the_data_do_not_support_it(tmp_path, facts, predictions):
    curves = {"logistic_regression": LOW_CURVE, "xgboost_calibrated": HIGH_CURVE}  # XGBoost's mean prediction exceeds 8%
    _build(_config(tmp_path), curves, facts, predictions)
    lines = (tmp_path / "out" / "sample_topn.md").read_text(encoding="utf-8").splitlines()
    assert lines[1] == "Risk scores are rankings, not absolute risks: the test-window prevalence is 8.00% (8 of 100)."


def test_description_names_both_bin_counts_when_they_differ(tmp_path, facts, predictions):
    curves = {"logistic_regression": LOW_CURVE, "xgboost_calibrated": make_curve([(0.02, 2, 50), (0.1, 6, 50)])}
    _build(_config(tmp_path), curves, facts, predictions)
    svg = (tmp_path / "out" / "reliability_light.svg").read_text(encoding="utf-8")
    assert "5 and 2 quantile bins (logistic_regression, xgboost_calibrated)" in svg


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"model_run_id": ["c" * 32] * 5 + ["d" * 32] * 2}, "exactly one distinct"),
        ({"model_run_id": "c" * 32}, "stale predictions file"),
        ({"model_name": "other_model"}, "not one of"),
        ({"partition": ["test"] * 6 + ["train"]}, "outside the held-out"),
        ({"scoring_window_days": [30] * 6 + [14]}, "exactly one distinct"),
    ],
)
def test_build_assets_rejects_inconsistent_predictions_and_writes_nothing(tmp_path, curves, facts, overrides, match):
    config = _config(tmp_path)
    with pytest.raises(ValueError, match=match):
        _build(config, curves, facts, make_predictions(7, **overrides))
    assert not config.out_dir.exists()


def test_build_assets_rejects_null_values_in_a_provenance_column(tmp_path, curves, facts):
    frame = make_predictions(7)
    frame.loc[0, "as_of_date"] = None
    with pytest.raises(ValueError, match="non-null"):
        _build(_config(tmp_path), curves, facts, frame)


def test_a_loader_that_raises_leaves_out_dir_untouched(tmp_path, predictions):
    config = _config(tmp_path)

    def failing(*_):
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        build_assets(config, curve_loader=failing, predictions_loader=lambda _: predictions)
    assert not config.out_dir.exists()


def test_a_failure_during_the_move_loop_still_removes_the_temp_directory(tmp_path, curves, facts, predictions, monkeypatch):
    config = _config(tmp_path)
    real_replace, calls = os.replace, []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(assets.os, "replace", flaky)
    with pytest.raises(OSError, match="disk full"):
        _build(config, curves, facts, predictions)
    assert not [p for p in config.out_dir.iterdir() if re.match(r"\.tmp-", p.name)]


def test_rerunning_replaces_the_previous_files(tmp_path, curves, facts, predictions):
    config = _config(tmp_path)
    _build(config, curves, facts, predictions)
    (config.out_dir / "sample_topn.md").write_text("stale", encoding="utf-8")
    _build(config, curves, facts, predictions)
    assert (config.out_dir / "sample_topn.md").read_text(encoding="utf-8").startswith("Sample Top-5")


def test_as_of_date_accepts_a_timestamp(tmp_path, curves, facts):
    import pandas as pd

    frame = make_predictions(7, as_of_date=pd.Timestamp(dt.date(2025, 6, 30)))
    _build(_config(tmp_path), curves, facts, frame)
    assert "ending 2025-06-30" in (tmp_path / "out" / "sample_topn.md").read_text(encoding="utf-8")
