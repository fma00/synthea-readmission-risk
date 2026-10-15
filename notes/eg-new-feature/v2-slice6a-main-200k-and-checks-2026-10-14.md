# v2 slice 6a: main_200k generation, Dataproc Big Join and pre-training data checks

**Date:** 2026-10-14. **Branch:** a feature branch off `v2`, PR into `v2`. **PRD:** `notes/prds/v2-mlops-keyless-scheduled-scale-up-2026-10-09.md`
(FR13, FR14, M7; Implementation hints item 6). **Predecessor:** `notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md`
(Results; A7; "Derived for slice 6"). **Step-0 decisions (owner, 2026-10-14):** slice 6 splits into 6a (this note) and 6b
(retraining, FR15 gate, FR16 frozen-v1, FR0.3 portable export; its own design doc); 5b's operating values are confirmed
(n2-standard-32, `-Xmx96g`, `max_run_duration` 7200 s for `main`; Dataproc `maxExecutors=7`, ttl 2 h); the BigQuery
direct-write of gold and the connector-0.44.0 MERGE test move to slice 8; `v2/main_200k/` raw is exempt from the 90-day rule
and kept 365 days; runtime facts go into `build_provenance` (no metadata v3).

## Why

Slice 6 evaluates models on `main_200k`, the pre-registered validation population (`prereg.yaml`: seed 29, clinician seed 29,
population 200,000, role `validation`). It does not exist yet. 6a creates it and proves it is fit for use **before any model
touches it**. It carries four requirements:

1. FR13/FR14: generate 200k patients on the spot VM, then run the Big Join on Dataproc Serverless 3.0 with `maxExecutors=7`.
2. FR14: record the runtime's actual sub-version and connector version. The batch description reports only `"3.0"`
   (measured: `data/pilot/pilot_20k/batch_*.json` → `describe.runtimeConfig.version`), so the facts must be read inside the job.
3. Slice-5 follow-ups: `main_200k`'s patient Ids disjoint from the pilot's and from v1's (FR16's external validation is only
   external if the frozen-v1 models never saw these patients); corrected-A7 (i)-(iii), with (iii) measured before any
   training; the raw-retention decision made before generating.
4. M7: at least 400 test-window positives (`prereg.power.min_test_positives`).

## Scope

**In:**
- **S1 Terraform:** a `main` generation template class; the raw bucket's lifecycle narrowed so `v2/main_200k/` is kept 365
  days instead of 90.
- **S2 Runtime facts:** a best-effort collector in the Spark job, enabled by a new `--record-runtime` flag that only the
  Dataproc submitter passes, writing into the existing `build_provenance` under a `runtime.` key prefix. No metadata v3.
- **S3 Population-check CLI** (`make check-population RUN=<run>`): read-only, counts only, never loads or fits a model; checks
  C1-C9 below; JSON report.
- **S4 Live runbook L0-L8**, each step confirmed by the owner immediately before it, including a rehearsal of S3 on `pilot_20k`
  before `main_200k` exists.

**Out:** BigQuery direct-write of gold and the connector MERGE test (slice 8); everything in 6b; `gold_fingerprint_definition`
in `serving.*` (6b, only if a promotion happens); any change to the label, the split, `prereg.yaml` analysis values, the scorer
or its guards; a pilot re-run of the Big Join with the new collector (option O1, not chosen).

## Decisions

- **D1 No metadata v3.** v2's `build_provenance` already accepts any non-empty `str → str` map (`gold_metadata.py:169-173`,
  `big_join.py:511-513`). A v3 bump would touch three readers that test `metadata_version == 2` (`models/data.py:212`,
  `models/training.py:267`, `scoring/bundle_guard.py:408`, the last a scorer guard this slice must not touch). Runtime facts are
  environment facts, not code facts, so they use a `runtime.` key prefix that `--provenance` arguments may not use. Trade-off:
  not a typed field; C8 reports them.
- **D2 Retention.** The current rule (`infra/storage.tf:49-57`, `age = 90`, unscoped) becomes two rules: `age = 90` with
  `matches_prefix = ["v2/pilot_"]`, and `age = 365` with `matches_prefix = ["v2/main_"]`. Only these two run names exist
  (`TEMPLATE_CLASS_FOR_RUN`, `ops/generation_vm.py:54`); an object outside both prefixes would no longer expire, so L0 lists
  the bucket and refuses to continue if anything else is in it. Cost ≈ 45.6 GB × ~$0.020/GB-month ≈ $0.9/month, ≈ $11 over
  365 days (list-price assumption), inside the CHF 5 early-warning budget. The comment "deterministic per seed"
  (`storage.tf:43`) becomes "content-deterministic given seed, clinician seed, end date and JVM time zone (slice 5)".
- **D3 Template.** `var.generation_templates` default becomes `{ pilot = 7200, main = 7200 }` (`infra/variables.tf:76-79`);
  `compute.tf:11`'s `for_each` creates `synthea-generator-main-7200s`, identical to the pilot template. `-Xmx96g` is already the
  launcher default (`LaunchOptions.java_max_heap = "96g"`, `ops/generation_vm.py:174`).
