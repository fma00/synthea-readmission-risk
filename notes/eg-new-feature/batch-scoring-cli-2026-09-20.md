# Slice 4: Top-N discharge-risk triage CLI (a DEMO over held-out gold rows)

**Status:** implemented (2026-09-20) from design v6, revised after goldfish rounds 1-5 (round 4 hit the 3-revision cap; the user chose to fix the 9 remaining gaps and run one more round; round 5: readiness closed, critic listed 5 test-level gaps, fixed in v6) (see "Design-check log" at the end). **Follows:** slice 3 (`model-training-2026-09-19.md`) and slice 2b (`readmission-label-planned-exclusion-2026-09-20.md`). **Last slice** of the four in the README.

## Why

CLAUDE.md "MLOps spine" locks a v1 batch scorer: *a single Typer-based CLI script, no orchestrator, built as pure importable functions, idempotent, overwriting the full prediction partition per run date, so a later Airflow migration wraps it without a rewrite.* Slice 3 left two logged models (MLflow experiment `readmission-risk-2b`, tracking dir `mlflow/`) and the "Big Join + Triage List" concept needs a triage list. This slice produces one.

**It is a demo, not as-of scoring** (decided by the user, not re-opened here): it scores discharges that already exist in the gold table `data/gold/full_run_2b`, restricted to the rows the chosen model's own run recorded as its held-out `test` partition. It never builds features for unlabelled recent discharges.

Decisions made with the user on 2026-09-20 (via `AskUserQuestion`, all "recommended" options):

| Topic | Decision |
|---|---|
| Run date | A *trailing window* of W calendar days ending on the run date (inclusive). Default W = the gold table's `readmission_window_days` (30). |
| In-sample rows | Refused, no override flag. Any discharge in the window that is not in the model's held-out partition makes the run fail. |
| Stored rows | The full ranked batch is written, with `rank` and `is_top_n`. The CLI prints only the Top-N. |
| Observed outcomes | Opt-in (`--show-observed-outcomes`), printed to stdout only, joined after scoring, never written to the prediction file. |
| Layout | `<output-dir>/run_date=YYYYMMDD/predictions.parquet`, Parquet only, one file per run date. The run date inside the file is the column `as_of_date` (not `run_date`: a same-named column inside a Hive-style `run_date=` partition makes `pd.read_parquet(<output-dir>)` fail with `ArrowTypeError`, verified; with `as_of_date` the parent-directory read works and yields a categorical `run_date` partition column). |
| Model selection | `--model {logistic_regression,xgboost_calibrated}` (default `logistic_regression`) resolved inside `--experiment-name`, or `--run-id`; the two are mutually exclusive. |
| Dependency | Add `typer==0.27.2` to `requirements.in` and recompile with hashes. |

## Scope

**In**
- New package `src/readmission_risk/scoring/` (pure functions) and a thin Typer script `scripts/score_discharges.py`.
- Guards that refuse a mismatched run/gold pair before any model is unpickled.
- Deterministic ranking with an explicit tie-break; idempotent partition overwrite.
- `typer` in `requirements.in` / `requirements.txt`; tests under `tests/scoring/`; README and CLAUDE.md updates.
- A real-data run on `data/gold/full_run_2b`, sanity-checked (procedure under Verification).

**Out (named follow-ups, see the last section)**
- As-of scoring of unlabelled recent discharges (features from raw CSVs, open-stay semantics, a scoring-side eligibility filter). `flag_inpatient_stays`' null-`STOP` semantics would have to change for it, so it is **not** reused here.
- Label v2 (deferred until after the 100k-200k scale-up). `LABEL_DEFINITION` and the label are not touched.
- Dataproc/GCS/BigQuery, Airflow, hosted MLflow, the Streamlit dashboard.
- Scoring train or dropped rows (no `--allow-in-sample`), feature attribution, threshold selection, retraining.
- **Not modified:** anything under `src/readmission_risk/pipeline/` and `src/readmission_risk/models/`. The scorer only imports from them.

## Surfaces touched

| Path | Change |
|---|---|
| `src/readmission_risk/scoring/__init__.py` | new (docstring only) |
| `src/readmission_risk/scoring/constants.py` | new: `DEFAULT_MODEL`, `DEFAULT_TOP_N`, `DEMO_NOTICE`, `PARTITION_TRAIN = "train"`, `PARTITION_TEST = "test"` (no imports from the other scoring modules: `store.py` needs `DEMO_NOTICE` and `pipeline.py` imports `store.write_partition`, so defining the constant in `pipeline.py` would be a circular import) |
| `src/readmission_risk/scoring/selection.py` | new: `window_bounds`, `select_held_out_window` |
| `src/readmission_risk/scoring/runs.py` | new: `resolve_run_id`, `validate_run`, `ModelRun`, `installed_versions` |
| `src/readmission_risk/scoring/ranking.py` | new: `PREDICTION_COLUMNS`, `score_batch`, `build_predictions`, `ObservedSummary`, `summarize_observed` |
| `src/readmission_risk/scoring/store.py` | new: `PREDICTION_SCHEMA`, `partition_dir`, `write_partition`, `read_partition` |
| `src/readmission_risk/scoring/report.py` | new: `format_report` |
| `src/readmission_risk/scoring/pipeline.py` | new: `ScoringConfig`, `ScoringResult`, `score_run_date`, `_validate_config`; imports only the constants it uses (`DEFAULT_MODEL`, `DEFAULT_TOP_N`) |
| `scripts/score_discharges.py` | new: the Typer layer (no logic) |
| `requirements.in`, `requirements.txt` | add `typer==0.27.2`; recompiled |
| `tests/scoring/` | new: `__init__.py` (required: `tests/` and `tests/models/` are packages and the tests import `tests.models.helpers`; `test_cli.py` and `conftest.py` share basenames with files under `tests/models/`), `conftest.py` (fixtures only), `helpers.py` (plain module: `clone_run`, `GUARD_CASES`, gold/model builders, as `tests/models/helpers.py` is), `test_selection.py`, `test_runs.py`, `test_ranking.py`, `test_store.py`, `test_report.py`, `test_pipeline.py`, `test_cli.py` |
| `README.md`, `CLAUDE.md` | slice status, run instructions, package layout, caveats |

Reused, unchanged: `models.data` (`load_gold_table_with_metadata`, `prepare_features`, `gold_fingerprint`, `FEATURE_COLUMNS`, `LABEL_COLUMN`), `models.tracking` (`mlflow_tracking_uri`, `load_logged_model`), `models.training.MODEL_NAMES`, `pipeline.gold_metadata` (`parse_yyyymmdd`, `GoldMetadata`), `tests/models/helpers.py` (`make_gold_frame`, `write_test_gold_metadata`).

None of the new modules may import `readmission_risk.pipeline.big_join` or start a Spark session. (The rule cannot be "`pyspark` absent from `sys.modules`": verified in this environment that `import mlflow` alone loads `pyspark`, as do `models.tracking` and `models.training`; only `models.data` avoids it because it imports no mlflow. The scorer needs mlflow, so the invariant is about the Big Join module and Spark sessions, and a test asserts `readmission_risk.pipeline.big_join not in sys.modules` after importing every scoring module.)

## Interfaces

### Constants (`constants.py`)

```python
DEFAULT_MODEL = "logistic_regression"
DEFAULT_TOP_N = 10
DEMO_NOTICE = (
    "DEMO, not clinical guidance. Scores come from a weak model trained on synthetic Synthea data, and the ranking "
    "mostly reflects the admission reason (a reason-code-only lookup does as well). The rows are held-out development-set "
    "discharges: the test window was used while designing the models and the label, so it is not a virgin holdout. "
    "Not clinically validated."
)
```

The notice is qualitative on purpose: model metrics live in the README and go stale on retraining. `DEMO_NOTICE` is imported from `readmission_risk.scoring.constants` by every user (`report.py`, `store.py`, the script, the tests); `pipeline.py` does NOT re-export it (an unused import would fail `ruff check .` with F401; the repo has no ruff config, so the defaults apply). On a successful run it is the first line of `format_report`'s output (error paths print only `ERROR: ...` on stderr and nothing on stdout); `--help` shows it (it must be set on the **command**, `@app.command(help=DEMO_NOTICE)`; verified that `Typer(help=...)` on a single-command app is silently dropped from `--help`), and it is stored in the Parquet file's schema metadata.

### `selection.py` (pure)

```python
def window_bounds(run_date: date, window_days: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(start, end): start = (run_date - (window_days - 1) days) at 00:00:00 UTC, INCLUSIVE;
    end = (run_date + 1 day) at 00:00:00 UTC, EXCLUSIVE. A row is in the window iff start <= index_stop < end.
    Calendar days are UTC days (gold timestamps are UTC). ValueError unless type(window_days) is int and >= 1; an
    OverflowError from the date arithmetic (run_date 00010101, window_days 10**9, run_date 99991231 all raise it; verified)
    is caught and re-raised as ValueError('window is outside the supported date range')."""

def select_held_out_window(
    gold: pd.DataFrame, partition_of: pd.Series, *, run_date: date, window_days: int
) -> pd.DataFrame:
    """gold: the frame load_gold_table_with_metadata returns (tz-aware UTC index_stop; ValueError otherwise).
    partition_of: Series indexed by encounter_id with values "train" | "test" (the model run's split_assignment.csv);
    a gold encounter_id missing from its index is "in neither partition" (dropped by the split).
    Steps: (0) ValueError if partition_of has a duplicated index or any value outside {"train","test"}; (1) in_window = window_bounds test on index_stop; (2) among in_window rows count n_train (partition "train") and
    n_dropped (not in partition_of); (3) if n_train + n_dropped > 0 raise ValueError whose message contains
    f"{n_train} in-sample" and f"{n_dropped} discharge(s) excluded by the split" plus the window bounds; (4) if no in_window
    row is "test" raise ValueError starting "no held-out discharges"; (5) return the in-window "test" rows, ALL columns of the input frame (only encounter_id and index_stop are required; ValueError if either is missing),
    sorted by (index_stop, encounter_id), as a copy. Rows OUTSIDE the window are never inspected: their partition is irrelevant."""
```

The completeness guard (step 3) is the whole in-sample policy: it refuses a window that touches training rows, rows purged/dropped by the split, and the censoring-buffer row, instead of silently truncating the batch.

### `runs.py` (touches MLflow, never unpickles a model)

```python
def installed_versions() -> dict[str, str]:
    """{"sklearn_version": sklearn.__version__, "xgboost_version": ..., "mlflow_version": ..., "pandas_version": ..., "numpy_version": ...}
    (exactly the five version tags train_and_evaluate records)."""

@dataclass(frozen=True, eq=False)   # eq=False: it holds a Series, so the generated __eq__/__hash__ would raise; compare fields
class ModelRun:
    run_id: str
    model_name: str                # the run's model_name tag
    test_start_date: str           # the run's test_start_date param, YYYYMMDD (used by guard 12)
    gold_fingerprint: str          # the run's gold_fingerprint tag; == gold_fingerprint(gold) once guard 6 has passed (build_predictions reuses it: no second hash)
    git_commit: str | None         # tag mlflow.source.git.commit
    git_dirty: str | None          # tag git_dirty
    partition_of: pd.Series        # encounter_id -> "train" | "test"; index = encounter_id, object dtype, unique; from split_assignment.csv

def resolve_run_id(tracking_dir: Path, experiment_name: str, model_name: str) -> str:
    """The run_id of the single FINISHED run in `experiment_name` whose tag model_name == model_name.
    ValueError if model_name not in MODEL_NAMES; the experiment does not exist ('experiment ... not found; available: [names of the
    store's non-deleted experiments]', so the default name on the real store, which lacks 'readmission-risk', tells you what to pass)
    or is deleted ('deleted'); or the number of matching runs is 0 ('no FINISHED run ...') or > 1 (message lists every matching
    run_id and says to pass --run-id). Sets the MLflow tracking URI (global, like validate_run). Matching = lifecycle active (`MlflowClient.search_runs([experiment_id], max_results=5000)`, ACTIVE_ONLY by default; the >1 check is over at most 5,000 runs, the same bound `train_and_evaluate`'s own mixed-label warning uses), status FINISHED
    (filtered in Python; a FAILED run with the same tag does not count) and tag model_name equal. Never 'latest wins': a result
    that depends on when you run it would break reproducibility."""

def validate_run(tracking_dir: Path, run_id: str, *, gold: pd.DataFrame, metadata: GoldMetadata) -> ModelRun:
    """Runs every guard below, in this order, raising ValueError (message contains the guard's key) on the first failure.
    Does NOT load the model. Sets the MLflow tracking URI (global, like load_logged_model). Writes nothing to the store; it
    downloads the two artifacts it needs into a `tempfile.TemporaryDirectory` (`MlflowClient.download_artifacts(run_id, path,
    dst_path=tmp)`), deleted on return. Computes `gold_fingerprint(gold)` itself (0.07 s on the real table)."""
```

**Both functions first check that `<tracking_dir>/mlflow.db` exists and raise `FileNotFoundError` otherwise**, before any MLflow call: pointing MLflow's SQLite store at a missing path would create and migrate an empty database, a side effect a read-only scorer must not have.

Guards of `validate_run`, in order (the key in backticks is what the error message must contain):

