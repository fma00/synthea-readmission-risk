from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from readmission_risk.models.data import (
    LABEL_COLUMN,
    gold_fingerprint,
    load_gold_table,
    prepare_features,
)
from readmission_risk.models.tracking import load_logged_model
from readmission_risk.pipeline.gold_metadata import LABEL_DEFINITION
from readmission_risk.scoring import pipeline
from readmission_risk.scoring.pipeline import (
    ScoringConfig,
    _validate_config,
    score_run_date,
)
from readmission_risk.scoring.ranking import PREDICTION_COLUMNS
from readmission_risk.scoring.store import read_partition
from tests.scoring.helpers import (
    DEFAULT_RUN_DATE,
    DEFAULT_WINDOW_DAYS,
    GUARD_CASES,
    assert_count_phrase,
    clone_run,
    forbid_model_loading,
    write_gold_dir,
)

RUN_DATE = date(2026, 6, 30)


def _config(env, out: Path, **overrides) -> ScoringConfig:
    fields = {
        "gold_dir": env.gold_dir,
        "run_date": DEFAULT_RUN_DATE,
        "output_dir": out,
        "tracking_dir": env.tracking_dir,
        "experiment_name": env.experiment_name,
        "window_days": DEFAULT_WINDOW_DAYS,
    }
    return ScoringConfig(**{**fields, **overrides})