- **D4 Package code with tests**, not a notes snippet (unlike 5b's `acceptance.py`): it is the gate in front of 6b, rehearsed
  on the pilot.
- **D5 C3 reuses P7.** The generation-summary-vs-prereg comparison in `dataproc_batch.check_raw_matches_prereg`
  (`dataproc_batch.py:183-212`) is factored into a pure function both use, so P7 and C3 cannot drift.
- **D6 The v1 comparator is the frozen models' training gold AND v1's raw.** The frozen-v1 models were trained on
  `data/gold/full_run_2b` (metadata version 1: no generation summary links it to raw), so C5 compares against its `patient_id`
  set directly, against `data/raw/full_run/csv/patients.csv` (a superset), and checks the link: every `full_run_2b` patient is
  in v1's raw `Id` set and v1's raw summary has `seed == 42`.
- **D7 A7 (ii) is the owner's corrected wording, no stricter:** every encounter starting on or after `reference_date + 1 day`
  is a posthumous Death Certification (slice-5 note, Results > A7). "Posthumous" is made explicit as `DEATHDATE` non-null and
  `DEATHDATE ≤ date(START)`; no bound on when the patient died.

## Surfaces touched

| Surface | Change |
|---|---|
| `infra/variables.tf:76-79` | default `{ pilot = 7200, main = 7200 }`; description text |
| `infra/storage.tf:43,49-57` | two prefix-scoped lifecycle rules; comment |
| `tests/test_infra.py:218-221` | `test_raw_bucket_deletes_after_90_days` → `test_raw_bucket_lifecycle_pilot_90_main_365`; new `test_generation_templates_default_has_pilot_and_main` |
| `src/readmission_risk/pipeline/runtime_env.py` (new) | `collect_runtime_facts` (S2); stdlib + `pyspark` only (FR3, `tests/pipeline/test_runtime_dependencies.py`); packed into the Spark zip automatically (`ops/spark_zip.py` packs tracked `pipeline/**/*.py`) |
| `scripts/run_big_join.py` | `--record-runtime`; refuse `--provenance runtime.*`; `with_runtime_facts` helper; merge inside the existing `try` |
| `src/readmission_risk/ops/dataproc_batch.py:149-182, 183-212` | `submit_argv` appends `--record-runtime` after the `--provenance` pairs; `generation_summary_problems` factored out of `check_raw_matches_prereg` (D5) |
| `src/readmission_risk/ops/prereg.py:58-71` | two properties on `Prereg`: `split` → `self.raw["split"]`, `power` → `self.raw["power"]` |
| `tests/pipeline/test_runtime_env.py` (new); `tests/pipeline/test_big_join.py` (existing `test_run_big_join_cli_through_hadoop_storage`, `:1431-1446`, unchanged; new Spark-free tests next to it, no extra Spark run); `tests/ops/test_dataproc_batch.py` (argv test; P7 tests unchanged and passing); `tests/ops/test_prereg.py` (properties) | tests |
| `tests/ops/fake_gcloud.py:66-78` (`storage cp`) and `:79-84` (`objects describe`) | `storage cp` also downloads (gs:// source → local file); `objects describe` also returns `crc32c_hash`; a `size_overrides: dict[str, int]` field lets a test make `describe` report a different size (the size-mismatch `GatherError` test) |
| `src/readmission_risk/ops/population_checks.py` (new), `scripts/check_population.py` (new), `tests/ops/test_population_checks.py` (new) | S3 |
| `Makefile` | `check-population` target (LIVE, read-only, downloads data); `.PHONY`; help text |
| `docs/setup.md` | subsection "main_200k (slice 6a)": runbook pointer, retention, check CLI |
| `CLAUDE.md` | small targeted update after the live run |
| this note | design, then Results |

Not touched: `pipeline/big_join.py`, `gold_metadata.py`, `models/*`, `scoring/*`, `bundles/*`, `warehouse/*`, `prereg.yaml`,
`ops/generation_vm.py`.

## Interfaces

### S2: `pipeline/runtime_env.py`

```python
RUNTIME_KEY_PREFIX = "runtime."
JAR_DIRS_DEFAULT: tuple[str, ...] = ("/usr/local/share/google/dataproc/lib", "/usr/lib/spark/jars",
                                     "/usr/lib/spark/connector", "/opt/spark/jars")
_ENV_NAME_RE = re.compile(r"DATAPROC_[A-Z0-9_]+")                 # fullmatch on the name
_SECRET_MARKERS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "CREDENTIAL")
_JAR_RE = re.compile(r"(spark-(?:[0-9.]+-)?bigquery|gcs-connector)[^/]*\.jar")  # fullmatch on the basename; NOT the
# google-cloud-bigquerystorage / grpc / proto client jars, which do not start with "spark-" or "gcs-connector"
CLASSPATH_CONF_KEYS = ("spark.jars", "spark.driver.extraClassPath")
MAX_VALUE_CHARS = 200

def collect_runtime_facts(spark, environ: Mapping[str, str], jar_dirs: Sequence[str]) -> dict[str, str]: ...
```

Returns non-empty `str → str` only and **never raises**. Six steps, each in its own `try/except Exception` (`# noqa: BLE001`,
reason: best-effort, must not fail the one-shot batch), named `python_version`, `java_version`, `env`, `conf`, `jars`, `classpath`; a failed
step appends `"<step>: <ExceptionType>"` to `runtime.collection_errors` (`"; "`-joined, in step order; key present only on
failure).

| Key | Value |
|---|---|
| `runtime.python_version` | `platform.python_version()` |
| `runtime.java_version` | `spark.sparkContext._jvm.java.lang.System.getProperty("java.version")` |
| `runtime.env.<NAME>` | each `NAME` in `environ` with `_ENV_NAME_RE.fullmatch(NAME)`, no `_SECRET_MARKERS` substring, non-empty value |
| `runtime.conf.<key>` | each `(key, value)` of `spark.sparkContext.getConf().getAll()` with `key.startswith("spark.dataproc.")` and `"version" in key.lower()`, non-empty value |
| `runtime.jar.<basename>` | one key per matching jar, so nothing is ever truncated away: step `jars` adds each regular file in each existing directory of `jar_dirs` plus `environ["SPARK_HOME"] + "/jars"` (when set) whose basename `_JAR_RE.fullmatch`es, value = its directory; step `classpath` splits `System.getProperty("java.class.path")` on `os.pathsep` and each `CLASSPATH_CONF_KEYS` conf value on `,` and `:`, and adds each entry whose basename matches, value `"classpath"` (a jar found both ways keeps its directory). No matching jar → no `runtime.jar.*` key |

Every value is cut to its first `MAX_VALUE_CHARS` characters (exactly 200 kept as is); keys are never cut. Which env/conf keys carry the
patch-level version on runtime 3.0 is **not verified**; the collector records everything matching and C8 reports what it found.

### S2: `scripts/run_big_join.py`

- New flag `--record-runtime` (`store_true`, default False). `parse_args` calls `parser.error` (exit 2) if any `--provenance`
  key starts with `RUNTIME_KEY_PREFIX`.
- New module-level helper, so the merge is testable without Spark:
  ```python
  def with_runtime_facts(config: BigJoinConfig, spark, enabled: bool, collect: Callable | None = None) -> BigJoinConfig:
      if not enabled:
          return config
      facts = (collect or collect_runtime_facts)(spark, os.environ, JAR_DIRS_DEFAULT)  # module global looked up at call time
      return dataclasses.replace(config, provenance={**(config.provenance or {}), **facts})
  ```
  (`BigJoinConfig` is `@dataclass(frozen=True)`, `big_join.py:103`.) In `main`, `config` is still built first (fail fast); the
  call `config = with_runtime_facts(config, spark, args.record_runtime)` is the first statement inside the existing inner
  `try` (before `build_big_join`), so a `ValueError` from `replace` prints `ERROR: …` and returns 1 like every other failure.
  Without the flag, behaviour is unchanged.

### S2: `ops/dataproc_batch.py`

- `submit_argv` (`:149-182`): `"--record-runtime"` appended as the last element, after the `--provenance` pairs.
- D5: `generation_summary_problems(summary: dict, prereg: Prereg, run: str) -> list[str]`, the `expected` dict comparison,
  the `generation_summary.provenance.synthea_jar_sha256` check and the `n_alive_patients ≥ population` check moved verbatim out
  of `check_raw_matches_prereg`, which keeps its `gcloud storage cat`, git-ancestor check and `Refused` and calls the new
  function. Messages unchanged.

### S2: `ops/prereg.py`

`Prereg.split` → `self.raw["split"]`, `Prereg.power` → `self.raw["power"]` (properties, like `generation`/`label`).

### S3: `ops/population_checks.py`

Two layers: `gather_inputs` does all I/O, `evaluate_checks` is pure.

```python
RUNS = ("pilot_20k", "main_200k")
Status = Literal["PASS", "FAIL", "WARN", "INFO", "SKIP"]

@dataclass(frozen=True)
class CheckResult:
    id: str; name: str; status: Status; detail: str; values: dict  # values: JSON-serializable counts/strings only

@dataclass(frozen=True)
class IdSource:
    name: str            # "pilot_20k_raw" | "v1_raw" | "v1_gold_full_run_2b"
    location: str        # gs:// URI or local path
    ids: frozenset[str]
    n_rows: int          # rows read (duplicates included)

@dataclass(frozen=True)
class PopulationInputs:
    run: str
    gold_frame: pd.DataFrame | None        # None when the loader refused
    gold_metadata: GoldMetadata | None
    gold_error: str | None                 # the loader's ValueError/FileNotFoundError message
    raw_summary: dict                      # the run's raw generation_summary.json
    patients: pd.DataFrame                 # columns Id, DEATHDATE (str; "" for missing)
    encounters: pd.DataFrame               # columns START, PATIENT, DESCRIPTION (str)
    comparators: tuple[IdSource, ...]
    v1_raw_summary: dict | None            # data/raw/full_run/generation_summary.json
    inputs: dict[str, dict]                # location -> {"bytes": int, "sha256": str, "crc32c": str | None}

class GatherError(RuntimeError):            # -> exit 2
    def __init__(self, message: str, inputs: dict): ...   # .inputs = what was recorded before the failure

def gather_inputs(run: str, *, runner: Runner, gold_opener: Callable[[str], ContextManager[Path]] = materialized_gold_dir,
                  work_dir: Path, v1_raw_dir: Path, v1_gold_dir: Path) -> PopulationInputs: ...
def evaluate_checks(inputs: PopulationInputs, prereg: Prereg, *,
                    prereg_blob_at: Callable[[str], bytes]) -> list[CheckResult]: ...
def exit_code(results: list[CheckResult]) -> int: ...      # 1 if any FAIL else 0
def write_report(path: Path, *, run: str, head_commit: str, tree_dirty: bool, prereg_sha256: str,
                 inputs: dict, results: list[CheckResult], error: str | None) -> None: ...
```

`gather_inputs`:
- Raw summary: `run_ok(runner, ["gcloud", "storage", "cat", f"{raw_prefix(run)}/generation_summary.json"])` (`ops/gcloud.py`
  runner; `raw_prefix` from `ops/generation_vm.py:83`).
- Raw CSVs: `<work_dir>/<run>/raw_subset/` is deleted and recreated, then for `csv/patients.csv` and `csv/encounters.csv`:
  `gcloud storage objects describe <uri> --format=json` (size, `crc32c_hash`), then `gcloud storage cp <uri> <local>` (gcloud
  validates the crc32c on download itself), then local size must equal the described size (else `GatherError`); the
  local sha256 is computed and recorded. No cache.
- `main_200k` only: the pilot's `patients.csv` the same way into `<work_dir>/main_200k/comparators/pilot_20k/patients.csv`.
- Gold: `with gold_opener(gold_prefix(run)) as d:` first record every regular file in `d` as `inputs[f"{gold_prefix(run)}/{name}"] = {bytes, sha256, crc32c: None}`, then `frame, meta = load_gold_table_with_metadata(d)`. A `ValueError` or
  `FileNotFoundError` **from the loader** becomes `gold_error` (the frame and metadata stay None); a download failure from
  `materialized_gold_dir` before any file is read (`ValueError` "cannot read gold", `FileNotFoundError` "no such bucket"/"No
  Parquet files") is a `GatherError`. To tell them apart, the opener is entered through a `contextlib.ExitStack` in its own `try`, and the loader call sits in a
  second `try` inside the stack.
- v1: `v1_raw_dir / "csv/patients.csv"` and `v1_raw_dir / "generation_summary.json"` (missing → `GatherError` naming the
  path), and `load_gold_table_with_metadata(v1_gold_dir)[0]["patient_id"]` (the loader returns `(frame, metadata)`; refusal → `GatherError`). Local files get sha256 and
  bytes in `inputs`, keyed by path; for the v1 gold one entry per regular file in the directory.
- CSV reading: `pyarrow.csv.read_csv(path, convert_options=ConvertOptions(include_columns=[...], column_types={c: pa.string()
  for c in cols}, strings_can_be_null=False))` then `.to_pandas()`; a missing column → `GatherError` naming file and column.
- gcloud or google-auth failures (`run_ok` raising, `google.api_core`/`google.auth` errors) → `GatherError`.

`evaluate_checks` (pure; each check runs regardless of other checks' outcomes; a check whose input is unavailable is `SKIP`).
Definitions: `ref` = `parse_yyyymmdd(prereg.generation["reference_date"], "reference_date")` (`pipeline/gold_metadata.py:61`; every
`parse_yyyymmdd` call below passes the field name as its second argument) at 00:00 UTC;
`ref1` = `ref + 1 day`; `split` = `chronological_group_split(frame, reference_date=ref date, test_start_date=parse_yyyymmdd(
prereg.split["test_start_date_resolved"]), readmission_window_days=prereg.label["readmission_window_days"],
train_start_date=None if prereg.split["train_start_date"] is None else parse_yyyymmdd(...))` (`models/split.py:76-83`),
called once and shared; a `RuntimeError`/`ValueError` from it makes C7b and C9 `FAIL` with the message. Modeling rows =
`split.train` ∪ `split.test` (the frames the split returns; names as in `models/split.py`). START and DEATHDATE are parsed
with `pd.to_datetime(..., utc=True, errors="coerce")` / `format="%Y-%m-%d"`; NaT counts as unparsable.

| Id | What | Pass criterion | SKIP when |
|---|---|---|---|
| C1 | Gold loads, fingerprint, label settings | `gold_error is None`; `metadata_version == 2`; definition `spark-rowhash-v1` (the loader already recomputed and compared the fingerprint; C1 records it); and the metadata equals prereg: `label_definition == prereg.label["definition"]`, `planned_procedure_codes == tuple(prereg.label["planned_procedure_codes"])`, `readmission_window_days == prereg.label["readmission_window_days"]`, `lookback_years == prereg.label["lookback_years"]`, `reference_date == prereg.generation["reference_date"]`. FAIL detail = `gold_error` or the mismatching fields | never |
| C2 | Structure (5b A1-A4 remainder) | `n_planned_stays > 0`; positive rate `n_positive / n_rows` in [0.005, 0.05]; 24 columns, none all-null; `payer_ownership` null fraction ≤ 0.10. (Zero rows and row/positive-count mismatches are refused by the loader → C1.) | gold unavailable |
| C3 | Generation provenance | `generation_summary_problems(raw_summary, prereg, run) == []`; `raw_summary == metadata.generation_summary`; `sha256(prereg_blob_at(raw_summary["provenance"]["prereg_commit"])) == raw_summary["provenance"]["prereg_sha256"]` (`prereg_blob_at` raising any `Exception`, e.g. an unknown commit, makes this clause FAIL with the message); if `metadata.build_provenance` has `prereg_commit`, it equals `raw_summary["provenance"]["prereg_commit"]` | never; when gold is unavailable only clauses 1 and 3 run (FAIL if either fails, else PASS with detail "gold unavailable: metadata clauses not run") |
| C4 | Patient Ids | raw `Id` unique; every gold `patient_id` in raw `Id` | gold unavailable → raw uniqueness only |
| C5 | Disjointness (D6) | `run_ids` = the set of the run's raw `patients.csv` `Id` (a superset of the gold's, C4); for each comparator: `len(run_ids & ids) == 0`; v1 link: `v1_raw_summary["seed"] == 42`, v1 raw Ids unique, `v1_gold ids ⊆ v1_raw ids` (else FAIL "not the v1 raw behind full_run_2b"); records per comparator `n_rows`, `len(ids)`, overlap | never |
| C6 | A7 (i) | 0 unparsable `START`; `max(START) ≥ ref` | never |
| C7 | A7 (ii) (D7) | every encounter with `START ≥ ref1` has `DESCRIPTION == "Death Certification"` and its patient's `DEATHDATE` parses and `≤ START.date()` (a `PATIENT` absent from `patients.csv` counts as not posthumous); reports the count (pilot: 1) | never |
| C7b | A7 (iii) | modeling rows with `label_horizon ≥ ref1` == 0; INFO in `values`: gold rows before the split with `label_horizon ≥ ref1`, modeling rows with `index_stop ≥ ref1` | gold unavailable |
| C8 | Runtime facts (FR14) | `main_200k`: FAIL if `build_provenance` lacks any of `head_commit`, `zip_sha256`, `main_sha256`, `prereg_commit`, `batch_id`, or has no `runtime.` key at all (the gold was not built by this slice's submitter). WARN (not FAIL; best-effort, no re-run) if `runtime.python_version` or `runtime.java_version` is missing, if `runtime.collection_errors` exists, if no `runtime.jar.<basename>` key has a basename starting with `spark-` and containing `bigquery` (the connector WARN), or if no `runtime.env.*`/`runtime.conf.*` value matches `re.search(rf"(?<![0-9.]){re.escape(RUNTIME_VERSION)}\.[0-9]+", value)` with `RUNTIME_VERSION = "3.0"` imported from `ops/dataproc_batch.py:67` (the sub-version WARN; `3.0.14-debian12` counts, Spark's `4.0.2` and a connector's `0.44.0` do not). `values` = every `runtime.*` pair. `pilot_20k`: INFO "not recorded (built before 6a)" | gold unavailable |
| C9 | Power (M7) | `main_200k`: `split.summary["n_test_positive"] ≥ prereg.power["min_test_positives"]`. `pilot_20k`: INFO with the count | gold unavailable |

(When gold is unavailable C1 FAILs, so the exit code is 1 regardless of C3's partial result.) `SplitResult` has `train`,
`test` and `summary` (`models/split.py:20-23`).

`exit_code`: 1 if any `FAIL`, else 0 (`WARN`, `INFO`, `SKIP` do not fail). Exit 2 is reserved for `GatherError` and
precondition failures in the CLI.

No-peeking rule: `values` and `detail` contain counts, sizes, checksums, versions, dates (C6's max START) and field names
only; never a patient Id, encounter Id or description text from the data, no per-subgroup or per-reason figures, no feature
distributions. Concretely: C4 reports `n_duplicate_raw_ids` and `n_gold_ids_missing_from_raw`; C5 per comparator `n_rows`,
`n_ids`, `n_overlap`; C7 `n_after_ref1`, `n_not_death_certification`, `n_not_posthumous`; C3 and C1 name mismatching
fields with prereg's and the data's values (configuration, not data). Enforced by tests: (c) a test that seeds C4, C5 and C7
failures with distinctive Ids (`LEAKCHECK-…`) and descriptions and asserts that none of them appears in the serialized report
or in captured stdout; and import tests (a) an AST test that
`ops/population_checks.py` and `scripts/check_population.py` import nothing from `readmission_risk.scoring`,
`readmission_risk.models.training`, `readmission_risk.models.evaluate`, `readmission_risk.models.tracking`, `mlflow`,
`sklearn`, `xgboost`; (b) a subprocess test that importing `readmission_risk.ops.population_checks` leaves none of those
(nor `pyspark`) in `sys.modules`. `readmission_risk.bundles.gcs`/`.manifest` are allowed (loaded transitively by
`warehouse.gold_source`, measured; path parsing and constants only).

### S3: `scripts/check_population.py`

`python scripts/check_population.py --run {pilot_20k,main_200k} [--v1-raw-dir data/raw/full_run]
[--v1-gold-dir data/gold/full_run_2b] [--work-dir data/checks]`. Steps:
Signature: `main(argv=None, *, runner=default_runner, gold_opener=materialized_gold_dir, run_git=subprocess.run,
repo_root=REPO_ROOT, environ=os.environ) -> int` (`REPO_ROOT = Path(__file__).resolve().parents[1]`; the operator's own ADC and
gcloud identity, no impersonation) (`default_runner`, `run_ok`: `ops/gcloud.py:34,43`), so tests inject fakes.
1. **Preconditions (any failure: exit 2, one `ERROR: …` line on stderr, no report):** `environ.get("CLOUDSDK_CORE_PROJECT")
   == "synthea-readmission-risk"`; `head_commit = git rev-parse HEAD` succeeds; `head_blob = git show HEAD:prereg.yaml`
   succeeds; the working-tree `prereg.yaml` bytes equal `head_blob` (a locally edited prereg is refused: the checks must use
   the committed values); `prereg = parse_prereg(head_blob.decode("utf-8"))` (`ops/prereg.py:351`) does not raise
   (`PreregError`/`UnicodeDecodeError`); `git status --porcelain` succeeds (`tree_dirty` = its output non-empty, recorded, not
   refused). A failing git call is a non-zero `returncode` from `run_git(..., capture_output=True, check=False)`.
2. `prereg_sha256 = sha256(head_blob)` (the bytes actually parsed); `prereg_blob_at = lambda c: git show <c>:prereg.yaml` bytes
   (raises `subprocess.CalledProcessError` on an unknown commit; C3 turns that into a FAIL).
3. `gather_inputs(...)`; `GatherError` → write the report with `error` and `exc.inputs`, `checks: []`, exit 2.
4. `evaluate_checks`, print one line per result `"<STATUS> <id> <name>: <detail>"`, write the report, exit `exit_code`.

Report `<work_dir>/<run>/population_checks.json` (written whenever step 1 passed, i.e. on exit 0, 1 and on a `GatherError`
exit 2):
`{"run", "head_commit", "tree_dirty", "prereg_sha256", "generated_at_utc", "inputs", "checks": [{"id", "name",
"status", "detail", "values"}], "error"}`; sorted keys, 2-space indent.

`Makefile`: `check-population: ## LIVE, read-only: download raw subsets and gold for RUN and run checks C1-C9` →
`$(BIN)/python scripts/check_population.py --run $(RUN) $(CHECK_ARGS)`, with the same `RUN` guard as `bigjoin-batch` and `CHECK_ARGS ?=` next to the existing defaults; `--v1-raw-dir`, `--v1-gold-dir` and
`--work-dir` resolve relative paths against `REPO_ROOT`; `generated_at_utc` is ISO 8601 with a `Z` suffix.

## UX flow (runbook; each L step confirmed by the owner immediately before it)

`export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk` (impersonated calls also `CLOUDSDK_BILLING_QUOTA_PROJECT=LEGACY`).
Interim results only in the gitignored `data/pilot/main_200k/live-checks.md`.

| Step | Action | Pass | On failure |
|---|---|---|---|
| 0 | 6a code merged into `v2` via PR; `git pull`; tree clean and pushed; PR #4 CI green on all four jobs | | |
| L0 | No labelled VM or disk; no RUNNING/PENDING batch; `gcloud storage ls gs://synthea-readmission-risk-raw/` lists only `v2/`, and `gcloud storage ls gs://synthea-readmission-risk-raw/v2/` lists only `v2/pilot_20k/`; `gs://synthea-readmission-risk-gold/v2/main_200k/` does not exist; `prereg.yaml` unchanged (`deviations: []`) | all true | stop (a stray raw object would lose its expiry under D2: decide before L1) |
| L1 | `terraform -chdir=infra plan`, then `apply` | plan = 1 add (`google_compute_instance_template.synthea_generator["main"]`) + 1 in-place update (`google_storage_bucket.raw`, lifecycle), 0 destroy, and under "Changes to Outputs" exactly `~ generation_templates` gaining `+ main = (known after apply)` (`infra/outputs.tf:36-39` maps over the templates); then `gcloud storage buckets describe gs://synthea-readmission-risk-raw --format=json(lifecycle_config)` shows exactly two Delete rules, `{age: 90, matchesPrefix: [v2/pilot_]}` and `{age: 365, matchesPrefix: [v2/main_]}`, and `gcloud compute instance-templates list` includes `synthea-generator-main-7200s` | any other plan line: stop |
| L2 | Rehearsal: `make check-population RUN=pilot_20k` | exit 0; expected from 5b: C2 rate 258/18,861; C5 0 overlap with v1 raw and `full_run_2b`; C6/C7 pass (1 Death Certification); C7b 0; C9 INFO 62; C8 INFO | a FAIL is a CLI bug: stop, fix via PR |
| L3 | `make generate-vm RUN=main_200k JDK_TARBALL=… GEN_ARGS=--allow-main-run` (~45-50 min projected) | exit 0; positive control OK; VM proven gone; `generation_summary.json` with `n_alive_patients = 200000` | launcher diagnosis; preemption → re-run after confirmation |
| L4 | `make bigjoin-batch RUN=main_200k BATCH_ARGS=--allow-main-run`, only after L3's VM is gone (~30-60 min, ~3.1 DCU-hours projected) | SUCCEEDED; wall, DCU-hours, shuffle GB-hours recorded; gold layout check as 5b's L5 | stop, propose fix |
| L5 | `make check-population RUN=main_200k` | exit 0 | stop rule below |
| L6 | No VM, disk or RUNNING/PENDING batch left | all true | clean up |
| L7 | Billing (owner, next day) | compared with the ≈ $1-3 projection | record |
| L8 | Results into this note; CLAUDE.md update; one PR into `v2` | | |

**Stop rule:** any L5 FAIL stops 6b. C5 overlap > 0, C7b > 0 or C9 < 400 go to `/eg-prd` and a committed `deviations` entry
before any analysis (5b's rule); other FAILs are investigated like 5b's L5a. WARNs are recorded in Results and decided by the
owner. **C8 FAIL recovery** (the collector did not run): owner-confirmed `gcloud storage rm --recursive
gs://synthea-readmission-risk-gold/v2/main_200k/` (the submitter refuses a non-empty gold prefix,
`dataproc_batch.py:236-238`), fix via PR if code is at fault, re-run L4 and L5; the raw data is unaffected. The 6b design doc
may use only L5's report.

## Data handling

- **No peeking:** 6a computes no model metric on `main_200k` and loads no model (enforced by the two import tests). C9/C7b
  counts come from the pre-registered split with prereg's settings; `n_test_positive` is the power quantity prereg names.
- **Determinism:** Ids are compared as sets (Synthea row order varies); the split is deterministic; the rowhash fingerprint is
  order-independent; no randomness. The report records input sizes, sha256 and GCS crc32c, HEAD, the tree state and prereg's
  sha256.
- **Leakage:** C5 (D6) protects FR16 and the pilot/main separation. Label, split and leakage controls unchanged.
- **Redaction:** synthetic data; the report and runtime facts hold no billing account, budget id or email; env capture is
  restricted to `DATAPROC_*` names without secret markers; no Id lists in the report.
- **Egress:** `main_200k` patients (~66 MB) and encounters (~4.4 GB) plus the pilot's patients (~7 MB); ~445 MB for L2. Expected
  inside the free 100 GB/month us-central1 → North America egress (assumption; 5b's 4.56 GB download billed nothing, L9).
- **Memory:** encounters at 200k ≈ 14 M rows × 3 string columns in pandas (object dtype) ≈ a few GB; fine on the 64 GiB laptop.

## Failure modes

- **L3:** spot preemption or 7,200 s exceeded → VM deleted, launcher reports it, no raw `_DONE`; re-run after confirmation
  (content-deterministic). Heap exhaustion → `-XX:+ExitOnOutOfMemoryError`, non-zero exit, launcher diagnosis; a fix via PR.
  Disk: 200 GB vs ~46 GB projected raw.
- **L4:** executor OOM or shuffle failure at 128 shuffle partitions (unchanged from the pilot) → stop, propose a fix via PR
  (the fingerprint does not depend on partitioning). ttl 2 h exceeded: same. No IAM change in this slice.
- **S2:** a collector step raising → `runtime.collection_errors`, never fails the batch; `--provenance runtime.x=…` refused
  at parse time; values truncated to 200 characters.
- **S3:** wrong/unset `CLOUDSDK_CORE_PROJECT` → exit 2; missing local v1 raw or gold → exit 2 naming the path; gold or raw
  object missing, download or auth failure → exit 2; a missing CSV column → exit 2 naming file and column; the gold loader
  refusing → C1 FAIL, gold-dependent checks SKIP, exit 1; unparsable `START` → C6 FAIL with count; empty or unparsable
  `DEATHDATE` for a C7 encounter → C7 FAIL; a comparator overlap → C5 FAIL with the count only; `split` raising → C7b and
  C9 FAIL with the message.
- **Concurrency:** L4 never while L3's VM exists (the submitter's preflight refuses; L0/L6 check it too).

## Verification criteria

Offline (CI; no GCP; JVM only where noted):

1. `tests/test_infra.py`: `test_raw_bucket_lifecycle_pilot_90_main_365` (exactly two lifecycle rules, with exact ages and
   prefixes, no unscoped `age` rule, `soft_delete_policy` unchanged); `test_generation_templates_default_has_pilot_and_main`;
   existing template cost-bound tests unchanged and passing.
2. `tests/pipeline/test_runtime_env.py`: with the shared local `spark` fixture: `runtime.python_version` and
   `runtime.java_version` present and non-empty. With a fake `spark` object (no JVM): injected `DATAPROC_IMAGE_VERSION=3.0.14`
   → `runtime.env.DATAPROC_IMAGE_VERSION == "3.0.14"`; `DATAPROC_API_TOKEN` and `SPARK_VERSION` excluded; conf step:
   `spark.dataproc.runtime.Version` kept, `spark.dataproc.tier` excluded, `spark.executor.version` excluded, empty value
   excluded; jars: a dir with `spark-bigquery-with-dependencies_2.13-0.44.0.jar`, `spark-4.0-bigquery-0.44.0.jar`,
   `gcs-connector-3.0.4.jar`, `google-cloud-bigquerystorage-3.11.0.jar` and `other.jar` → exactly three `runtime.jar.*` keys
   (the first three), each valued with the directory; a `SPARK_HOME` in `environ` adds `$SPARK_HOME/jars`; nonexistent dir
   ignored; no match → no `runtime.jar.*` key; classpath: a fake `_jvm` whose `java.class.path` holds
   `/x/spark-bigquery-with-dependencies_2.13-0.44.0.jar:/y/other.jar` and a conf `spark.jars` of `/z/gcs-connector-3.0.4.jar` →
   both connector keys valued `"classpath"`; forty matching jars → forty keys (no truncation); a fake whose `_jvm`
   raises → `runtime.collection_errors == "java_version: <Type>"` and the call returns; a 200-character value kept intact, a
   201-character value cut to 200; every value a non-empty str.
3. `tests/pipeline/test_big_join.py`: the existing `test_run_big_join_cli_through_hadoop_storage` stays as is (no flag →
   `build_provenance == {"head_commit": "abc"}`, the regression); a **new parse-only** test for `--provenance runtime.x=y` →
   exit 2; `with_runtime_facts` unit tests with a fake collector and a plain `BigJoinConfig` (no Spark): disabled → same object;
   enabled → provenance = argv pairs + facts. **Spark-free `main` wiring tests:** monkeypatch the CLI module's
   `make_spark_session` (a fake with `stop()`), `collect_runtime_facts` (a fake returning `{"runtime.python_version": "x"}` and
   counting calls) and `build_big_join` (captures the config it receives, then raises `ValueError("stop")` so `main` returns 1
   before the metadata read); with `--record-runtime` the captured provenance is `{"head_commit": "abc",
   "runtime.python_version": "x"}`; without it the captured provenance is `{"head_commit": "abc"}` and the fake collector was
   not called. No additional Spark run (CI memory, `pipeline-dataproc-runtime`).
4. `tests/ops/test_dataproc_batch.py`: `submit_argv`'s expected argv gains `--record-runtime` as the last element; existing P7
   tests pass unchanged; one direct test of `generation_summary_problems`.
5. `tests/ops/test_prereg.py`: `split` and `power` properties return the document's sections.
6. `tests/ops/test_population_checks.py`: `evaluate_checks` on in-memory `PopulationInputs` (gold frames built with
   `tests/models/helpers.py`'s `row()`/`frame()` with explicit dates around prereg's reference date 20261012 and test start
   20230101; on-disk gold for the `gather_inputs` test via `tests/gold_helpers.write_gold_metadata_for_frame(..., version=2,
   reference_date="20261012", generation_summary=<the fixture summary>)`): PASS and FAIL cases per check C1-C9 (C1 FAIL via `gold_error`, which also SKIPs C2/C4-gold/C7b/C8/C9);
   C1 a metadata `readmission_window_days` of 31, `lookback_years` of 2, another `label_definition` and another `reference_date` → FAIL each; C3 `prereg_blob_at` raising → FAIL; C5 one shared Id → FAIL, values overlap 1; v1 seed 43 → FAIL; a v1 gold Id missing from v1 raw → FAIL; C7 a
   non-Death-Certification encounter after `ref1` → FAIL; a Death Certification dated before `DEATHDATE` → FAIL; one dated after
   a `DEATHDATE` later than `reference_date` → PASS (D7); C7b a modeling row with `label_horizon ≥ ref1` → FAIL (a test-window
   row if the split's post-conditions make a train row impossible); C8 the WARN conditions each (including only `gcs-connector` and `google-cloud-bigquerystorage` jar keys → connector WARN; a
   version value `3.0.14-debian12` → no sub-version WARN; only `4.0.2` and `0.44.0` values → sub-version WARN), and FAIL on a missing `batch_id`; C9 at 399 vs 400. `gather_inputs` with `tests/ops/fake_gcloud.py` (objects preloaded, cp downloads) and
   `gold_opener = lambda uri: nullcontext(local_gold_dir)`: happy path; a size mismatch → `GatherError`; a missing column →
   `GatherError` whose `.inputs` holds what was recorded; a loader refusal → `gold_error` set; gold files recorded in `inputs`.
   The CLI `main` with fakes (`runner` = `FakeGcloud`, `gold_opener`, `run_git` answering `rev-parse`, `status`, `show`):
   exit 2 without a report on a wrong `CLOUDSDK_CORE_PROJECT`, on a working-tree `prereg.yaml` differing from HEAD's, on a
   HEAD blob that `parse_prereg` refuses, and on a failing `git status`; the Id-leak test (c); a
   report with the documented keys on exit 0, on exit 1 and on a `GatherError` exit 2; the two import tests.
7. `ruff check .`, full `pytest`, `make tf-validate`.

Live: L1-L6 as above; **6a is accepted when L5 exits 0** with C1-C9 recorded in Results.

**FR14 closure is separate from 6a's acceptance.** FR14's "record the actual runtime sub-version and connector version" counts
as met only if C8 raises neither the sub-version WARN nor the BigQuery-connector WARN. If either fires, Results and the PRD's
FR14 entry record FR14 as **partially met**, with what *is* recorded: the top-level `spark_version` field (already written by
`build_big_join`, `big_join.py:596`; the pilot's Dataproc gold has `4.0.2`), `runtime.python_version`, `runtime.java_version`,
every `runtime.*` value found, and the batch record's `runtimeConfig.version` (`"3.0"`, in
`data/pilot/main_200k/batch_<id>.json`). The follow-up below then decides how to close it. 6b may proceed either way: FR14 is
provenance, not an input to the gate.

## Out-of-scope follow-ups

- Slice 8: BigQuery direct-write of gold and the connector MERGE test (FR14 remainder).
- 6b: retraining, the gate, frozen-v1, FR0.3, O/E, the analysis-lock decision and Phase B, the `gold_fingerprint_definition`
  column (only if a promotion happens).
- If C8 WARNs on the sub-version or connector (FR14 partially met, see Verification): decide whether to read the batch's
  driver output, add a probe batch, or accept "3.0 + `spark_version`" as FR14's record (an owner/PRD decision).
- O1 (not chosen): a Dataproc pilot re-run with `--record-runtime` as an S2 rehearsal; would need an output-suffix option.
- Unchanged open items: V-post and L-post, the terraform-fmt pre-commit hook, the pandas 3 check, the 45 cross-machine QALY rows.
- The local pilot raw/gold copies (~8.4 GB under `data/`) can be deleted at any time (owner).

## Revision log

- r0 (2026-10-14): initial draft.
- r1 (2026-10-15): design check round 1 (comprehension passed; critic 14 gaps; readiness 9 questions). Loader refusals → C1
  FAIL + SKIP, C2's unreachable clauses dropped; exit codes and always-written report defined; `gather_inputs`/
  `evaluate_checks` API with seams (`runner`, `gold_opener`, `prereg_blob_at`); gold via `materialized_gold_dir` named
  correctly; import rule split into an AST test and a transitive subprocess test with `bundles.gcs/.manifest` allowed;
  v1 comparator anchored on `full_run_2b` (D6); C7 aligned with the owner's A7 (ii) (D7); C8 WARN/FAIL split, connector WARN,
  VERSION-keyed sub-version heuristic, `_JAR_RE` fullmatch; conf/SPARK_HOME/200-char tests; no download cache, local
  size + sha256 verified, gcloud's own crc32c check; C3 reuses P7 (D5) and names `generation_summary.provenance`; prereg
  `split`/`power` properties and `parse_yyyymmdd`; pilot comparator path; CLI precondition exit 2; no extra Spark run in CI;
  the `BigJoinConfig` hedge removed and the merge placed inside the existing `try`; L0 lists the raw bucket; L1 names the
  describe fields.
- r2 (2026-10-15): round 2 (critic 3 gaps; readiness 7 questions). Spark-free `main` wiring tests for `--record-runtime` and a
  C8-FAIL recovery step; C1 compares label definition, planned codes, window, lookback and reference date with prereg; the
  CLI parses prereg from HEAD's blob and refuses a locally edited `prereg.yaml`, recording the parsed bytes' sha256;
  test-file contradiction resolved; frame builders named; `prereg_blob_at` failure → C3 FAIL; precondition exits write no
  report; gold files recorded in `inputs`; `GatherError.inputs`; `run_ids` defined; injectable CLI `main`.
- r3 (2026-10-15): round 3 (readiness: ready; critic 3 gaps). C8 WARNs when no BigQuery connector jar is found and uses
  `re.search` for the version; the no-peeking rule lists each check's allowed `values` and adds an Id-leak test; every CLI
  failure before gathering (including `PreregError` and a failing `git status`) is a precondition: exit 2, no report. Also
  folded in readiness's slips: `parse_yyyymmdd`'s field argument, the loader's tuple, `REPO_ROOT`, operator ADC, `ExitStack`.
- r4 (2026-10-15): round 4 (readiness: ready; critic 1 gap). Hit the 3-revision cap; owner chose "apply fixes and run one
  more round". L1 expects the `~ generation_templates` output change; citations corrected (P7 183-212, `storage.tf:43`,
  `fake_gcloud.py` ranges); the fake's `size_overrides`.
- r5 (2026-10-15): round 5 (readiness: ready; critic 1 gap). FR14 closure defined separately from 6a's acceptance (met only
  without C8's sub-version/connector WARNs, otherwise "partially met" with the recorded facts listed). Rebutted: the critic's
  claim that the Spark version is not recorded; `spark_version` is a top-level v2 metadata field (`big_join.py:596`).
- r6 (2026-10-15): round 6 (readiness: ready; critic 3 gaps, all FR14 precision). The sub-version test requires `3.0.<n>`
  (from `RUNTIME_VERSION`), not any three-part version; the jar pattern is narrowed to `spark-…bigquery` and `gcs-connector`
  (excludes bigquerystorage client jars); one `runtime.jar.<basename>` key per jar, never truncated; jars are also read from
  the driver classpath (`java.class.path`, `spark.jars`, `spark.driver.extraClassPath`) and `/usr/local/share/google/dataproc/lib`.

## Implementation notes (2026-10-15, before the live run)

Design check: comprehension passed (round 1); readiness `implementation ready` from round 3 on; the critic did not close in the
same round as readiness (gaps 14 → 3 → 3 → 1 → 1 → 3); after r6 the owner chose "override and proceed". Deviations from the
text above, all small:
- `tests/test_tooling.py` (not in Surfaces touched) pins the Makefile's target set and variables: `check-population` and
  `CHECK_ARGS` added. The Makefile has no `?=` defaults for `RUN`/`GEN_ARGS`/`BATCH_ARGS`, so none was added for `CHECK_ARGS`.
- `write_report` also takes `generated_at_utc` (the CLI passes it).
- `/eg-precommit-review` (2 rounds) fixed: C7 crashed with `TypeError` when no patient had a DEATHDATE (the lookup lost its
  UTC zone through numpy; now `set_axis`); an unexpected `evaluate_checks` error now exits 2 with a report instead of 1;
  a `prereg_commit` read from GCS reaches git only as a 40-character lowercase hex sha, in C3 and in P7
  (`dataproc_batch.check_raw_matches_prereg`), and the CLI's `git show` passes `--end-of-options`.
- Sanity run on real local data (pilot raw, locally built pilot gold, v1): C1-C7b PASS, C8/C9 INFO, exit 0, every figure equal
  to 5b's (18,861 rows / 258 positives, 0 overlap with v1 raw and `full_run_2b`, 1 Death Certification, 1 gold and 0 modeling
  rows with `label_horizon` past the end, 62 test positives).