| # | Key | Passes iff |
|---|---|---|
| 1 | `run not found` | `MlflowClient.get_run` succeeds (MlflowException converted to ValueError; verified that an unknown id raises `MlflowException`) |
| 2 | `not FINISHED` / `deleted` | `run.info.status == "FINISHED"` (else message contains `not FINISHED`) and `run.info.lifecycle_stage == "active"` (else `deleted`: `get_run` returns soft-deleted runs, and `--run-id` bypasses `resolve_run_id`'s ACTIVE_ONLY search) |
| 3 | `model_uri` | tag `model_uri` present (the run came from `train_and_evaluate`) |
| 4 | `model_name` | tag `model_name` present and in `MODEL_NAMES` |
| 5 | `gold_label_definition` | tag present (absent means the run predates slice 2b) and equal to `metadata.label_definition` |
| 6 | `gold_fingerprint` | tag equals `gold_fingerprint(gold)`: the model was trained on exactly this gold table |
| 7 | `reference_date` or `readmission_window_days` (whichever is offending; the message names only that one) | params `reference_date == metadata.reference_date` and `readmission_window_days == str(metadata.readmission_window_days)` (MLflow params are strings). Guard 6 does not cover these: the fingerprint covers the 23 gold columns, not the metadata sidecar. |
| 8 | `library version` | each of the five tags in `installed_versions()` is present and equals the installed version (the model is a cloudpickle, valid only under the lockfile it was trained with); the message lists every mismatch |
| 9 | `feature_columns.json` | artifact `feature_columns.json` has `features == list(FEATURE_COLUMNS)` (same order) |
| 10 | `signature` | `mlflow.models.get_model_info(<model_uri tag>).signature` exists and its input names `== list(FEATURE_COLUMNS)` |
| 11 | `split_assignment.csv` | artifact parses (`pd.read_csv(path, dtype=str, keep_default_na=False)`: without `keep_default_na=False` an id spelled `NA`/`null` would silently become NaN) to exactly the columns `encounter_id, partition`; `encounter_id` unique; `partition` values within `{"train","test"}`; at least one `"test"` row; every `encounter_id` is present in `gold` |
| 12 | `split consistency` | the artifact agrees with the run's own `test_start_date` param (T = that date at 00:00Z) and with gold (the completeness guard otherwise trusts the artifact blindly): (a) every `"test"` row has `index_start >= T`; (b) every `"train"` row has `index_start < T`; (c) no `patient_id` appears in both partitions. The message always prints all three counts: `N test row(s) start before test_start_date, M train row(s) start on/after it, K patient(s) appear in both partitions`. Verified true on all four real 2b runs (0, 0, 0). |
| 13 | `model provenance` | `mlflow.models.get_model_info(<model_uri tag>).run_id == run_id`: a `model_uri` tag pointing at ANOTHER run's logged model (for example one trained on a different gold table) would otherwise pass guards 3, 6 and 10 while the output records this run's id. Verified: `info.run_id` equals the run id on both real 2b runs. It reuses guard 10's `get_model_info` call (so an `MlflowException` there is guard 10's `signature`, evaluated first). |

Guard 8's tag names are checked against a real trained run in a test, so a rename in `training.py` cannot silently disable the guard. Known limit of guard 8: the Python minor version and the `cloudpickle` version, which also matter for unpickling, are not recorded by training and so not checked (Python is pinned by `.python-version`).

**Uniform rule for absent or malformed run metadata:** any lookup on the run that finds the item absent or unparseable is a refusal carrying that guard's key, never a `KeyError`, `MlflowException`, `json.JSONDecodeError` or `pandas` parse error (the CLI convention would turn those into tracebacks instead of `ERROR:` and exit 1). Concretely: an absent tag (guards 3, 4, 5, 6, 8) or param (7, 12) is a refusal; `download_artifacts` raising `MlflowException`/`OSError` for a missing artifact is caught and converted (guards 9 and 11); `feature_columns.json` that is not JSON, not an object, or lacks a `"features"` list is guard 9; a `split_assignment.csv` that does not parse is guard 11; `get_model_info` raising `MlflowException` is guard 10; a `test_start_date` param that is absent or fails `parse_yyyymmdd` is guard 12 (`split consistency`).

The order of guards 1-13 is the order above, but tests pin only that each guard's defect is refused and that all run before any model load. `validate_run` returns `ModelRun`; `git_commit`/`git_dirty` are informational (shown in the report, not guarded: the user's list of refusals did not include them, and a dirty tree does not invalidate a model, it only makes it unreproducible from a commit; this is flagged in the report line).

### `ranking.py` (pure)

```python
PREDICTION_COLUMNS: tuple[str, ...] = (
    "as_of_date", "rank", "is_top_n", "patient_id", "encounter_id", "discharge_time",
    "admission_reason_code", "admission_reason_description", "risk_score",
    "model_run_id", "model_name", "top_n", "scoring_window_days", "partition",
    "label_definition", "gold_fingerprint", "gold_reference_date",
)

PREDICTION_DTYPES: dict[str, str] = {   # the single source of truth for the pandas dtypes of the predictions frame
    "as_of_date": "object",              # every element is exactly datetime.date (type(v) is date; a datetime is NOT accepted)
    "rank": "int32", "is_top_n": "bool", "discharge_time": "datetime64[us, UTC]", "risk_score": "float64",
    "top_n": "int32", "scoring_window_days": "int32",
    # every other column of PREDICTION_COLUMNS: "object" (str; None allowed only in the two admission_reason columns)
}   # compared as `str(frame[col].dtype)`: a tz-naive "datetime64[us]", a "datetime64[us, Europe/Zurich]" or a float64 rank is wrong

def score_batch(model, batch: pd.DataFrame) -> np.ndarray:
    """ValueError if LABEL_COLUMN is in batch.columns (the scorer must not be handed the label: structural, not by convention).
    scores = model.predict_proba(prepare_features(batch))[:, 1]. Never calls .fit. RuntimeError (a broken model/environment,
    not user input) if predict_proba does not return shape (len(batch), 2), or any score is non-finite, < 0 or > 1."""

def build_predictions(
    batch: pd.DataFrame, scores: np.ndarray, *, run_date: date, top_n: int, window_days: int,
    model_run_id: str, model_name: str, label_definition: str, gold_fingerprint: str, gold_reference_date: str,
) -> pd.DataFrame:
    """batch: label-free rows (ValueError if LABEL_COLUMN present) with at least patient_id, encounter_id, index_stop,
    admission_reason_code, admission_reason_description; scores aligned to batch rows positionally (ValueError on length
    mismatch or an empty batch; top_n must be int >= 1).
    ORDER: sort by risk_score DESCENDING (exact float comparison), ties by encounter_id ASCENDING (Python str order, i.e. code
    point order); rank = 1-based position in that order (int32, always distinct: NO shared ranks); is_top_n = rank <= top_n
    (top_n larger than the batch is not an error: every row is Top-N). Returns exactly PREDICTION_COLUMNS in order, one row per
    batch row, in rank order, RangeIndex. Pandas dtypes (pinned, because the round-trip contract of store.py depends on them):
    as_of_date = object column of `datetime.date` (the run_date argument, on every row); rank int32; is_top_n bool;
    patient_id / encounter_id / model_run_id / model_name / partition / label_definition / gold_fingerprint / gold_reference_date
    object (str); admission_reason_code / admission_reason_description object with None for nulls (a null stays None, never
    the string "None" or NaN); discharge_time = index_stop as datetime64[us, UTC] (`.dt.as_unit("us")`; ValueError if any `index_stop` has a nonzero nanosecond component, because the cast would truncate silently; verified lossless on
    the gold values and required for the round trip); risk_score float64 unrounded; top_n / scoring_window_days int32 constants (the column is `scoring_window_days`, not `window_days`, so a reader cannot confuse it with the 30-day label horizon `readmission_window_days`; it is the W of the trailing window that selected the batch; the `window_days` ARGUMENT of `build_predictions` is written to it);
    partition = "test" (constant in v1, kept so a future in-sample mode is schema-compatible); the provenance columns are the
    arguments verbatim. Input order of `batch` must not matter, and neither `batch` nor `scores` is mutated (tested)."""

@dataclass(frozen=True)
class ObservedSummary:
    n_batch: int; n_positive_batch: int; batch_positive_rate: float
    n_top: int                      # min(top_n, n_batch): the denominator of precision_at_n
    n_positive_top: int; precision_at_n: float
    lift: float | None              # precision_at_n / batch_positive_rate; None when n_positive_batch == 0

def summarize_observed(predictions: pd.DataFrame, labels: pd.Series, *, top_n: int) -> ObservedSummary:
    """labels: 0/1 Series indexed by encounter_id (the gold label), joined to predictions BY encounter_id (never positionally);
    ValueError if any prediction encounter_id has no label. Top-N = rows with rank <= top_n."""
```

Risk-score semantics (documented in the module docstring, the README and the report): the value is the run's model's predicted probability of an unplanned 30-day readmission (calibrated XGBoost: `CalibratedClassifierCV`; logistic regression: natively probabilistic). On the 2023+ window both models under-predict on average (mean ≈ 1.7% vs 2.66% observed), so treat scores as a ranking, not as absolute risk.

### `store.py`

```python
PARTITION_FILENAME = "predictions.parquet"
PREDICTION_SCHEMA: pa.Schema   # pyarrow, explicit; see below
def partition_dir(output_dir: Path, run_date: date) -> Path      # output_dir / f"run_date={run_date:%Y%m%d}"
def write_partition(predictions: pd.DataFrame, output_dir: Path, run_date: date) -> Path
def read_partition(output_dir: Path, run_date: date) -> pd.DataFrame
```