class Recorder:
    """Loader/writer stand-ins. `strict` fakes fail the test if they are ever called; pass-through ones wrap the real
    callables and record the order and arguments of the calls."""

    def __init__(self):
        self.calls: list[tuple] = []

    def strict_loader(self, *args, **kwargs):
        self.calls.append(("load", *args))
        raise AssertionError("loader must not be called")

    def strict_writer(self, *args, **kwargs):
        self.calls.append(("write", *args))
        raise AssertionError("writer must not be called")

    def loader(self, tracking_dir, run_id):
        self.calls.append(("load", tracking_dir, run_id))
        return load_logged_model(tracking_dir, run_id)

    def writer(self, predictions, output_dir, run_date):
        from readmission_risk.scoring.store import write_partition

        self.calls.append(("write", predictions, output_dir, run_date))
        return write_partition(predictions, output_dir, run_date)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _independent_scores(env, run_id: str, run_date: str = DEFAULT_RUN_DATE, window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
    batch = env.expected_batch(run_date, window_days)
    model = load_logged_model(env.tracking_dir, run_id)
    scores = model.predict_proba(prepare_features(batch.drop(columns=[LABEL_COLUMN])))[:, 1]
    return dict(zip(batch["encounter_id"], scores, strict=True))


# --------------------------------------------------------------------------------------------- guards, through the pipeline
@pytest.mark.parametrize("case", GUARD_CASES, ids=[c.case_id for c in GUARD_CASES])
def test_each_guard_refuses_exactly_its_defect(scoring_env, tmp_path, monkeypatch, case):
    run_id, extras = case.build(scoring_env, "guard-cases")
    recorder, out = Recorder(), tmp_path / "out"
    forbid_model_loading(monkeypatch)  # only around the scoring call: `build` legitimately loads a model to clone it
    with pytest.raises(ValueError) as info:
        score_run_date(_config(scoring_env, out, run_id=run_id), loader=recorder.strict_loader, writer=recorder.strict_writer)
    message = str(info.value)
    assert case.guard_key in message
    for extra in extras:  # full sentences carrying exact counts; the count must not be the tail of a longer number
        assert re.search(rf"(?<![0-9]){re.escape(extra)}", message), f"{extra!r} not in: {message}"
    for forbidden in case.forbidden:
        assert forbidden not in message
    assert recorder.calls == [] and not out.exists()


# --------------------------------------------------------------------------------------------- end to end
def test_end_to_end_scores_only_the_held_out_batch(scoring_env, tmp_path):
    env, out = scoring_env, tmp_path / "out"
    result = score_run_date(_config(env, out, top_n=7))
    predictions = result.predictions

    expected = env.expected_batch(DEFAULT_RUN_DATE, DEFAULT_WINDOW_DAYS)
    assert len(expected) >= 20  # fixture precondition (measured: 50)
    assert set(predictions["encounter_id"]) == set(expected["encounter_id"])
    assert (predictions["model_run_id"] == env.lr_run_id).all()

    # scores id by id (never positionally) against an independent recomputation
    independent = _independent_scores(env, env.lr_run_id)
    np.testing.assert_allclose(
        predictions.set_index("encounter_id")["risk_score"].reindex(list(independent)).to_numpy(), list(independent.values()), rtol=1e-12
    )
    order = sorted(independent, key=lambda encounter_id: (-independent[encounter_id], encounter_id))
    assert list(predictions["encounter_id"]) == order  # the total order: score descending, encounter_id ascending

    # non-default top_n=7 must reach the frame (kills a pipeline that passes DEFAULT_TOP_N on)
    assert (predictions["top_n"] == 7).all()
    assert int(predictions["is_top_n"].sum()) == min(7, len(predictions))
    assert list(predictions.loc[predictions["is_top_n"], "rank"]) == list(range(1, min(7, len(predictions)) + 1))

    # provenance, asserted independently of the pipeline (kills swapped same-typed str arguments)
    gold = load_gold_table(env.gold_dir)
    assert (predictions["gold_fingerprint"] == gold_fingerprint(gold)).all()
    assert (predictions["gold_reference_date"] == "20260916").all()
    assert (predictions["label_definition"] == LABEL_DEFINITION).all()
    assert (predictions["model_name"] == "logistic_regression").all() and (predictions["partition"] == "test").all()
    assert (predictions["as_of_date"] == RUN_DATE).all() and (predictions["scoring_window_days"] == DEFAULT_WINDOW_DAYS).all()
    assert result.window_start == pd.Timestamp("2026-01-02T00:00:00Z")  # 180 days ending 2026-06-30
    assert result.window_end == pd.Timestamp("2026-07-01T00:00:00Z")  # end exclusive
    assert result.window_days == DEFAULT_WINDOW_DAYS

    pd.testing.assert_frame_equal(read_partition(out, RUN_DATE), predictions)
    assert result.partition_path == out / "run_date=20260630" / "predictions.parquet"


def test_label_does_not_reach_ranking(scoring_env, tmp_path):
    env = scoring_env
    permuted = env.df.assign(**{LABEL_COLUMN: np.random.default_rng(1).permutation(env.df[LABEL_COLUMN].to_numpy())})
    ids = set(env.expected_batch(DEFAULT_RUN_DATE, DEFAULT_WINDOW_DAYS)["encounter_id"])
    in_window = env.df["encounter_id"].isin(ids)
    assert (env.df.loc[in_window, LABEL_COLUMN].to_numpy() != permuted.loc[in_window, LABEL_COLUMN].to_numpy()).any()
    gold_b = write_gold_dir(tmp_path / "gold-b", permuted)  # same rows and counts, different labels
    clone = clone_run(
        env.tracking_dir, env.lr_run_id, experiment="label-test", gold_frame=env.df,
        tags={"gold_fingerprint": gold_fingerprint(load_gold_table(gold_b))},
    )  # fmt: skip
    a = score_run_date(_config(env, tmp_path / "a")).predictions
    b = score_run_date(_config(env, tmp_path / "b", gold_dir=gold_b, run_id=clone)).predictions
    columns = ["encounter_id", "rank", "is_top_n", "risk_score"]  # the provenance columns differ by construction
    pd.testing.assert_frame_equal(a[columns], b[columns])


def test_default_model_and_explicit_run_id(scoring_env, tmp_path):
    env = scoring_env
    default = score_run_date(_config(env, tmp_path / "default"))
    assert default.run.run_id == env.lr_run_id

    recorder = Recorder()
    by_name = score_run_date(_config(env, tmp_path / "by-name", model_name="xgboost_calibrated"), loader=recorder.loader)
    by_id = score_run_date(_config(env, tmp_path / "by-id", model_name=None, run_id=env.xgb_run_id))
    assert by_name.run.run_id == env.xgb_run_id
    assert recorder.calls[0][2] == by_name.run.run_id  # the loader gets the RESOLVED run (kills resolve-XGB-load-LR)
    pd.testing.assert_frame_equal(by_name.predictions, by_id.predictions)

    xgb_scores = _independent_scores(env, env.xgb_run_id)
    lr_scores = _independent_scores(env, env.lr_run_id)
    got = by_name.predictions.set_index("encounter_id")["risk_score"]
    np.testing.assert_allclose(got.reindex(list(xgb_scores)).to_numpy(), list(xgb_scores.values()), rtol=1e-12)
    assert not np.allclose(list(xgb_scores.values()), list(lr_scores.values()))  # the two models really differ

    with pytest.raises(ValueError, match="mutually exclusive"):
        score_run_date(_config(env, tmp_path / "x", model_name="xgboost_calibrated", run_id=env.xgb_run_id))


def test_default_window_days_comes_from_gold_metadata(scoring_env, tmp_path):
    env = scoring_env
    gold_c = write_gold_dir(tmp_path / "gold-c", env.df, readmission_window_days=14)
    clone = clone_run(
        env.tracking_dir, env.lr_run_id, experiment="window-test", gold_frame=env.df, params={"readmission_window_days": "14"}
    )
    run_date = "20251112"
    expected = env.expected_batch(run_date, 14)
    assert len(expected) >= 5  # fixture precondition (measured: 10)
    result = score_run_date(_config(env, tmp_path / "default", gold_dir=gold_c, run_id=clone, run_date=run_date, window_days=None))
    assert (result.predictions["scoring_window_days"] == 14).all() and result.window_days == 14
    assert result.window_end - result.window_start == pd.Timedelta(days=14)
    assert set(result.predictions["encounter_id"]) == set(expected["encounter_id"])
    override = score_run_date(_config(env, tmp_path / "override", gold_dir=gold_c, run_id=clone, run_date=run_date, window_days=180))
    assert (override.predictions["scoring_window_days"] == 180).all()  # kills a hard-coded 30 or a metadata-ignoring default


_BAD_CONFIGS = [
    {"run_date": "2026-06-30"},
    {"run_date": "20260231"},
    {"run_date": ""},
    {"top_n": 0},
    {"top_n": -1},
    {"top_n": True},
    {"top_n": 2.5},
    {"top_n": 2**31},
    {"top_n": 3_000_000_000},
    {"window_days": 0},
    {"window_days": True},
    {"window_days": 1.5},
    {"model_name": "random_forest"},
    {"run_id": ""},
    {"model_name": "logistic_regression", "run_id": "abc"},
]


@pytest.mark.parametrize("overrides", _BAD_CONFIGS, ids=[str(sorted(o.items())) for o in _BAD_CONFIGS])
def test_invalid_config_is_refused_before_any_io(tmp_path, overrides):
    config = ScoringConfig(gold_dir=tmp_path / "does-not-exist", run_date=DEFAULT_RUN_DATE, output_dir=tmp_path / "out")
    config = ScoringConfig(**{**config.__dict__, **overrides})
    with pytest.raises(ValueError):  # a FileNotFoundError would show that the gold table was read first
        _validate_config(config)
    recorder = Recorder()
    with pytest.raises(ValueError):
        score_run_date(config, loader=recorder.strict_loader, writer=recorder.strict_writer)
    assert recorder.calls == [] and not (tmp_path / "out").exists()


def test_valid_config_parses_and_run_id_may_accompany_an_experiment_name(tmp_path):
    config = ScoringConfig(gold_dir=tmp_path, run_date="20260630", run_id="abc", experiment_name="anything")
    assert _validate_config(config) == date(2026, 6, 30)
    with pytest.raises(ValueError, match="mutually exclusive"):
        _validate_config(ScoringConfig(gold_dir=tmp_path, run_date="20260630", run_id="abc", model_name="logistic_regression"))


def test_refusals_happen_before_load_and_before_write(scoring_env, tmp_path):
    env = scoring_env
    recorder = Recorder()
    out = tmp_path / "out"
    for overrides in (
        {"run_id": "0" * 32},  # a guard failure
        {"run_date": "20221130", "window_days": 30},  # an in-sample window
        {"run_date": "20260917"},  # after the gold reference date
    ):
        with pytest.raises(ValueError):
            score_run_date(_config(env, out, **overrides), loader=recorder.strict_loader, writer=recorder.strict_writer)
    assert recorder.calls == [] and not out.exists()

    passing = Recorder()
    result = score_run_date(_config(env, out), loader=passing.loader, writer=passing.writer)
    assert [c[0] for c in passing.calls] == ["load", "write"]  # exactly once each, the loader first
    assert passing.calls[0][1:] == (env.tracking_dir, result.run.run_id)
    assert passing.calls[1][1] is result.predictions and passing.calls[1][2] == out
    assert result.partition_path == out / "run_date=20260630" / "predictions.parquet"

    # the swap seam: a stub writer's return value is what the result reports, and the pipeline writes nothing itself
    sentinel = tmp_path / "somewhere-else" / "partition"
    stub_out = tmp_path / "stub-out"
    swapped = score_run_date(_config(env, stub_out), writer=lambda predictions, output_dir, run_date: sentinel)
    assert swapped.partition_path == sentinel and not stub_out.exists()


def test_pipeline_postconditions_raise_runtime_error(scoring_env, tmp_path, monkeypatch):
    real = pipeline.build_predictions
    for label, tamper in (
        ("drops a row", lambda frame: frame.iloc[1:].reset_index(drop=True)),
        ("swaps an id", lambda frame: frame.assign(encounter_id=["unknown-id", *frame["encounter_id"].iloc[1:]])),
    ):
        monkeypatch.setattr(pipeline, "build_predictions", lambda *a, _t=tamper, **k: _t(real(*a, **k)))
        recorder = Recorder()
        with pytest.raises(RuntimeError):
            score_run_date(_config(scoring_env, tmp_path / label), writer=recorder.strict_writer)
        assert recorder.calls == [], label


def test_idempotent_rerun_and_replacement(scoring_env, tmp_path):
    env, out = scoring_env, tmp_path / "out"
    first = score_run_date(_config(env, out))
    path = first.partition_path
    first_hash = _sha(path)
    second = score_run_date(_config(env, out))
    pd.testing.assert_frame_equal(first.predictions, second.predictions)
    assert _sha(path) == first_hash  # two identical runs give a byte-identical file

    replaced = score_run_date(_config(env, out, model_name="xgboost_calibrated", top_n=3))
    back = read_partition(out, RUN_DATE)
    pd.testing.assert_frame_equal(back, replaced.predictions)
    assert set(back["model_run_id"]) == {env.xgb_run_id} and set(back["model_name"]) == {"xgboost_calibrated"}
    assert (back["top_n"] == 3).all() and int(back["is_top_n"].sum()) == 3  # the first run's values did not survive

    sibling = score_run_date(_config(env, out, run_date="20260615"))
    assert sibling.partition_path != path and sibling.partition_path.is_file()  # an independent partition

    before = _sha(path)
    with pytest.raises(ValueError):
        score_run_date(_config(env, out, run_id="0" * 32))  # a refused rerun leaves the partition untouched
    assert _sha(path) == before


def test_output_independent_of_gold_row_order_and_part_files(scoring_env, tmp_path):
    env = scoring_env
    reordered = write_gold_dir(tmp_path / "gold-d", env.df, n_parts=3, shuffle_seed=7)  # same rows, other order and layout
    a = score_run_date(_config(env, tmp_path / "a")).predictions
    b = score_run_date(_config(env, tmp_path / "b", gold_dir=reordered)).predictions
    pd.testing.assert_frame_equal(a, b)  # the fingerprint sorts by encounter_id, so the original run is still accepted


def test_observed_outcomes_are_opt_in_and_never_persisted(scoring_env, tmp_path):
    env = scoring_env
    off = score_run_date(_config(env, tmp_path / "off"))
    assert off.observed is None
    on = score_run_date(_config(env, tmp_path / "on", show_observed_outcomes=True, top_n=4))
    for result in (off, on):
        assert list(read_partition(result.partition_path.parent.parent, RUN_DATE).columns) == list(PREDICTION_COLUMNS)

    # a fully independent recomputation with a non-default top_n (avoids a self-referential comparison)
    scores = _independent_scores(env, env.lr_run_id)
    order = sorted(scores, key=lambda encounter_id: (-scores[encounter_id], encounter_id))
    labels = env.df.set_index("encounter_id")[LABEL_COLUMN]
    observed = on.observed
    assert observed.n_batch == len(order) and observed.n_top == 4
    assert observed.n_positive_batch == int(labels.loc[order].sum())
    assert observed.n_positive_top == int(labels.loc[order[:4]].sum())
    assert observed.precision_at_n == pytest.approx(observed.n_positive_top / 4)


def test_pipeline_refuses_a_natural_in_sample_window(scoring_env, tmp_path):
    env, recorder = scoring_env, Recorder()
    for run_date, n_train, n_dropped in (("20221130", 3, 3), ("20221215", 2, 6)):  # measured on the fixture (0 test rows)
        with pytest.raises(ValueError) as info:
            score_run_date(
                _config(env, tmp_path / "out", run_date=run_date, window_days=30),
                loader=recorder.strict_loader,
                writer=recorder.strict_writer,
            )
        assert_count_phrase(str(info.value), n_train, "in-sample")
        assert_count_phrase(str(info.value), n_dropped, "discharge(s) excluded by the split")
    assert recorder.calls == []


def test_pipeline_refuses_windows_past_the_observation_horizon(scoring_env, tmp_path):
    """Gold keeps a non-readmitted discharge only if its outcome window was closed by the reference date (2026-09-16 with a
    30-day label window: discharges before 2026-08-18). A window reaching past that holds only readmitted discharges, so it is
    refused rather than scored as a silently truncated batch, even though nothing in it is in-sample."""
    env, recorder = scoring_env, Recorder()
    ok = score_run_date(_config(env, tmp_path / "ok", run_date="20260817"))  # window [2026-02-19, 2026-08-18): the last valid one
    assert len(ok.predictions) > 0 and ok.window_end == pd.Timestamp("2026-08-18T00:00:00Z")
    with pytest.raises(ValueError, match="on or before 2026-08-17"):
        score_run_date(
            _config(env, tmp_path / "out", run_date="20260818"), loader=recorder.strict_loader, writer=recorder.strict_writer
        )
    assert recorder.calls == [] and not (tmp_path / "out").exists()


def test_scoring_modules_do_not_import_the_big_join():
    code = (
        "import importlib, pkgutil, sys\n"
        "import readmission_risk.scoring as pkg\n"
        "for m in pkgutil.iter_modules(pkg.__path__):\n"
        "    importlib.import_module(f'readmission_risk.scoring.{m.name}')\n"
        "assert 'readmission_risk.pipeline.big_join' not in sys.modules, 'the Big Join (Spark) was imported'\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False, env={**os.environ, "MLFLOW_DISABLE_AGENT_HINT": "1"})
    assert done.returncode == 0, done.stderr