`PREDICTION_SCHEMA` has exactly the fields of `PREDICTION_COLUMNS`, in the same order (`PREDICTION_SCHEMA.names == list(PREDICTION_COLUMNS)`, asserted in test 15): `as_of_date date32`, `rank int32`, `is_top_n bool`, `discharge_time timestamp[us, tz=UTC]`, `risk_score float64`, `top_n int32`, `scoring_window_days int32`, every other column `string`. Field nullability is part of the contract: every field is `nullable=False` EXCEPT `admission_reason_code` and `admission_reason_description` (the only gold columns that can be null among those written). `write_partition` pre-checks and raises a ValueError naming the column and the null count (pyarrow's own error for a null in a non-nullable field is also a ValueError, verified, but its message is not specific). Schema-level key/values: `readmission_risk.demo_notice = DEMO_NOTICE`, `readmission_risk.schema_version = "1"`.

`write_partition`: `ValueError` unless `predictions` has exactly `PREDICTION_COLUMNS` in order, has exactly the pandas dtypes of `ranking.PREDICTION_DTYPES` (each column's `str(dtype)` compared; the message names the column and both dtypes; `pa.Table.from_pandas` does NOT catch these: verified that it silently accepts a tz-naive `datetime64[us]` column for a `timestamp[us, tz=UTC]` field (treating it as UTC), a `tz_convert("Europe/Zurich")` column and a float64 `rank`), every `as_of_date` element is exactly `datetime.date`, is non-empty, has `rank == 1..n` in order, and every `as_of_date` value equals the `run_date` argument (row/partition consistency). Then, in this order: (1) build the Arrow table with `pa.Table.from_pandas(predictions, schema=PREDICTION_SCHEMA, preserve_index=False)` BEFORE touching the filesystem, converting (as a second line of defence behind the dtype pre-check) `pa.ArrowInvalid`, `pa.ArrowTypeError` and `pa.ArrowNotImplementedError` (`ArrowTypeError` is a `TypeError` and would otherwise escape the CLI's `ERROR:` path) into `ValueError("predictions cannot be converted to PREDICTION_SCHEMA: ...")`; (2) `mkdir(parents=True, exist_ok=True)` the partition directory; (3) write it to a UNIQUE dot-prefixed temp file in that directory, named `.predictions.<uuid4 hex>.tmp` and created by `pq.write_table(table, tmp_path, compression="snappy")` under the process umask (NOT `tempfile.mkstemp`, which creates it 0600 and the rename would carry that mode onto the partition), calling `pq.write_table` **through the module attribute** (`import pyarrow.parquet as pq`, so a test can monkeypatch it); `os.replace` the temp file onto `predictions.parquet`, and remove the temp file in a `finally`. A unique name per writer is what makes overlapping runs for one run date safe: each `os.replace` moves a complete file and the last replace wins (a fixed name let the loser corrupt the winner's file; found in pre-commit review). The name is dot-prefixed on purpose: pyarrow's dataset discovery ignores files starting with `.` or `_`, whereas a plain name makes `pd.read_parquet(<output-dir>)` raise `ArrowInvalid` (verified), so a concurrent reader (the future dashboard) or a crash leftover would break directory reads. At rest the directory holds exactly one file; only a hard crash (kill -9, power loss) between the write and the `finally` can leave a stale `.predictions.<hex>.tmp`, which is harmless to readers (dot-prefixed), is never reused, and is deliberately NOT swept at the start of a write (a sweep could delete another live writer's temp file). `read_partition` returns the file via `pd.read_parquet(<file>)` (the file itself, not the directory); `FileNotFoundError` if absent.

Prototype-verified (this session): two writes of the same frame give byte-identical files (sha256 equal); with `discharge_time` cast to `us` and a null admission reason, `pd.testing.assert_frame_equal(read_partition(...), predictions)` passes (without the cast the round trip returns `us` for an `ns` input and fails); `pd.read_parquet(<output-dir>)` over two sibling partitions works (also with a stray dot-prefixed tmp file present) and returns the extra categorical column `run_date` (with the in-file column named `run_date` it raised `ArrowTypeError`, hence `as_of_date`).

### `pipeline.py`

```python
@dataclass(frozen=True)
class ScoringConfig:
    gold_dir: Path
    run_date: str                       # "YYYYMMDD"
    output_dir: Path = Path("data/predictions")
    tracking_dir: Path = Path("mlflow")
    experiment_name: str = "readmission-risk"        # the trainer's default; the real 2b runs need "readmission-risk-2b"
    model_name: str | None = None       # None and run_id None -> DEFAULT_MODEL
    run_id: str | None = None
    top_n: int = DEFAULT_TOP_N
    window_days: int | None = None      # None -> metadata.readmission_window_days
    show_observed_outcomes: bool = False

@dataclass(frozen=True, eq=False)   # holds DataFrames/Series: no generated __eq__/__hash__
class ScoringResult:
    predictions: pd.DataFrame           # exactly what was written
    partition_path: Path                # what the writer returned
    run: ModelRun
    window_start: pd.Timestamp; window_end: pd.Timestamp
    window_days: int                    # resolved
    observed: ObservedSummary | None    # None unless show_observed_outcomes

def score_run_date(config: ScoringConfig, *, loader=load_logged_model, writer=write_partition) -> ScoringResult
```

`loader` and `writer` are injection points (the BigQuery swap replaces `writer`; tests replace both to prove ordering).

`_validate_config(config) -> date` (pure, first; returns the parsed run date): `parse_yyyymmdd(config.run_date, "run_date")`; `type(top_n) is int and 1 <= top_n <= 2**31 - 1` (the column is int32; without the cap `astype("int32")` would silently wrap a huge value negative or raise an OverflowError traceback); `window_days` None or `type is int and >= 1`; `model_name` None or in `MODEL_NAMES`; `run_id` None or a non-empty str; `model_name` and `run_id` both set -> `ValueError("--model and --run-id are mutually exclusive")`.

`score_run_date` steps, all validation strictly before the first side effect (the write):

1. `_validate_config`.
2. `load_gold_table_with_metadata(gold_dir)` (its own checks stand: sidecar present, label definition, counts). `run_date > parse_yyyymmdd(metadata.reference_date)` -> ValueError.
3. `window_days = config.window_days or metadata.readmission_window_days`.
4. Resolve the run id (`--run-id` as is; else `resolve_run_id(tracking_dir, experiment_name, model_name or DEFAULT_MODEL)`), then `validate_run(...)`.
5. `select_held_out_window(gold, run.partition_of, ...)` (the completeness guard; may refuse).
6. **Only now** `model = loader(tracking_dir, run.run_id)`. (cloudpickle deserialization can execute arbitrary code: no run that fails a guard is ever unpickled. The tracking store is trusted local input, exactly as slice 3 documents.)
7. `label_free = batch.drop(columns=[LABEL_COLUMN])`; `scores = score_batch(model, label_free)`; `predictions = build_predictions(label_free, scores, ..., model_run_id=run.run_id, model_name=run.model_name, label_definition=metadata.label_definition, gold_fingerprint=run.gold_fingerprint, gold_reference_date=metadata.reference_date)`. The SAME label-free frame goes to both calls (both refuse a frame that still has the label). Post-conditions (RuntimeError): one row per batch row; the prediction encounter ids equal the batch's.
8. If `show_observed_outcomes`: `observed = summarize_observed(predictions, gold.set_index("encounter_id")[LABEL_COLUMN], top_n=...)`. This is the only place the label is read, and it runs after scoring.
9. `partition_path = writer(predictions, output_dir, run_date)`.

### `report.py`

`format_report(result: ScoringResult) -> str` (pure, plain text, no rich; `top_n` is read from `result.predictions["top_n"]`). Contents, in order: (1) `DEMO_NOTICE`; (2) `model: <model_name> run_id=<id> (trained at commit <first 7 chars or "unknown">, working tree <git_dirty or "unknown">)`; (3) `window: <start> to <end> (end exclusive), <n> held-out discharges scored`, where start/end are `pd.Timestamp.isoformat()` of `window_start`/`window_end` (e.g. `2025-06-01T00:00:00+00:00 to 2025-07-01T00:00:00+00:00`); (4) `Top <min(top_n, n)> of <n> scored discharges:` followed by a header line and one table row per Top-N row **in rank order**: rank, `risk_score` formatted `f"{x:.4f}"`, discharge date formatted `%Y-%m-%d` (UTC), admission reason description truncated to 48 characters (a null prints `(none)`), patient_id, encounter_id, separated by two or more spaces; (5) `Wrote <partition_path>`; (6) only if `result.observed` is not None, a block headed `Observed outcomes (opt-in development-set sanity check; NOT written to the output file; labels are from the gold table, i.e. later than the run date):` with the counts and precision/lift (lift printed `n/a` when `None`) and the sentence `Counts this small are not evidence of model quality.` Exact column widths and spacing are not pinned.

### `scripts/score_discharges.py` (Typer, no logic)

A single command: `app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)`; `@app.command(help=DEMO_NOTICE)`. Options: `--gold-dir` (required), `--run-date` (required, YYYYMMDD), `--output-dir` (`data/predictions`), `--tracking-dir` (`mlflow`), `--experiment-name` (`readmission-risk`; the real store has no such experiment, so a real run needs `--experiment-name readmission-risk-2b` and the error for a missing experiment lists the available names), `--model` (a free `str`, default `None`, NOT an Enum/Choice: an unknown name is a `ValueError` from `_validate_config`, so it exits 1 like every other user error rather than Typer's usage exit 2), `--run-id` (when given, `--experiment-name` is ignored, and `--help` says so), `--top-n` (10), `--window-days`, `--show-observed-outcomes` and `--verbose`, both declared as single-name flags (`typer.Option("--show-observed-outcomes")`, `typer.Option("--verbose")`; no `--no-...` variants). It builds a `ScoringConfig`, calls `score_run_date`, prints `format_report`, and follows the repo's CLI convention (`scripts/train_models.py`): `FileNotFoundError`/`ValueError` -> `ERROR: <message>` on stderr, exit 1 (`--verbose` also prints the traceback); `RuntimeError` and MLflow/OS errors propagate as a traceback; Typer's own usage errors exit 2. The report goes to stdout. MLflow's pickle-safety warning on load is not suppressed.

## UX flow

```
python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --experiment-name readmission-risk-2b --run-date 20250630
  load gold + metadata -> resolve/validate run (all guards) -> select held-out window (completeness guard)
  -> load model -> score (label-free) -> rank -> [opt-in: observed summary] -> write run_date=20250630/predictions.parquet -> print report
```

## Data handling: leakage, split integrity, reproducibility

**Measured facts** (this session, on `data/gold/full_run_2b` and the two logged runs; not assumed):
- Gold 9,284 rows; each run's `split_assignment.csv`: 6,391 train / 1,539 test; the other 1,354 rows appear in neither. Both runs share one test partition. The run tag `gold_fingerprint` equals `gold_fingerprint(load_gold_table(...))` (`c05486...`); computing it takes 0.07 s.
- Test partition: `index_stop` from 2023-01-02 09:59:57Z to 2026-08-17 20:36:33Z; 915 distinct discharge days, mean 1.68 and max 7 discharges per day, about 35 per month. No test row is discharged after the reference date 2026-09-16.
- Non-test gold rows discharged on/after 2023-01-01: exactly 6: five split-boundary stays (`index_start` 2022-12-28 to 2022-12-31, `index_stop` 2023-01-01 to 2023-01-09, purged) and one censoring-buffer row (`index_stop` 2026-09-01T22:17:27Z).
- With W = 30, valid run dates (completeness guard passes, the window ends by the observation horizon, and the batch is non-empty): **1,287** contiguous dates, 20230208 through 20260817, batches of 21-49 rows (median 35). (The v1-v6 design said 1,301 dates through 20260831 with batches of 16-49; that was wrong: see the review-round-1 record below. W = 7 and W = 1 were not re-measured.)
- Real refusals at W = 30: `20230115` -> 0 in-sample and 15 excluded by the split; `20221201` -> 25 in-sample and 7 excluded; `20230207` -> 0 and 1; from `20260818` on (including `20260831`, `20260901` and the reference date `20260916`) the observation-horizon refusal (`run_date must be on or before 2026-08-17`). `20230208` scores 30 rows and `20260817` scores 32.
- Logistic regression scores on the 1,539 test rows: 1,539 distinct values (min 0.0002, median 0.0068, max 0.4524). Calibrated XGBoost: 1,524 distinct (15 duplicated scores; min 0.0102, median 0.0108, max 0.383), so **ties occur on the real data** and the tie-break is behaviour, not theory.
- Synthetic fixture (`make_gold_frame(300, seed=1)`, 652 rows, reference date 2026-09-16, test start 2023-01-01): the split yields 165 train / 310 test / 177 in neither partition; `run_date=20260630` with `window_days=180` holds 50 rows, all test (8 positives); `20221130` (W=30) holds 6 rows (3 train, 3 in neither, 0 test) and `20221215` (W=30) 8 rows (2 train, 6 in neither); `20251112` (W=14) holds 10 test rows and nothing else.
- Both models: `predict_proba` is exactly row-order independent (bit-equal after permuting the input rows) and deterministic across two calls; scrambling the gold `is_readmitted` column leaves the LR scores unchanged (bit-equal). All five library-version tags on the runs equal the installed versions.

**Split integrity (L1).** The rows scored are exactly members of *that model's* held-out partition, read from the run's own `split_assignment.csv` (the artifact is the source of truth; the split is never recomputed at scoring time, so a later change to `split.py` cannot silently move rows). The completeness guard turns any window that includes train rows, dropped rows, or the censored row into a refusal. The fingerprint guard (#6) proves the run was trained on this exact table, so the assignment's encounter ids mean what they say, and guard #12 checks the artifact against the run's own `test_start_date` and gold (test rows start on/after it, train rows before it, no patient in both), so the artifact is not trusted blindly.

**Label (L2).** The scorer never receives the label: `score_batch` refuses a frame with the column, and the pipeline drops it first. The label is read once, after scoring, only if `--show-observed-outcomes`, and never written to the prediction file (the file's columns are exactly `PREDICTION_COLUMNS`).

**No fitting at scoring (L3).** The loaded object is a fitted `Pipeline` / `CalibratedClassifierCV` whose scalers, encoders and calibrators were fitted on training rows only (slice 3). Scoring calls only `predict_proba`.

**Point-in-time features (L4).** The 16 features are computed by slice 2 from records dated strictly before `index_start` (`big_join.py`, `compute_lookback_features`, lines ~248-309: windows `[window_start, index_start)`, `START < index_start`), except `length_of_stay_days`, derived from `index_stop`, which is known at discharge (the scoring moment). This slice does not re-verify that; slice 2's tests own it.

**Reproducibility.** There is no randomness at scoring (no seed exists or is needed). *Determinism claim:* for a fixed gold table (fingerprint), run, run date, window, `top_n` and library versions (guarded), `score_run_date` produces the same predictions frame, in the same order, and, on the same pyarrow, a byte-identical file. It rests on: order-independent `predict_proba`, `load_gold_table` sorting by `(index_start, encounter_id)`, the explicit total order in `build_predictions`, and snappy compression pinned explicitly.

**Known limits of the demo (stated in the README and the report, not fixable here):**
- (a) *Membership is not as-of.* Which gold rows exist depends on whole-export information: the terminal rule (a stay is dropped if a later same-patient stay's stop makes it non-terminal) and the death/censoring keep-rule (slice 3 design note: patients who die within 30 days without a readmission are excluded, so the highest short-term mortality patients are missing). A real as-of list would contain rows this one omits.
- (b) *Simulated run date.* Patients already readmitted before the run date still appear; an as-of system would know that. The same discharge appears in up to W consecutive run-date partitions (windows overlap).
- (c) *Development-set rows, weak model.* The test window is not a virgin holdout; the list mostly ranks by admission reason (real-data first look, LR, `20250630`: ranks 1-6 are CABG-history stays, then NSTEMI, drug-abuse, drug-abuse, NSTEMI; risk_score 0.0816 at rank 1 to 0.0149 at rank 10). Models under-predict on average.
- (e) *The notice text is dataset-specific.* `DEMO_NOTICE` says the rows are a development set that is not a virgin holdout. That is true for `full_run_2b`'s 2023+ window and would be FALSE if this scorer were pointed at the untouched v2 population; the notice must be revisited (or made conditional) when that happens (follow-up 11).
- (f) Guard 8 does not cover the Python or `cloudpickle` versions (see guard 8).
- (d) Multi-run consistency: `--run-id` may name a run from the sensitivity experiment (different training window, same test partition); allowed, because the guards judge the run's content, not its experiment name.

## Failure modes

| Condition | Behaviour |
|---|---|
| gold dir missing / no metadata / label or counts mismatch | existing `load_gold_table_with_metadata` errors (FileNotFoundError / ValueError); CLI exit 1 |
| `tracking_dir/mlflow.db` missing | FileNotFoundError, nothing created (no empty MLflow DB) |
| `--model` and `--run-id` both given, or an unknown model name | ValueError (config), before any I/O |
| experiment missing/deleted; 0 or >1 matching runs | ValueError (`resolve_run_id`); >1 lists the run ids |
| any of guards 1-13 | ValueError naming the guard; no model unpickled, nothing written |
| `partition_of` with a duplicate id or a value outside `{train,test}` | ValueError (`select_held_out_window` step 0) |
| `--experiment-name` names no experiment | ValueError listing the store's available experiment names |
| `run_date` after the gold reference date | ValueError |
| window contains train / dropped / censored rows | ValueError (`N in-sample`, `M discharge(s) excluded by the split`) |
| window ends after the observation horizon (reference date minus the label window; added in pre-commit review round 1) | ValueError (`run_date must be on or before <date>`); checked before the completeness guard |
| window contains no gold row at all | ValueError `no held-out discharges` |
| `top_n` greater than the batch | allowed: all rows `is_top_n`, report says `Top k of k` |
| model returns a bad shape / NaN / out-of-[0,1] | RuntimeError (traceback; environment or model bug) |
| ties in `risk_score` | broken by `encounter_id` ascending; ranks stay distinct |
| write fails midway | previous partition untouched (temp file + `os.replace`); tmp removed |
| two concurrent runs for one run date | not handled (single-writer assumption); each replace is atomic, the last wins |

## Verification criteria

Fixtures are hand-built frames; each was run against the intended implementation and against the named mutants in a throwaway prototype **before** being written here (each mutant fails the fixture that owns its rule; the prototype caught and fixed two weak fixtures: a random label order that let positional alignment pass, and a window fixture that could not see the silent-filter mutant, which is why test 3 owns it). Build synthetic timestamps with `pd.to_datetime(..., utc=True, format="ISO8601")` (mixed-precision strings otherwise raise).

**Test infrastructure** (`tests/scoring/`):
- `__init__.py` exists. `conftest.py` sets `os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")` before importing mlflow and carries an autouse function-scoped fixture equivalent to `tests/models/conftest.py`'s `_restore_mlflow_global_state` (teardown-only: restore the tracking URI, `mlflow.autolog(disable=True)`); the equivalent is duplicated rather than hoisted so slice 3's test infrastructure is not edited (dedupe is a follow-up). Because `validate_run`, `resolve_run_id` and `load_logged_model` set the global tracking URI, the session-scoped `scoring_env` fixture records `mlflow.get_tracking_uri()` before training and restores it right after training and again in its own teardown (a function-scoped autouse fixture would otherwise capture the leaked URI as "original"). `clone_run` and the other helpers never leave the URI changed either.
- `helpers.py::ScoringEnv`: a `@dataclass(frozen=True, eq=False)` with fields `gold_dir: Path`, `tracking_dir: Path`, `experiment_name: str`, `lr_run_id: str`, `xgb_run_id: str`, `df: pd.DataFrame`, `n_train: int`, `n_test: int` (read at fixture time from the LR run's params `split_n_train` / `split_n_test`, converted with `int`; measured 165 / 310, never hard-coded in tests), `expected_batch(run_date: str, window_days: int) -> pd.DataFrame` (the independent boolean-mask batch: in-window AND in the run's test partition, computed from `df` and the source run's assignment). `GuardCase.build` receives it. `conftest.py` provides it through the `scoring_env` fixture.
- `helpers.py::assert_count_phrase(message, n, phrase)`: asserts with `re.search(rf"(?<![0-9]){n} {re.escape(phrase)}", message)`, so `"3 in-sample"` cannot be satisfied by `"13 in-sample"`. Every count assertion in tests 3, 4, 21 and 30 uses it (for #12 the whole `N test row(s) ..., M train row(s) ..., K patient(s) ...` triple sentence is compared, not fragments).
- `helpers.py::forbid_model_loading(monkeypatch)`: patches `mlflow.sklearn.load_model`, `mlflow.pyfunc.load_model`, `cloudpickle.load` and `cloudpickle.loads` to raise `AssertionError`; test 21 applies it ONLY around the `score_run_date` call (the clone construction in `build` legitimately loads a model), so a `validate_run` that itself unpickles the model, not just one that calls the injected `loader`, fails.
- `scoring_env` (**session-scoped**, built with `tmp_path_factory`, so the slice-3 training runs once for the whole `tests/scoring` package rather than once per module): `df = make_gold_frame(300, seed=1)` (652 rows) written to `gold_dir` as two Parquet part files with `write_test_gold_metadata` (defaults: `reference_date="20260916"`, `readmission_window_days=30`); `train_and_evaluate(TrainingConfig(gold_dir=gold_dir, reference_date=REFERENCE_DATE_STR, test_start_date=TEST_START_STR, tracking_dir=tracking_dir, experiment_name="scoring-test", n_bootstrap=0))` with every other field at its default (same helpers/constants as `tests/models/test_cli.py`); it exposes `gold_dir`, `tracking_dir`, `experiment_name`, `lr_run_id`, `xgb_run_id`, the gold frame `df`. Its default scoring call is `run_date="20260630"`, `window_days=180` (measured on this fixture: 50 rows, 8 positives; tests recompute the expected batch with a plain boolean mask and assert the precondition `>= 20` rows).
- `helpers.py::clone_run(tracking_dir, source_run_id, *, experiment: str, gold_frame: pd.DataFrame, tags: dict[str, str] | None = None, params: dict[str, str | None] | None = None, features_json: dict | str | None = None, split_assignment_csv: str | None = None, signature_columns: Sequence[str] | None = None, no_signature: bool = False, status: str = "FINISHED", drop_tags: Sequence[str] = (), drop_artifacts: Sequence[str] = ()) -> str` returns the new run id. It creates `experiment` if absent with `MlflowClient.create_experiment(experiment, artifact_location=(Path(tracking_dir).resolve() / "artifacts").as_uri())` (without an explicit location a SQLite store defaults to `./mlruns` under the CWD, which would litter the repo; the real store's `Default` experiment shows exactly that), then a run that: copies the source's params and non-`mlflow.*` tags, then sets its OWN `model_uri` tag (after re-logging the model, below) and only THEN applies caller-supplied `params`/`tags` (replace) and `drop_tags` (remove), so a caller-supplied `tags={"model_uri": ...}` wins over the clone's own tag (guard case #10's bad-`model_uri` variant depends on this) and `drop_tags=("model_uri",)` removes it; every override argument is tested with `is not None`, never truthiness, so `split_assignment_csv=""` and `features_json=""` really plant an EMPTY file rather than silently copying the source's; logs `feature_columns.json` (`features_json`: a dict is JSON-dumped, a str is written verbatim so invalid JSON can be planted; else the source's) and `split_assignment.csv` (`split_assignment_csv` text if given, else the source's) unless named in `drop_artifacts`; loads the source's fitted model with `load_logged_model` and re-logs it via `mlflow.sklearn.log_model(..., serialization_format="cloudpickle")` with a signature inferred from `prepare_features(gold_frame)[list(signature_columns)]` (`gold_frame` is the fixture frame, used only for signature inference) (`signature_columns=None` means all `FEATURE_COLUMNS` in order; `no_signature=True` logs with no signature at all), and ends the run with `status`. MLflow params cannot be edited after the fact, which is why tampering uses clones. Tampering tests put clones in an experiment of their own name (never `scoring-test`), so name-based resolution on `scoring-test` stays pristine.
- `helpers.py::GUARD_CASES`: a list of `GuardCase(case_id: str, guard_key: str, build: Callable[[ScoringEnv, str], tuple[str, tuple[str, ...]]], forbidden: tuple[str, ...] = ())`, one per case below, shared by test 21. `build(env, experiment)` performs whatever the case needs (a clone, a soft-delete after cloning, a bogus id, an artifact text derived from the source run's `split_assignment.csv`, or a data-dependent relabelling computed from `env.df`) and returns `(run_id_to_pass, extra_expected_substrings)`; the extras carry data-dependent expectations such as the #12 count triple. The test asserts the message contains `guard_key` and every extra and contains none of `forbidden`.

Guard-by-guard defects (each case breaks exactly one guard; 38 cases in `GUARD_CASES`, the sum of the per-guard counts below): #1 `run_id="0"*32`; #2 a clone with `status="FAILED"` (key `not FINISHED`) and a clone soft-deleted with `MlflowClient.delete_run` after cloning (key `deleted`); #3 `drop_tags=("model_uri",)`; #4 `tags={"model_name": "random_forest"}` and separately `drop_tags=("model_name",)`; #5 `tags={"gold_label_definition": "all_cause_v0"}` and separately dropped; #6 `tags={"gold_fingerprint": "0"*64}` and separately dropped; #7 four cases, each with ITS OWN key and a `forbidden` key (guard 7's message names only the offending param: `reference_date` mismatch or absence must not mention `readmission_window_days`, and vice versa): `params={"reference_date": "20260917"}` (key `reference_date`, forbidden `readmission_window_days`), `params={"readmission_window_days": "14"}` (key `readmission_window_days`, forbidden `reference_date`), and each of the two params separately absent (same key/forbidden pairs) (params cannot be removed from a run, so the clone simply does not copy them: `clone_run` treats `params={"x": None}` as "do not copy"); #8 `tags={"sklearn_version": "0.0.0"}` and separately `drop_tags=("numpy_version",)`; #9 `features_json={"features": <FEATURE_COLUMNS reversed>}`, the list with one extra name appended, `features_json='{"nofeatures": 1}'`, `features_json="not json"`, `features_json='{"features": "abc"}'` (not a list), and `drop_artifacts=("feature_columns.json",)`; #10 `signature_columns=<FEATURE_COLUMNS reversed>`, `no_signature=True`, and `tags={"model_uri": "models:/m-doesnotexist"}` (`get_model_info` raises `MlflowException`, converted to key `signature`); #11 `split_assignment_csv` variants derived from the source run's CSV: wrong header, duplicate `encounter_id`, a `partition` value `"validation"`, no `"test"` row, an `encounter_id` absent from the gold table, an empty file (`split_assignment_csv=""`, which `pd.read_csv` cannot parse), plus `drop_artifacts=("split_assignment.csv",)`; #12 five variants: three relabellings and two param defects. The relabellings are computed from the fixture (`env.df` and the source run's assignment; measured on the fixture: 165 train / 310 test / 177 in neither): (i) the FIRST train row by `encounter_id` whose patient has at least two train rows (`e0000000`, patient with 3 train rows) relabelled `test`; (ii) the first test row whose patient has at least two test rows (`e0000008`) relabelled `train`; (iii) a gold row in neither partition, belonging to a patient who has a test row, with `index_start < T` (172 such rows exist), added as `train`. For each, the case's `build` recomputes the three sub-rule counts with plain pandas on the modified assignment (independent of the implementation) and returns the expected message triple as an extra substring; it also asserts the intended pattern by construction: (i) first count 1, (ii) second count 1, (iii) counts `0, 0, k` with `k >= 1`. The exact triples pin each sub-rule (a mutant that omits the patient-disjointness check fails (iii); one that omits (a) or (b) fails (i)/(ii)). The param defects: `drop` of `test_start_date` and `params={"test_start_date": "not-a-date"}`; both are refusals with key `split consistency`. #13 one case: a clone with `tags={"model_uri": <the SOURCE run's model_uri tag>}` (a valid, loadable logged model whose `run_id` is the source run, not the clone; key `model provenance`, forbidden `signature`).

**`test_selection.py`**
1. `test_window_bounds_and_row_boundaries` — `run_date=2026-03-10, window_days=3` gives `[2026-03-08T00:00Z, 2026-03-11T00:00Z)`. Gold rows (`index_stop`; `index_start`; partition): e6 `03-08T00:00:00Z`; `03-06`; test (listed FIRST in the input frame) — e1 `03-07T23:59:59Z`; `03-05`; train — e2 `03-08T00:00:00Z`; `03-06`; test — e3 `03-10T23:59:59.999999Z`; `03-10T08:00`; test — e4 `03-11T00:00:00Z`; `03-09`; absent from the assignment — e5 `03-09T12:00:00Z`; `03-01`; test. Expected result ids `["e2","e6","e5","e3"]` (sorted by `index_stop`, the equal-`index_stop` pair e2/e6 by `encounter_id`; verified in the prototype). Kills: start off by one day (`W` instead of `W-1`: includes e1), end inclusive (includes e4), filtering on `index_start` (drops e2/e6/e5, includes e4), sorting by `index_stop` alone with a stable sort (`[e6, e2, e5, e3]`). (e1 and e4 are deliberately unscored yet just outside the window: it also pins that out-of-window rows never trigger the guard.)
2. `test_window_bounds_rejects_bad_window_days` — 0, -1, `True`, `2.0` -> ValueError; `run_date=0001-01-01` with `window_days=30`, `window_days=10**9`, and `run_date=9999-12-31` each raise ValueError (not OverflowError).
2b. `test_select_refuses_tz_naive_index_stop` — `select_held_out_window` on a gold frame with tz-naive `index_stop` (and a valid `partition_of`) -> ValueError; a frame missing `encounter_id` or `index_stop` -> ValueError.
3. `test_completeness_guard_refuses_unscored_in_window_rows` — gold t1/t2/t3 with `index_stop` `03-10T00:00`, `01:00`, `02:00`, `run_date=2026-03-10, window_days=1`; partition `{t1: test, t2: train}` (t3 absent): ValueError containing `1 in-sample` and `1 discharge(s) excluded by the split`. Kills the silent-filter mutant (returns `[t1]`).
4. `test_completeness_guard_counts_all_train_window` — same gold, all three `train`: message contains `3 in-sample` and `0 discharge(s) excluded` (the in-sample error, not the empty-batch error, wins).
5. `test_empty_window_refused` — a window with no gold rows: ValueError starting `no held-out discharges`.
5b. `test_partition_of_with_unknown_value_or_duplicate_index_refused` — `partition_of` containing the value `"validation"` (even for an out-of-window row), or a duplicated `encounter_id` index: ValueError (the pure function does not rely on guard 11 having run).

**`test_ranking.py`**
6. `test_ties_break_by_encounter_id_ascending_with_distinct_ranks` — batch rows in input order `(e,0.2) (c,0.5) (a,0.5) (b,0.5) (d,0.1)`, `top_n=3`: ids `[a,b,c,e,d]`, ranks `[1,2,3,4,5]`, `is_top_n [T,T,T,F,F]`. Kills: input-order (stable) tie-break (`[c,a,b,...]`), `encounter_id` descending (`[c,b,a,...]`), ascending score (`[d,e,a,b,c]`), competition ranking (`[1,1,1,4,5]`).
7. `test_ranking_independent_of_input_order` — the same batch shuffled (fixed permutation) gives a frame `equals` to test 6's.
8. `test_top_n_larger_than_batch_marks_every_row` — `top_n=5` and `top_n=7` on the 5-row batch: all `is_top_n`, no error; `top_n=0`, `-1`, `True` -> ValueError.
9. `test_build_predictions_columns_dtypes_and_constants` — columns exactly `PREDICTION_COLUMNS`; dtypes exactly `ranking.PREDICTION_DTYPES` (asserted as `{c: str(df[c].dtype)}` against it for every column, the unlisted ones being `object`), i.e. as pinned in the `build_predictions` docstring (`as_of_date` object of `datetime.date`, `rank` int32, `is_top_n` bool, `discharge_time` `datetime64[us, UTC]` even when the input `index_stop` is `ns`, `risk_score` float64, `top_n`/`scoring_window_days` int32, the rest object); a null `admission_reason_description` stays `None`; an `index_stop` with a nanosecond component -> ValueError; constants (`as_of_date`, `model_run_id`, `partition == "test"`, `top_n`, `scoring_window_days`, provenance) verbatim on every row; no `is_readmitted` column.
10. `test_scorer_refuses_a_frame_carrying_the_label` — `score_batch` and `build_predictions` raise ValueError when `is_readmitted` is a column. `build_predictions` also raises ValueError for a `scores` array whose length differs from the batch (both longer and shorter) and for an empty batch.
10b. `test_pure_functions_do_not_mutate_their_inputs` — for `select_held_out_window`, `score_batch`, `build_predictions`, `summarize_observed`: `copy.deepcopy` of every input frame/Series/array taken before the call is `assert_frame_equal`/`assert_series_equal`/`array_equal` to the input after the call (column order, dtypes and index included; a sort or `drop` done in place would fail).
11. `test_score_batch_never_fits` — a stub model whose `fit` raises `AssertionError` and whose `predict_proba` returns fixed probabilities: `score_batch` returns them (`[:, 1]`).
12. `test_score_batch_rejects_broken_model_output` — stub returning shape `(n, 1)`, or a NaN, or 1.2, or -0.1 -> RuntimeError.
13. `test_summarize_observed_hand_computed` — ranks a..e as in test 6, labels given as a Series in the deliberately non-rank order `d:1, e:0, a:1, b:0, c:0`, `top_n=3`: `n_batch=5, n_positive_batch=2, n_top=3, n_positive_top=1, precision_at_n=1/3, batch_positive_rate=0.4, lift=0.8333…`. Kills positional label alignment (Top-3 positives 2) and counting batch positives (2).
14. `test_summarize_observed_top_n_exceeds_batch_and_zero_positives` — `top_n=10` on the same data: `n_top=5, n_positive_top=2, precision_at_n=0.4, lift=1.0` (kills a denominator of N: 0.2); all-zero labels: `lift is None`, `n_positive_top=0`; a prediction id with no label -> ValueError.

**`test_store.py`**
15. `test_partition_round_trip_and_layout` — `write_partition` returns `<out>/run_date=20260630/predictions.parquet`; the directory holds exactly that one file; `assert_frame_equal(read_partition(...), predictions)` on a frame that includes a row with a null admission reason; `PREDICTION_SCHEMA.names == list(PREDICTION_COLUMNS)`; `pq.read_schema(file).remove_metadata()` equals `PREDICTION_SCHEMA.remove_metadata()` (names, order, types, nullability; `remove_metadata` because `from_pandas` adds a `pandas` metadata key, which is allowed), and the file's schema metadata contains `readmission_risk.demo_notice == DEMO_NOTICE` and `readmission_risk.schema_version == "1"`.
15a. `test_read_partition_missing_file_raises_file_not_found` — `read_partition` on an output dir with no such partition -> FileNotFoundError.
15b. `test_parent_directory_reads_as_hive_partitions` — two partitions (run dates 20260630 and 20260701), the first with a stray dot-prefixed temp file (`.predictions.<hex>.tmp`) beside its file, written under one output dir: `pd.read_parquet(output_dir)` succeeds, has both partitions' rows, and its `run_date` partition column (categorical with `int32` categories, verified) agrees with `as_of_date` row by row: `parent["run_date"].astype(str)` equals `parent["as_of_date"].map(lambda d: d.strftime("%Y%m%d"))`. Kills an in-file column named `run_date` (`ArrowTypeError`, prototype-verified).
16. `test_rewrite_replaces_the_whole_partition_and_is_byte_stable` — write a 5-row frame, then a 3-row frame with different `top_n`/`model_run_id`: the file has 3 rows and none of the first frame's values; writing the same frame twice gives an identical sha256.
17. `test_write_partition_validates_before_touching_disk` — wrong columns, empty frame, ranks not `1..n`, or an `as_of_date` value differing from the `run_date` argument -> ValueError; a null `patient_id` (non-nullable) -> ValueError naming the column; each of a tz-naive `discharge_time` (`datetime64[us]`), a `discharge_time` converted to another timezone, a float64 `rank`, a string-typed `risk_score`, and an `as_of_date` holding `datetime.datetime` objects -> ValueError naming the column, and the output dir is not created (all checks precede `mkdir`); an existing partition's sha256 is unchanged and no temp file remains; the output dir is not created when it did not exist.
18. `test_failed_write_leaves_previous_partition_intact` — monkeypatch `pq.write_table` (the module attribute `store.py` calls) with a stub that first writes a few partial bytes to the path it is given (so the dot-prefixed tmp file really exists) and then raises `OSError`: the `OSError` propagates, the previous `predictions.parquet` is byte-identical, and no temp file is left (kills a missing `finally`); the path handed to `pq.write_table` is dot-prefixed and in the partition directory.

**`test_runs.py`** (uses `scoring_env`)
19. `test_installed_versions_keys_match_training_tags` — every key of `installed_versions()` is a tag on a real trained run and its value equals the tag.
20. `test_valid_run_passes_all_guards` — for both runs, `validate_run` returns a `ModelRun` whose `partition_of` counts equal the split summary (`n_train`, `n_test`), `model_name` right.
21. `test_each_guard_refuses_exactly_its_defect` — parametrized over `GUARD_CASES` (every case listed above, 38), each run through `score_run_date` with `--run-id` = the clone (or the bogus id for #1) and recording fakes for `loader` and `writer`: `ValueError` whose message contains the guard's key (for #12 the exact three counts), `loader` and `writer` never called, `forbid_model_loading` never triggered, output dir not created. Running through `score_run_date` rather than `validate_run` is what pins "all guards run before any model load" for every guard, and `forbid_model_loading` closes the hole of a `validate_run` that unpickles the model itself. Guards #6 and #7 are triggered only by cloned-run tags/params (the gold table and metadata are never altered for this test).
22. `test_resolve_run_id_unique_and_ambiguous` — the pristine `scoring-test` experiment resolves each model name to its own run id; an experiment with two FINISHED cloned LR runs raises a ValueError listing both ids (kills "latest wins"); an experiment holding one FINISHED and one FAILED LR clone resolves to the FINISHED one (kills a dropped status filter); an experiment with one soft-deleted LR clone and no other raises `no FINISHED run`; an experiment with no XGBoost run raises `no FINISHED run`; a deleted experiment (`MlflowClient.delete_experiment`) raises a ValueError containing `deleted`; an unknown model name raises ValueError.
23. `test_missing_tracking_db_is_refused_without_creating_it` — `tracking_dir` absent or an existing directory without `mlflow.db`: FileNotFoundError from both `resolve_run_id` and `validate_run`; nothing is created (no directory, no `mlflow.db`).
23b. `test_missing_experiment_error_lists_available_names` — `resolve_run_id(tracking_dir, "readmission-risk", "logistic_regression")` on the fixture store raises a ValueError whose message contains `not found` and `scoring-test`.

**`test_pipeline.py`** (uses `scoring_env`)
24. `test_end_to_end_scores_only_the_held_out_batch` — the output's encounter ids equal, as a set, the ids of an independent boolean-mask batch (in-window AND in the run's test partition, computed from the fixture frame and the run's `split_assignment.csv`); `model_run_id` equals the resolved run; the mapping `{encounter_id: risk_score}` equals, id by id (never positionally), the mapping from `load_logged_model(...).predict_proba(prepare_features(expected_batch))[:, 1]` recomputed in the test (kills a misaligned score/row join after sorting); ranks follow the total order; the file reads back equal to `result.predictions`. The written file's provenance columns are asserted independently of the pipeline: `gold_fingerprint == gold_fingerprint(load_gold_table(gold_dir))`, `gold_reference_date == "20260916"`, `label_definition == LABEL_DEFINITION`, `model_name ==` the run's `model_name` tag (`logistic_regression`), `partition == "test"`, `as_of_date` equal to `date(2026, 6, 30)` on every row (kills swapped same-typed `str` arguments such as `gold_reference_date=str(run_date)`); and `result.window_start == pd.Timestamp("2026-01-02T00:00:00Z")` and `result.window_end == pd.Timestamp("2026-07-01T00:00:00Z")` (180 days ending 2026-06-30, end exclusive). The call uses a NON-default `top_n=7`: `(predictions.top_n == 7).all()` and `predictions.is_top_n.sum() == min(7, n)` and the `is_top_n` rows are exactly ranks 1-7 (kills a pipeline that passes `DEFAULT_TOP_N` to `build_predictions`).
25. `test_label_does_not_reach_ranking` — write a second gold directory B from the fixture frame with `is_readmitted` permuted by a fixed `np.random.default_rng(1).permutation` (same rows, same metadata counts, so it loads; asserted to differ from A in at least one in-window label) and clone the LR run with `tags={"gold_fingerprint": gold_fingerprint(load_gold_table(B))}` so guard #6 passes for B. Scoring A with the original run and B with the clone gives predictions whose columns `encounter_id, rank, is_top_n, risk_score` are `assert_frame_equal` (the provenance columns `model_run_id` and `gold_fingerprint` differ by construction and are excluded). Scope, stated honestly: `prepare_features` ignores the label, so scores cannot depend on it in any case, and the LR scores on this fixture are distinct (a label-only tie-break would be invisible here; tie-breaking is pinned by the pure test 6, and a label reaching `build_predictions` is refused by test 10). This test kills an implementation in which the label reaches the sort key or the rank assignment through some other path; the structural refusal (test 10) is what protects the score path.
26. `test_default_model_and_explicit_run_id` — no model flags: the LR run is used; `model_name="xgboost_calibrated"` uses the XGBoost run; `run_id=<xgb id>` gives an identical frame to `model_name="xgboost_calibrated"`; both flags given -> ValueError. Also, with a recording `loader` wrapper around the real one: it is called with `run.run_id` of the resolved run (kills a mutant that resolves the XGBoost run for provenance but loads the LR run), and the XGBoost run's `{encounter_id: risk_score}` equals, id by id, `load_logged_model(tracking_dir, xgb_id).predict_proba(prepare_features(expected_batch))[:, 1]` and differs from the LR mapping in at least one score.
26b. `test_default_window_days_comes_from_gold_metadata` — a gold directory C with the fixture rows and metadata `readmission_window_days=14` (`write_test_gold_metadata(..., readmission_window_days=14)`) and a clone of the LR run with `params={"readmission_window_days": "14"}` (guard #7); `run_date="20251112"`, whose 14-day window holds 10 held-out rows and no train/dropped rows on the fixture (measured; the test asserts the precondition, `>= 5` rows): `window_days=None` yields `scoring_window_days == 14` on every row, `window_end - window_start == 14 days` and the expected batch of that window; an explicit `window_days=180` on the same call overrides it (kills a hard-coded 30 or a metadata-ignoring default).
26c. `test_invalid_config_is_refused_before_any_io` — two parts. (a) `pipeline._validate_config` called directly: returns the parsed `date` for a valid config; raises ValueError for each of `run_date="2026-06-30"`, `"20260231"`, `""`; `top_n=0`, `-1`, `True`, `2.5`, `2**31`, `3_000_000_000`; `window_days=0`, `True`, `1.5`; `model_name="random_forest"`; `run_id=""`; `model_name` and `run_id` both set (message contains `mutually exclusive`); and does NOT raise when `run_id` is given together with a non-default `experiment_name` (the experiment name is then ignored, as `--help` says). (b) `score_run_date` with a NONEXISTENT `gold_dir` and recording `loader`/`writer`: each of the bad configs above raises **ValueError** (a FileNotFoundError would show that gold was read first), and neither fake is called.
27b. `test_pipeline_postconditions_raise_runtime_error` — `pipeline.py` binds `build_predictions` by name (`from .ranking import build_predictions, score_batch, summarize_observed`), so the test monkeypatches `pipeline.build_predictions` with a wrapper that (i) drops one row, and separately (ii) swaps one `encounter_id` for an unknown id: `score_run_date` raises RuntimeError (not ValueError) in both, and the writer is not called (kills a pipeline that omits step 7's post-conditions).
27. `test_refusals_happen_before_load_and_before_write` — for a guard failure, an in-sample window, and `run_date` after the reference date: `loader` and `writer` (recording fakes) are never called and the output dir does not exist. For the passing config the two injected callables are PASS-THROUGH recorders wrapping the real `load_logged_model` and `write_partition` (the model must really predict and the partition must really be written): each is called exactly once, `loader` before `writer`, `loader` with `(tracking_dir, run.run_id)`, `writer` with the same `predictions` frame that ends up in `result.predictions`; `result.partition_path` is what the writer returned. A second passing call whose `writer` is a stub returning a sentinel `Path` (and writing nothing) shows the swap seam: `result.partition_path` is the sentinel and the output dir was not created by the pipeline itself.
28. `test_idempotent_rerun_and_replacement` — two identical runs: `assert_frame_equal` and identical sha256; a rerun of the same run date with `model_name="xgboost_calibrated"` and `top_n=3` replaces the partition entirely: the file's `model_run_id` is the XGBoost run everywhere and `model_name == "xgboost_calibrated"`, `top_n == 3` on every row and `is_top_n.sum() == 3`; a second run date creates an independent sibling partition; a refused rerun (tampered `--run-id`) leaves the existing partition's sha256 unchanged.
29. `test_observed_outcomes_are_opt_in_and_never_persisted` — default `observed is None`; with the flag and a non-default `top_n=4`, `observed` equals a fully independent recomputation in the test (score mapping recomputed from `load_logged_model(...).predict_proba(prepare_features(expected_batch))`, ordered by score descending then `encounter_id` ascending, labels read from the fixture frame): `n_batch`, `n_positive_batch`, `n_top == 4`, `n_positive_top` (the positives among the first 4 of that independent order) and `precision_at_n` all equal (kills a pipeline that passes the default `top_n` to `summarize_observed`, and avoids the self-referential comparison); in both cases the written file's columns equal `PREDICTION_COLUMNS` (no label or observed column).
30. `test_pipeline_refuses_a_natural_in_sample_window` — on `scoring_env` with the original LR run, `run_date="20221130"`, `window_days=30`: measured on the fixture, that window holds 6 gold rows, 3 in the run's train partition and 3 in neither (0 test), so `score_run_date` raises ValueError containing `3 in-sample` and `3 discharge(s) excluded by the split`; `run_date="20221215"` gives `2 in-sample` and `6 discharge(s) excluded`; `loader` and `writer` are not called. This mirrors the real `20221201` refusal, and it is the test that shows the pipeline actually consults `partition_of` (every in-window row of the default fixture call is a test row, so test 24 alone could not tell a pipeline that ignores the partition from a correct one). (A tampered assignment that marks one in-window test row `train` is refused by guard 12 as `split consistency`, before the completeness guard: that is case #12(ii), not this test.)
30b. `test_output_independent_of_gold_row_order_and_part_files` — write the same fixture frame a second time with its rows shuffled (fixed permutation) and split across 3 Parquet part files instead of 2, with the same metadata: predictions frames equal (`assert_frame_equal`) to those from the default gold directory (the fingerprint sorts by `encounter_id`, so the original run is still accepted). Pins the determinism claim's dependence on the loader's sort.
31. `test_scoring_modules_do_not_import_the_big_join` — importing every `readmission_risk.scoring.*` module in a subprocess leaves `readmission_risk.pipeline.big_join` out of `sys.modules` (see the note under Surfaces on why the assertion is not about `pyspark` itself).

**`test_report.py`**
32. `test_report_contents_and_order` — fixture: a 5-row result with `top_n=3`, distinctive encounter ids `enc-0001` ... `enc-0005` (rank order `enc-0003, enc-0001, enc-0005, enc-0002, enc-0004`) and patient ids `pat-0001`...; window `2026-03-08T00:00Z` to `2026-03-11T00:00Z`; the rank-1 row's description is `"d" * 48 + "TAILTAIL"` (56 chars) and the rank-2 row's is null; the `ModelRun` has a 40-char `git_commit` and `git_dirty="clean"`. Assertions are made on the report split into lines, and on the Top-N table lines only (the lines between the `Top 3 of 5 scored discharges:` heading and the `Wrote` line, minus the header): the text starts with `DEMO_NOTICE`; contains the run line `model: <model_name> run_id=<id> (trained at commit <first 7 chars>, working tree clean)`; contains exactly `window: 2026-03-08T00:00:00+00:00 to 2026-03-11T00:00:00+00:00 (end exclusive), 5 held-out discharges scored` and `Top 3 of 5 scored discharges`; the table's data lines are defined as the lines strictly between the `Top 3 of 5 scored discharges:` heading and the `Wrote` line THAT CONTAIN the substring `enc-` (the header line has none; blank or ruled lines are ignored, so layout whitespace is not pinned): there are exactly 3, their ids appear in rank order `enc-0003, enc-0001, enc-0005`, and `enc-0002` / `enc-0004` appear nowhere in the report; each data line carries `f"{risk:.4f}"` and a `YYYY-MM-DD` discharge date; rank 1's line (description `"d"*48 + "TAILTAIL"`) contains `"d"*48` and contains NONE of `"d"*48 + "T"`, `"d"*48 + "..."`, `"d"*48 + "\u2026"` (kills truncation at 49-51 characters and an appended ellipsis); rank 3's line (description exactly `"e"*48`, so the fixture's rank-3 row has that description) contains all 48 `e`s (kills truncation at 47); rank 2's line contains `(none)`; no `Observed outcomes` when `observed is None`; with an `ObservedSummary` the block appears, prints `n/a` for `lift=None`, and contains the sentence about small counts. A second call with `ModelRun(git_commit=None, git_dirty=None)` prints `trained at commit unknown, working tree unknown` (kills a `None[:7]` crash).

**`test_cli.py`** (script loaded with `importlib` as in `tests/models/test_cli.py`; `typer.testing.CliRunner`; prototype-verified that `result.stderr` and `result.stdout` are separate and exit codes are 0 / 1 / 2)
33. `test_cli_maps_every_flag_to_config` — the script must bind `score_run_date` and `format_report` by name in its own namespace (`from readmission_risk.scoring.pipeline import ScoringConfig, score_run_date`; `from readmission_risk.scoring.report import format_report`) and expose the Typer object as `app`, so the test can `monkeypatch.setattr(module, "score_run_date", fake)`. The fake records the `ScoringConfig` and returns a minimal REAL `ScoringResult` built by a `helpers.py` builder (a 3-row predictions frame, a `ModelRun`, window bounds, `observed=None`), so the script's `format_report` call and printing run unmodified; invoke with distinct values for every option (`--top-n 3 --window-days 45 --model xgboost_calibrated --experiment-name x ...`): each field equals its flag (a swap of `top_n`/`window_days` fails); `--run-id` alone maps `model_name=None`. A second invocation with only the two required flags asserts every default: `output_dir == Path("data/predictions")`, `tracking_dir == Path("mlflow")`, `experiment_name == "readmission-risk"`, `top_n == 10`, `window_days is None`, `model_name is None`, `run_id is None`, `show_observed_outcomes is False` (kills a wrong CLI default).
34. `test_cli_end_to_end_prints_report_and_writes_partition` — real invocation on `scoring_env` with `--gold-dir <gold_dir> --tracking-dir <tracking_dir> --experiment-name scoring-test --run-date 20260630 --window-days 180 --top-n 3 --output-dir <tmp>`: exit 0; stdout has the notice, the run line, `Top 3 of`; `predictions.parquet` exists.
35. `test_cli_reports_user_errors_with_exit_1` — nonexistent gold dir, in-sample window, `--model` with `--run-id`, `--model random_forest` (an unknown name is exit 1, not Typer's usage exit 2): exit 1, `ERROR:` on stderr, nothing on stdout, no output directory; `--verbose` adds `Traceback`; a missing required option exits 2.
36. `test_cli_help_shows_the_notice` — `--help` exits 0 and the output with ALL whitespace removed (`re.sub(r"\s+", "", text)`) contains `DEMO_NOTICE` with all whitespace removed. (Corrected in v4: a whitespace-*normalised* comparison FAILS on the real notice, because Click wraps `development-set` after the hyphen, giving `development- set`; verified at `COLUMNS` 60, 80 and 200, where all-whitespace removal passes every time. Do not reword the notice to avoid the hyphen instead: the comparison must not depend on wrap positions.)
37. `test_cli_observed_flag` — without `--show-observed-outcomes` stdout has no `Observed outcomes`; with it, it does; either way the file has no label column.

**Manual real-data gate (run by hand, results recorded in the final report; not CI, it needs the local store).** Common flags: `--gold-dir data/gold/full_run_2b --experiment-name readmission-risk-2b`; run ids are `LR2b = 2bb281f6ab4e47c588eea078cc712201`, `XGB2b = 681107e057f84c7f9fea53883b913542` (experiment `readmission-risk-2b`), `LRsens = d2a73ccf711548028a413f030c8a2be0`, `XGBsens = bd2e30b396dd49eb850e86c59f33dfb3` (experiment `readmission-risk-2b-sensitivity`, trained with `--train-start-date 20170916`, same test partition). Expected values were measured with the throwaway prototype for `--run-date 20250630` (W=30, N=10): every run scores **42** held-out discharges with no tied scores in the batch, and the batch holds **1** observed positive, which each run places in its Top-10 (precision@10 0.10, lift 4.2; printed with the small-counts sentence).

| Run | rank-1 risk_score | rank-10 risk_score | Top-10 admission reasons |
|---|---|---|---|
| LR2b (default, no model flag) | 0.0816 | 0.0149 | CABG-history 6, NSTEMI 2, dependent drug abuse 2 |
| XGB2b (`--model xgboost_calibrated`) | 0.1074 | 0.0132 | CABG-history 6, NSTEMI 2, patient referral for dental care 2 |
| LRsens (`--run-id`) | 0.1003 | 0.0302 | CABG-history 6, NSTEMI 3, dental referral 1 |
| XGBsens (`--run-id`) | 0.0547 | 0.0167 | CABG-history 5, drug abuse 2, dental referral 1, STEMI 1, NSTEMI 1 |

Checks: (1) the four runs above produce those numbers (scores to 4 decimals); `--run-id 2bb281f6ab4e47c588eea078cc712201` gives a file identical to the default. (2) Refusals, all exit 1 with `ERROR:` on stderr and no partition written: run dates `20230115` (0 in-sample, 15 excluded by the split), `20221201` (25, 7), `20230207` (0, 1), and, since review round 1, `20260818`, `20260831`, `20260901` and `20260916` (the observation-horizon refusal, `run_date must be on or before 2026-08-17`); `--run-id 227042d020a24b2784650548c5d59f5e` (a pre-2b all-cause run, no `gold_label_definition` tag) refuses on `gold_label_definition`, as does `bcfbfd43f69042efb545964e64c187f3`; `--model logistic_regression --run-id ...` refuses as mutually exclusive; an experiment name that does not exist lists the available ones. (3) Boundaries succeed: `20230208` (30 rows) and, since review round 1, `20260817` (32 rows; `20260831` used to be listed here and scored a silently truncated 16). (4) Idempotency: run twice, `sha256` of `predictions.parquet` equal; re-run with `--top-n 5`: same 42 rows, `is_top_n` true for exactly 5, `top_n` column 5. (5) The file: `pd.read_parquet(<file>).columns` equals `PREDICTION_COLUMNS` (no label column); `pd.read_parquet("data/predictions")` reads. (6) Pass criterion for "eyeball": ranks strictly descending by score, every `discharge_time` inside the printed window, reasons plausible (a CABG-history-dominated Top-10 is expected and is the caveat, not a bug), the demo notice printed first. A Top-10 that is *not* reason-concentrated, or a label column anywhere, is a failure.

**Test gate:** `ruff check .` then `pytest` (269 existing tests plus the new ones; new tests need no JVM or gold data). `MLFLOW_DISABLE_AGENT_HINT=1`.

## Dependency change (flagged)

`typer` is not installed; `pyproject.toml` keeps `dependencies = []` (the lockfile-only convention, unchanged; it is a known gap). Verified read-only with `pip install --dry-run typer==0.27.2`: it adds exactly `typer 0.27.2`, `rich 15.0.0`, `markdown-it-py 4.2.0`, `mdurl 0.1.2`, `shellingham 1.5.4`; `click 8.5.0`, `pygments 2.21.0` and `annotated-doc` are already installed (`pygments` moves from the dev to the runtime lockfile as well). Its only environment-marker dependency is `colorama; platform_system == "Windows"`, so `requirements-linux-extras.txt` needs no change and CI's install commands are unchanged. Procedure: add `typer==0.27.2` to `requirements.in`; run `pip-compile --generate-hashes --output-file=requirements.txt requirements.in` (not the `--no-index` command in the header); check the diff adds only those packages (and `pygments`) and leaves every existing pin, notably `pandas==2.3.3` and `numpy==2.4.4`, untouched; check that the `pygments` and `click` pins (2.21.0 / 8.5.0) are identical in `requirements.txt` and `requirements-dev.txt` (the two files are installed together). `requirements-dev.txt` is left untouched (its `# via` annotations still name `rich`-less consumers; that is cosmetic) UNLESS the check shows a differing pin, in which case recompile it too with `pip-compile --generate-hashes --output-file=requirements-dev.txt requirements-dev.in`, review the diff, and record it; reinstall per README. **CI (Linux) is the first Linux verification of the new hashes**; if it fails on a missing platform-conditional dependency, follow the procedure in the slice-3 note.

## Standards deviations and gaps (flagged, both ways)

- **Present but out of proportion:** the cloudpickle model is arbitrary-code-on-load; mitigated only by ordering (guards first) and the trusted-local-store assumption. There is no signed-artifact or model-registry step (slice 3 decision).
- **Deviation from CLAUDE.md wording:** the "Batch scoring (v1)" bullet describes scoring per run date over new data; this slice is a held-out-rows demo (user decision). CLAUDE.md is updated to say so.
- **Missing, unchanged:** no CI job verifies `requirements.txt` still matches `requirements.in`; the real-data gate is manual (CI has no gold table or MLflow store); no schema-evolution policy for the prediction file beyond `schema_version`; no data-quality check on the written file beyond `write_partition`'s own validation.
- **`scripts/` vs a packaged entry point:** consistent with slices 1-3 (`pyproject` has no `[project.scripts]`); named follow-up.

## Downstream work checklist

- [ ] `requirements.in` + `requirements.txt` (Typer), reinstall, run the suite.
- [ ] Code + tests above (including `tests/scoring/__init__.py`); `ruff check .` clean; report the new test count and the added suite time (the 38 guard cases each clone a run: expect on the order of a minute; measure it, and if it is too slow, cases that only differ in an artifact text can share a clone).
- [ ] Manual real-data gate; record what was seen.
- [ ] README (there is no "package layout" section; the layout goes in CLAUDE.md): (1) slice table row 4 -> **Done** with a link to this note; (2) a new section "Batch scoring (Slice 4: Top-N triage list)" after the Slice 3 section containing: what it is (a demo over held-out gold rows, not as-of scoring), the run command (`python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --experiment-name readmission-risk-2b --run-date 20250630`), the flag list in one table, the output layout and column list (`as_of_date`, `scoring_window_days` etc.), the valid run-date range on the real data (20230208-20260831 at W=30) and why others refuse, the demo limits (a)-(f) verbatim in short form, and one sentence that `resolve_run_id` refuses when an experiment holds more than one FINISHED run for a model, so re-running `train_models.py` under an existing experiment name makes `--model` ambiguous (use a fresh `--experiment-name` per training run, or pass `--run-id`); (3) the Setup section's install lines are unchanged (Typer arrives through the lockfile).
- [ ] CLAUDE.md: (1) Architecture: a "Slice 4 (2026-09-20)" paragraph stating demo scope, the `src/readmission_risk/scoring/` layout (module list), the output contract (partition path, columns, tie-break), the 13 guards in one sentence, the deferred as-of scoring path and label v2 as follow-ups; (2) the "Batch scoring (v1)" bullet gets a clause saying v1 as built is a held-out-rows demo; (3) "Build & test commands": the score command and the new test count and suite time; the Typer dependency line; (4) "Repo hygiene": nothing new. Acceptance for both documents: every command shown was run as written.
- [ ] This note: append the measured real-run results and the design-check log.
- [ ] No commit until the user says so.

## Out-of-scope follow-ups

1. **As-of scoring path:** features for unlabelled recent discharges built from the raw CSVs (a Big Join variant), open-stay semantics (`flag_inpatient_stays`' null-`STOP` handling must change; do not reuse it as is), and a scoring-side eligibility filter. This is what turns the demo into the product.
2. **Label v2** (four measured candidates in the slice-2b note's "Label v2" section), after the 100k-200k scale-up. Changing it bumps `LABEL_DEFINITION`; the guard #5 then refuses old runs by design.
3. Scale-up phase: swap `writer` for a BigQuery direct-write (Storage Write API) writer; GCS-resident inputs.
4. Airflow (self-hosted) wrapper around `score_run_date`; hosted MLflow.
5. Streamlit dashboard reading the partitions.
6. `--allow-in-sample` with an `in_sample` flag column (rejected for v1).
7. Feature attribution per row; thresholds/decision curves; retrain-on-shift calibration.
8. `[project.scripts]` packaged entry point; CI check for lockfile drift; a fixture-backed CI smoke test of the CLI on committed tiny data.
9. Tighten slice 3's purge (tracked in the slice-2b note); a content-fingerprint of the gold metadata sidecar.
10. Record `git_dirty`/commit in the output file if reproducibility-from-commit becomes a requirement.
11. Revisit `DEMO_NOTICE` (make it conditional on the gold/run) before any scoring of the v2 population or any real as-of scoring.
12. Dedupe the MLflow-state autouse fixture now duplicated in `tests/scoring/conftest.py` and `tests/models/conftest.py` (hoist to `tests/conftest.py`); record the Python and `cloudpickle` versions as run tags in slice 3's trainer and check them in guard 8.

## Design-check log

### Round 1 (2026-09-20): comprehension passed; critic `design needs revision` (22 gaps); readiness `implementation not ready` (12 questions). All addressed in design v2; none rebutted.

Verified empirically before revising (throwaway prototypes, scratchpad): `import mlflow` alone loads `pyspark` (critic 1 was right); an in-file `run_date` column breaks `pd.read_parquet(<output-dir>)` over Hive-style partitions (critic 5 right) while `as_of_date` works; the `us` cast plus a null reason round-trips under `assert_frame_equal`; guard 12's three checks hold on all four real 2b runs; the pre-2b runs lack the `gold_label_definition` tag; per-run real-data expectations for the manual gate were measured for all four runs.

| Gap (critic C / readiness R) | Resolution |
|---|---|
| C1 no-pyspark rule and test 31 unsatisfiable | Rule restated (no `big_join` import, no Spark session); test 31 asserts `big_join` not in `sys.modules`; reason documented |
| C2 / R1 test 25 cannot pass (provenance columns differ) | Compares only `encounter_id, rank, is_top_n, risk_score`; renamed `test_label_does_not_reach_ranking`; states honestly that it kills label-in-sort/tie-break, and that test 10 is the structural protection |
| C3 / R2 `clone_run` signature vs `no_signature`; unstated types | Signature fully typed; `no_signature` added; `signature_columns=None` = all features |
| C4 / R7 in-memory dtypes unpinned | `build_predictions` docstring pins every dtype; `pa.Table.from_pandas(..., schema=..., preserve_index=False)` specified; test 9 pins them |
| C5 in-file `run_date` breaks parent-directory read | Column renamed `as_of_date`; test 15b added |
| C6 test 26 does not prove the XGBoost path | Test 26 checks the loader receives the resolved run id and XGBoost scores id by id, differing from LR |
| C7 FINISHED filter, deleted branches untested | Test 22 extended (FAILED clone, soft-deleted run, deleted experiment) |
| C8 `--run-id` ignores lifecycle | Guard 2 now also requires `lifecycle_stage == "active"` (key `deleted`), with a test case |
| C9 default `window_days` and CLI defaults untested | Test 26b (metadata window 14) and defaults assertions in test 33 |
| C10 no non-mutation test | Test 10b |
| C11 unknown partition values in pure selection | Step 0 in `select_held_out_window`; test 5b |
| C12 `test_start_date` unused | New guard 12 (`split consistency`) using it, with exact-count tests |
| C13 / R4 MLflow state isolation, env var | `tests/scoring/conftest.py` sets the env var and duplicates the restore fixture; `scoring_env` restores the URI itself |
| C14 / R3 clone experiments' artifact location | `clone_run` creates experiments with an explicit `artifact_location` under the temp tracking dir |
| C15 / R4 helper placement; `tests/scoring/__init__.py` | Helpers in `helpers.py`; `__init__.py` added to Surfaces |
| C16 step-7 ambiguity | The same label-free frame goes to `score_batch` and `build_predictions` |
| C17 null reason in the report | Prints `(none)`; test 32 |
| C18 `DEMO_NOTICE` dataset-specific | Limit (e) and follow-up 11 |
| C19 gate lacks sensitivity/pre-2b runs | Gate uses all four real runs and refuses two pre-2b runs |
| C20 default experiment name unusable | Kept (consistent with the trainer); the error lists available names; test 23b |
| C21 Python/cloudpickle versions unchecked | Stated as a known limit; follow-up 12 |
| C22 frozen dataclasses with DataFrames; artifact reading; `keep_default_na`; Typer flags; stale tmp; pygments pins | `eq=False`; artifact download and `keep_default_na=False` specified; single-name flags; stale-tmp sentence qualified; pin-consistency check added |
| R5 test 21 mechanism and scope | Test 21 runs every case through `score_run_date` with recording fakes; #6/#7 only via clones |
| R6 fingerprint source for `build_predictions` | `ModelRun.gold_fingerprint` |
| R8 `--model` declaration and exit code | Free `str` validated by `_validate_config`; test 35 |
| R9 test 18 cannot show tmp cleanup | Stub writes partial bytes, then raises; `pq.write_table` called via the module attribute |
| R10 gate lacks expected values / unclear run id / "by eye" criterion | Table of measured values for four runs; run ids named; explicit pass criterion |
| R11 report formats | Pinned (`isoformat()`, `%Y-%m-%d`, `.4f`) and asserted |
| R12 `scoring_env` arguments | Spelled out (`TrainingConfig` fields) |

### Round 2 (2026-09-20): critic `design needs revision` (15 gaps); readiness `implementation not ready` (7 questions). All addressed in design v3.

Verified before revising (scratchpad prototypes on the real fixture/store): fixture split and window counts (165/310/177; `20221130`, `20221215`, `20251112`), guard-12 case preconditions, nullable-field round trip, a plain `.tmp` breaks the parent-directory read while a dot-prefixed one does not, `OverflowError` for the three overflow inputs, and the revised test-1 fixture and its stable-sort mutant.

| Gap (critic C / readiness R) | Resolution |
|---|---|
| C1 test 30 contradicts guard 12 and step order; test 24 cannot see a pipeline that ignores `partition_of` | Test 30 replaced by a natural in-sample window on the fixture (`20221130`: 3 train + 3 excluded; `20221215`: 2 + 6); the tampered-assignment case moved to guard 12 (#12(ii)) |
| C2 circular import `store` <-> `pipeline` | New `scoring/constants.py` owns `DEMO_NOTICE` and defaults; `pipeline.py` re-exports |
| C3 `clone_run` has no frame to infer a signature from | `gold_frame` parameter added |
| C4 / R2 missing or malformed run metadata | Uniform rule (refusal with the guard's key, never `KeyError`/`MlflowException`/JSON error); per-guard details; cases added |
| C5 missing artifact raises `MlflowException` | Converted (guards 9 and 11); cases `drop_artifacts` for both artifacts |
| C6 test 15b comparison wrong (int categories) | `astype(str)` vs `strftime("%Y%m%d")` conversion stated |
| C7 `_validate_config` untested | Test 26c (bad configs with a nonexistent gold dir, so ValueError proves validation precedes I/O) |
| C8 test 26b not pinned | `run_date="20251112"`, 10 rows measured, precondition asserted |
| C9 / R4 test 33 fake return value; names the script must bind | Fake returns a real `ScoringResult` from a helper; script binds names and exposes `app` |
| C10 / R6 case #12(i) triple depends on data | First train row whose patient has >=2 train rows (`e0000000`); triples recomputed independently; intended pattern asserted |
| C11 `OverflowError` escapes | `window_bounds` converts to ValueError; test 2 cases |
| C12 tmp file breaks concurrent directory reads | Dot-prefixed `.predictions.parquet.tmp` (verified); tests 15b/17/18 updated |
| C13 notice printed "at the top of every run" vs error paths | Reworded: first line of `format_report` on success only |
| C14 truncation, nullability, equal-`index_stop` sort, ignored `--experiment-name` | Test 32 truncation case; nullability pinned in `PREDICTION_SCHEMA` and tested; test 1 gets e6 (equal stop) with its stable-sort mutant; `--experiment-name` ignored with `--run-id`, documented |
| C15 default experiment name exists in no real store | Kept deliberately: it matches the trainer's default and a default embedding a data-version name (`-2b`) would rot; the error lists available names (test 23b) |
| R1 `GUARD_CASES` structure | `GuardCase(case_id, guard_key, build)` with `build(env, experiment) -> (run_id, extra_substrings)` |
| R3 schema comparison and metadata attachment | `remove_metadata()` comparison; the two custom keys attached via `with_metadata` (the `pandas` key is allowed); verified |
| R5 minimum columns of `select_held_out_window` | Only `encounter_id` and `index_stop` required; all input columns returned |
| R7 `requirements-dev.txt` | Left untouched unless a pin differs; then recompile and record |

### Round 3 (2026-09-20): critic `design needs revision` (14 gaps); readiness `implementation not ready` (7 questions). All addressed in design v4.

Verified before revising: the critic's test-36 claim (a whitespace-*normalised* comparison of the real `DEMO_NOTICE` against Typer's `--help` fails at any width because Click wraps at the hyphen in `development-set`; all-whitespace-removed comparison passes at COLUMNS 60/80/200). My v3 note that this was "verified" had used a shorter notice; corrected.

| Gap (critic C / readiness R) | Resolution |
|---|---|
| C1 test 36 unsatisfiable | Compare with all whitespace removed; the earlier "verified" claim corrected |
| C2 / R1 guard-case count 35 vs 34 listed | Recounted; the missing cases the uniform rule promised were added (non-list `features`, bad `model_uri` for `get_model_info`, empty `split_assignment.csv`): 37, computed as the sum of per-guard counts |
| C3 ruff F401 on a re-export | `pipeline.py` no longer re-exports `DEMO_NOTICE`; every user imports it from `constants.py` |
| C4 test 32 cannot discriminate (single-letter ids, weak truncation marker) | Distinctive ids `enc-000N`; a 56-char description with a `TAIL` marker after 48 chars; assertions on the table lines only |
| C5 "never unpickled" only checked through the injected loader | `forbid_model_loading` helper patches `mlflow.sklearn.load_model`, `mlflow.pyfunc.load_model`, `cloudpickle.load(s)` around each guard case |
| C6 ambiguous count substrings | `assert_count_phrase` with a digit-lookbehind regex; whole triple sentence for #12 |
| C7 test 26c garbled / untestable claim | Rewritten in two parts: `_validate_config` called directly, and `score_run_date` with a nonexistent gold dir |
| C8 `write_partition` could leave an empty dir or leak `ArrowTypeError` | Arrow table built before `mkdir`; Arrow errors converted to ValueError; test 17 extended |
| C9 test 25 overclaims | Claim narrowed; tie-break and label-refusal coverage pointed at tests 6 and 10 |
| C10 schema field order not stated | `PREDICTION_SCHEMA.names == list(PREDICTION_COLUMNS)`, asserted in test 15 |
| C11 `window_days` output column ambiguous | Renamed `scoring_window_days` |
| C12 test 2 mislabelled | Split into 2 and 2b |
| C13 single-run resolution usability; `search_runs` page limit | README note about fresh experiment names; `max_results=5000` stated |
| C14 ns truncation; `git_commit=None`; fixture scope | Nanosecond guard and test; test 32 second call; `scoring_env` made session-scoped |
| R2 `ScoringEnv` undefined | Defined in `helpers.py` with its fields |
| R3 `parse_yyyymmdd` field name | `"run_date"`; `_validate_config` returns the parsed date |
| R4 does `resolve_run_id` set the tracking URI | Yes, stated |
| R5 test 27 fake semantics | Pass-through recorders for the passing case, a sentinel-writer stub for the swap seam |
| R6 / R7 README "package layout" and doc content | No such README section: layout goes in CLAUDE.md; exact content lists and acceptance for both documents |

### Round 4 (2026-09-20): critic `design needs revision` (7 gaps); readiness `implementation not ready` (2 questions). The 3-revision cap was reached; the user chose "fix the 9 gaps, then one more Pass B/C round" (design v5).

Verified by both agents and by the critic's probes: `pa.Table.from_pandas(..., schema=...)` silently accepts a tz-naive timestamp, a converted timezone and a float `rank`, so test 17's naive-tz case was unpassable as specified (confirmed real).

| Gap (critic C / readiness R) | Resolution |
|---|---|
| C1 / R1 test 17 naive-tz case unpassable; the mechanism was wrong | `ranking.PREDICTION_DTYPES` is the single dtype source of truth; `write_partition` compares each column's `str(dtype)` and requires exact `datetime.date` in `as_of_date`, before any filesystem access; Arrow-error conversion kept as a second line of defence; test 17 lists the five defects |
| C2 / R2 `clone_run` overwrote a planted `model_uri`; truthiness vs `is not None` | Caller tags applied AFTER the clone's own `model_uri`; every override tested with `is not None` |
| C3 test 32 truncation could not catch off-by-N or an ellipsis | Asserts absence of `d*48+"T"`, `d*48+"..."`, `d*48+"\u2026"`; rank-3 row of exactly 48 chars must print whole |
| C4 stale `window_days` in test 9 | Already correct after the v4 rename; test 9 now compares to `PREDICTION_DTYPES` |
| C5 `top_n` propagation untested with a non-default value | Tests 24 (`top_n=7`), 28 (`top_n=3` rerun), 29 (`top_n=4` with an independent recomputation) and 34 (explicit arguments) |
| C6 test 20 needs split counts | `ScoringEnv.n_train` / `n_test` read from the run params `split_n_train` / `split_n_test` |
| C7 guard 7 has two keys under one number | Four cases, each with its own key and a `forbidden` key; the guard's message names only the offending param |

### Round 5 (2026-09-20, the extra round the user chose): readiness `implementation ready` (no open questions); critic `design needs revision` (5 gaps, all test-level). Fixed in design v6.

| Gap (critic) | Resolution |
|---|---|
| 1 pipeline-level provenance and window bounds never asserted (swapped same-typed `str` arguments survive) | Test 24 asserts `gold_fingerprint`, `gold_reference_date`, `label_definition`, `model_name`, `partition`, `as_of_date` on the written file and the literal window bounds; test 28 asserts `model_name` for the XGBoost rerun |
| 2 test 32 line-counting ambiguous | Data lines are the lines between the heading and `Wrote` that contain `enc-`; blank and ruled lines ignored |
| 3 specified behaviours with no test | Tests 15a (`read_partition` FileNotFoundError), 10 extended (length mismatch, empty batch), 27b (step-7 RuntimeError post-conditions) |
| 4 `top_n` upper bound vs int32 | `_validate_config` caps `top_n` at `2**31 - 1`; cases `2**31` and `3_000_000_000` in 26c(a) |
| 5 guard 10 does not bind the model to the run | New guard 13 (`model provenance`: `get_model_info(...).run_id == run_id`, verified on both real runs) with one case; guard cases 37 -> 38 |

## Implementation record (2026-09-20)

**Gate override.** After round 5 the readiness pass closed (`implementation ready`) but the critic pass, whose 5 late gaps were fixed in v6 without a re-check, did not formally close. The user authorized proceeding (`AskUserQuestion`, "Yes, override and implement"). Every late fix is exercised by a test below.

**Built.** `src/readmission_risk/scoring/` (`constants`, `selection`, `runs`, `ranking`, `store`, `report`, `pipeline`), `scripts/score_discharges.py`, `typer==0.27.2` in `requirements.in`/`requirements.txt` (the recompile added exactly `typer`, `rich`, `markdown-it-py`, `mdurl`, `shellingham` and `pygments`, plus `# via` annotation lines; `pandas==2.3.3`, `numpy==2.4.4` and every other pin untouched; `click` and `pygments` pins identical in `requirements-dev.txt`, which was left as is), and `tests/scoring/` (118 tests at first implementation, 123 after the pre-commit review fixes). Test 21 (the 38-case guard matrix) lives in `test_pipeline.py`, not `test_runs.py`, because it runs through `score_run_date`.

**Deviations from the doc (decisions, not drift).**
- `ranking.PREDICTION_DTYPES` is a full 17-entry mapping built from the 6 special dtypes plus `object` for the rest (equivalent to the doc's "listed specials, everything else object").
- `select_held_out_window` returns a fresh `RangeIndex` (the doc only said "a copy").
- The installed ruff (0.16.8, the version pinned in `requirements-dev.txt`) enables more rules than the defaults the doc assumed (`C408`, `ISC004`, `RUF100`, `TRY004`, `DTZ001`, `EXE001`, `PLW1510`, `I001` with an 88-column import wrap). The code follows the repo's existing `# noqa: TRY004` + reason precedent (`split.py`); the script is executable.
- The doc's test 18 gained an assertion on the temp file's name (dot-prefixed, beside the final file) after the mutation check below found it unobserved.

**Verification results.**
- `ruff check .`: clean. `pytest`: **387 passed** (269 existing + 118 new; 392 = 269 + 123 after the review fixes below) in about 180 s (the suite was about 90 s before; the scoring tests add about 90 s: one session-wide training of two small models, 38 cloned runs, and the CLI/pipeline tests).
- **Mutation check** (a throwaway script applying 19 named wrong implementations to the real source, one at a time, and running the relevant tests): start off by one day, end inclusive, silent filter instead of refusing, sort by `index_stop` alone, ties by `encounter_id` descending, stable sort on score only, competition ranks, precision denominator N, positional label alignment, no dtype pre-check, non-dot-prefixed temp name, temp file never removed, in-file column named `run_date`, guard 12 ignoring patient overlap, `resolve_run_id` ignoring run status, guard 8 disabled, `top_n` not propagated, `gold_reference_date` swapped for the run date, model unpickled before the guards. All 19 were killed; the first pass had one **survivor** (the temp-file name, never observed by any test) and one pattern that did not apply (matched twice); both were fixed and re-run.
- **Manual real-data gate** on `data/gold/full_run_2b` (all matched the doc's table exactly): `--run-date 20250630` scores 42 held-out discharges with all four real runs. LR2b (default): rank-1 0.0816, rank-10 0.0149, Top-10 = CABG-history 6, NSTEMI 2, dependent drug abuse 2; XGB2b: 0.1074 / 0.0132, CABG-history 6, NSTEMI 2, dental referral 2; LRsens: 0.1003 / 0.0302; XGBsens: 0.0547 / 0.0167. `--show-observed-outcomes`: 1 readmission in the batch of 42, in the Top-10 (precision@10 0.10, lift 4.2). Explicit `--run-id 2bb281f6...` gives a frame identical to the default. Refusals (exit 1, `ERROR:` on stderr, no partition written): `20230115` (0 in-sample, 15 excluded), `20221201` (25, 7), `20230207` (0, 1), `20260901` (0, 1), `20260916` (0, 1); the two pre-2b runs refuse on `gold_label_definition`; `--model` with `--run-id` refuses as mutually exclusive; the default experiment name lists the four real experiments. Boundaries succeed: `20230208` scores 30 rows, `20260831` scores 16. Idempotency: two runs give identical sha256; `--top-n 5` gives the same 42 rows with 5 `is_top_n` and `top_n == 5`. The file has exactly `PREDICTION_COLUMNS` and no label column; ranks strictly descending, every `discharge_time` inside the window; `pd.read_parquet("data/predictions")` reads two sibling partitions (83 rows).
- **By eye** (the printed Top-10, `20250630`): dates all in June 2025, six CABG-history stays lead, then NSTEMI and drug-abuse admissions; a CABG-history-dominated list is the documented caveat, not a bug. The admission-reason column truncates to 48 characters, so `(situation)` prints as `(situ`, as designed.

### Pre-commit review, round 1 (`/eg-precommit-review`; two findings, both fixed)

1. **Silent truncation near the reference date (real defect; the design's own "no silent truncation" claim was wrong for these dates).** Slice 2 keeps a non-readmitted index row only if `index_stop + label window < reference end`; readmitted ones are kept regardless. So discharges at or after `reference end - 30 days` (2026-08-18 on the real table) exist in gold only if readmitted (verified: exactly 1 gold row, a positive, on or after that date), and no comparison against rows that DO exist can notice the rest are missing. `20260831` had been advertised as a valid boundary and scored 16 rows of a window that should hold about 35; batch size decayed from 32 rows at `20260817` to 16 at `20260831`. Fix: `selection.observation_horizon(reference_date, label_window_days)` and an `observed_until` argument of `select_held_out_window` (the pipeline always passes it) refuse any window whose end is after the horizon, naming the latest valid run date (`2026-08-17`). Valid real run dates: 1,287 (20230208 to 20260817, batches of 21-49 rows), not 1,301. Tests: `test_observation_horizon`, `test_window_past_the_observation_horizon_is_refused` (a window ending exactly at the horizon passes; one day later is refused), `test_pipeline_refuses_windows_past_the_observation_horizon` (fixture: `20260817` scores, `20260818` refuses, loader and writer not called); three mutants (`>=`, check never applied, horizon not passed by the pipeline) all killed. README, CLAUDE.md and this note's figures were corrected.
2. **Fixed temp-file name made overlapping runs unsafe (the doc claimed "each replace is atomic, the last wins", which a shared temp name falsified).** Two runs for one run date (cron plus a manual run, an orchestrator retry) would truncate and write the same inode, so the loser could corrupt the file the winner had just renamed into place, or fail on its own rename. Fix: `store.write_partition` uses a unique dot-prefixed temp file per writer (`tempfile.mkstemp(dir=..., prefix=".predictions.", suffix=".tmp")`). Test: `test_overlapping_runs_for_one_run_date_each_replace_with_a_complete_file` (an inner run completes between the outer's write and its rename; the fixed-name mutant is killed). Still not done: an `fsync` before the rename (a crash could leave an unsynced final file; named follow-up) and any cross-process locking (last writer wins).
3. **Round 2 found a regression introduced by fix 2, and one item was rebutted.** `tempfile.mkstemp` creates the file `0600` and the rename carried that onto the partition (verified: `-rw-------` next to an older `-rw-r--r--` partition), which would hide the output from a dashboard or scheduler running as another user. The temp file is now named with `uuid4` and created by `pq.write_table` under the umask; test `test_partition_file_gets_umask_permissions_not_owner_only` (umask 022 must give 0644; the mkstemp mutant is killed). Stale counts in CLAUDE.md and this note were updated. **Rebutted:** "a SIGKILL between write and rename leaves a `.predictions.<hex>.tmp` file that is never cleaned; sweep stale ones at the start of a write." A sweep could delete another live writer's temp file, which reintroduces the overlap hazard fix 2 removes; the leftover is dot-prefixed (readers ignore it), so it is documented in `write_partition`'s docstring instead.

