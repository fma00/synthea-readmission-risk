# v2 slice 5: `gs://` Big Join, Spark-side gold fingerprint, pre-registration, 20k pilot (2026-10-12)

Status: DESIGN ACCEPTED BY OWNER OVERRIDE (2026-10-12) at revision 7. Gate note: the critic and readiness passes never
closed in the same round (round 6: readiness ready, critic 7; round 7: critic 9, readiness 6, all fixed in revision 7 but
not re-verified); the owner overrode after round 7, as in slices 2 and 4. History: revision 7 (round 1: comprehension passed, critic 24, readiness 13; round 2: critic 20, readiness 13;
round 3: critic 15, readiness 9; round 4: critic 11, readiness 5; the 3-revision cap was reached after round 4 and the
owner chose "apply the fixes and run one more round"; round 5: critic 12, readiness 5, owner chose "revise + one more
round"; round 6: critic 7, readiness READY; the 7 fixed in revision 6; round 7: critic 9, readiness 6, all fixed in revision 7;
see "Revision log" at the end). Executes PRD `notes/prds/v2-mlops-keyless-scheduled-scale-up-2026-10-09.md`
"Implementation hints" item 5 (FR10-FR13, plus FR3's `--py-files` zip). Branch: a feature branch off `v2`, PR into `v2`
(ruleset 24876876). Split by owner decision (Step 0, 2026-10-12) into:

- **5a (code-only PR, no GCP spend):** everything in "Interfaces" below: Python, Terraform *definitions*, tests, docs,
  and the committed `prereg.yaml`. Nothing is applied, uploaded, generated or run on GCP in 5a.
- **5b (live pilot, after 5a merges into `v2`):** the runbook in "Verification criteria > 5b live checks". Every live
  step (terraform apply, uploads, the VM, the Dataproc batch, the raw download) is confirmed with the owner first.

Shorthand used below (all from the PRD): FR = functional requirement; M = success metric (M6 pre-registration
precedes the 200k generation; M7 ≥ 400 test positives at 200k); G = PRD goal; F4 = slice 4's open follow-up "read the
billing report for slice 4's runs"; L0-L9 = this note's live checks; P1-P6 = preflight refusals; G-a/G-b = gates.

## Owner decisions (Step 0, 2026-10-12)

| # | Question | Decision |
|---|---|---|
| D1 | Pilot scope | The 20k pilot's Big Join runs on **Dataproc Serverless runtime 3.0** in this slice (FR13 needs DCU-hours, which only Dataproc produces); the slice is split into 5a (code) and 5b (live). |
| D2 | Generation VM | **Terraform instance template + gcloud launch.** Terraform holds the long-lived parts (service accounts, IAM, network, the instance template that fixes the VM's shape and every cost bound); a committed launcher creates one instance from the template per run and proves it gone afterwards. A Terraform VM resource was rejected: a self-deleted, preempted or timed-out VM leaves state drift, and a later unrelated `apply` would recreate it and start another generation. |
| D3 | Fingerprint transition | **Add a definition** (`spark-rowhash-v1`) next to `pandas-csv-sha256-v1` in `_FINGERPRINT_DEFINITIONS`; the v1 champion, its gold table and slice 4's GCS download are unchanged; no re-export. |
| D4 | Pre-registration enforcement | `prereg.yaml` at the repo root with a schema test; the generation launcher and the batch submitter **refuse** unless it is committed, unmodified, the working tree clean and HEAD on a remote branch, and stamp the prereg commit + sha256 into the VM's metadata/labels and the run's provenance. Every analysis value is fixed now; the pilot may inform only operational values; any later change is an appended, committed deviation. |
| D5 | Seeds and dates | Owner-chosen before any data exists: **pilot seed 17 (20,000 patients), 200k seed 29 (200,000 patients), `reference_date` 20261012 for both.** The pilot seed MUST differ from the 200k seed: measured 2026-10-12 with the project's Synthea jar, a 30-patient run with seed 7 is a strict subset of a 60-patient run with seed 7 (30/30 patient Ids shared). Different seeds make an overlap very unlikely but this was not measured; slice 6 checks that the pilot's and the 200k run's patient-Id sets are disjoint. Pilot gold is used for operational and pipeline-sanity checks only, never for any model, threshold or rule choice. |
| D6 | Cost guard | Pilot VM: spot, `max_run_duration` 2 h, termination action DELETE, auto-delete disk, labels. Dataproc: `maxExecutors=7`, batch `ttl` 2 h. DCU-hours from the batch's `runtimeInfo.approximateUsage.milliDcuSeconds`. Billing report read about 24 h later. Projected pilot spend ≈ $1-3 (+ ≈ $0.5 egress for the raw download, L7, required since revision 5). The 200k run's `max_run_duration` is set in slice 6 from the pilot's measured time, not now. |

**New finding during the design check (measured 2026-10-12), affects D5 and CLAUDE.md:** Synthea has a separate
clinician seed (`-cs`). Two 30-patient runs with `-s 7` and no `-cs` produced identical `patients.csv` content (sorted)
but **different `encounters.csv` (3,878 differing lines) and `providers.csv` (188)**; with `-s 7 -cs 7` all three files
were identical after sorting (row order still varies: Synthea writes from several threads). So "Synthea generation is
deterministic per seed" (PRD NFR Reproducibility, CLAUDE.md) holds only with `-cs` fixed; v1's raw data (generated
without `-cs`) cannot be regenerated byte-for-byte. This slice pins `clinician_seed` per population in `prereg.yaml`
(proposal: equal to the population seed, 17 and 29; signed off at G-a) and passes `-cs`. The order-independent
fingerprint makes the varying row order irrelevant downstream. CLAUDE.md gets a correction after the slice.

**Extended in revision 3 (measured 2026-10-12, two 1,000-patient runs, all 10 tables, `-s 7 -cs 7 -r 20261012`):** 8 of
10 tables identical after sorting; two were not. (a) `encounters.csv`: 2 rows differed, only in `STOP`, by the wall-clock
time of the run (07:05:39Z vs 07:06:00Z): Synthea's simulation END defaults to *now*, not to `-r`. With `-e 20261012`
added, two 300-patient runs were identical (encounters, patients, procedures) and the latest `STOP` was before the end
date. This variation could reach the gold (`label_horizon` includes in-window stays' `STOP`), so `prereg.yaml` pins
`generation.end_date` and generation passes `-e`. **Corrected in revision 5 (round-5 critic, measured):** the Big Join
treats observation as running to the END of `reference_date` (`R_end = reference_date + 1 day` at 00:00Z,
`big_join.py:189` and `:275`), while `-e 20261012` stops the simulation at the START of that day (latest `STOP`
2026-10-11T15:30Z; Synthea parses the date in the JVM's zone, CEST on the laptop). So `end_date` = `reference_date` + 1
day (20261013): with `-e 20261013` under `-Duser.timezone=UTC` the latest record was 2026-10-12T14:03Z, inside the
reference day. **Synthea output also depends on the JVM time zone** (measured: identical seeds/dates/population, UTC vs
America/New_York → different `patients.csv` and `encounters.csv`, 19,404 vs 19,490 encounter rows), so generation pins
`-Duser.timezone=UTC` (`prereg.yaml` `generation.jvm_timezone: UTC`). v1 was generated in the laptop's local zone: one
more reason it is not byte-reproducible (CLAUDE.md correction). (b) `payers.csv`: the aggregate columns
`AMOUNT_COVERED`, `AMOUNT_UNCOVERED`, `REVENUE`, `QOLS_AVG` differ (thread-order floating-point accumulation); the Big
Join reads only `Id` and `OWNERSHIP` from payers, so the gold is unaffected; stated, not fixed. **v1 consequence:** v1's
raw data runs past its `reference_date` (latest encounter `START` 2026-09-26 vs reference 2026-09-16), because the
simulation ended at the generation's wall clock; the Big Join's censoring keeps the labelled rows' outcome windows before
the reference date, so this is recorded as a CLAUDE.md correction, not re-analysed here. Same runs: exactly the 10
requested files were written (with the jar's default `exporter.csv.excluded_files = patient_expenses.csv` still set),
alive patients = 1,000 exactly (1,168 rows incl. deaths), peak RSS 13.0 GB at 1k (18-core laptop, default heap), 21 s.

## Why

The v1 pipeline proves itself only on a laptop at 10k patients, and its results are underpowered (41 test positives,
CLAUDE.md caveat 2) and not a virgin holdout (caveat 1). v2's scale-up needs: the same Big Join code running against
GCS on Dataproc (FR11), a content fingerprint computed where the gold table is written, so that edits which preserve the
row and positive counts are detected (FR12; CLAUDE.md names this gap as "a content fingerprint is a named follow-up"), a
pre-registration committed before the validation population exists (FR10, M6), and a 20k pilot that measures
generation and Big Join cost and time on the real shapes before the 200k run (FR13).

## Scope

**In (5a):**
1. FR11: `gs://` support for the Big Join (inputs, existence/emptiness checks, `generation_summary.json`, output-exists
   check, Parquet write, `_gold_metadata.json` write), the run-big-join CLI, and the training CLI (`--gold-dir`).
   `make_spark_session` takes master and config from the caller. Local behaviour unchanged; existing tests unchanged
   except the enumerated deliberate edits in "Verification > Existing tests changed on purpose".
2. FR12: `spark-rowhash-v1`, an order-independent fingerprint over all 24 gold columns, computed in Spark when the gold
   table is written, recorded in `_gold_metadata.json` (`metadata_version` 1 → 2), recomputed and checked by the loader
   (training and scoring) and by guard 6 for bundles that name the new definition. Training records it as a tag only
   when the gold metadata is version 2; the exporter uses it for runs that carry the tag.
3. FR10: `prereg.yaml` (Phase A) committed, validated by tests, enforced by the launcher and submitter.
4. FR3: a cross-platform byte-deterministic `--py-files` zip of exactly the Spark job's sources.
5. FR13: generation changes (`-cs`, `exporter.csv.included_files`, an explicit JVM heap, peak-memory and jar-hash
   recording), the VM startup script, the generation launcher, the Dataproc batch submitter, and the Terraform for: a
   dedicated network/subnet, two buckets (`artifacts`, `dataproc-staging`), two service accounts
   (`synthea-generator`, `dataproc-runtime`) with additive IAM, the instance template, and the two already-enabled APIs.
6. Docs and tooling: `docs/setup.md` section "Scale-up pilot" and its teardown checklist; Makefile targets
   `spark-zip`, `generate-vm`, `bigjoin-batch` (variables below); CLAUDE.md update after the slice. (`.gitignore`
   already ignores `dist/` and `/data/`; no change.)

**In (5b):** the live runbook L0-L9 (below) for the 20k pilot only; results go into this note's Results section.

**Out (named follow-ups, not built):** the 200k generation and Big Join (slice 6; both CLIs refuse `main_200k` unless
`--allow-main-run`, and 5b never passes it); training/retraining at 200k, the promotion gate, frozen-v1 evaluation, and
computing `observed_expected_ratio` in `models/evaluate.py` (slice 6; this slice only pre-registers them); BigQuery
direct-write of gold and the connector-0.44.0 MERGE test (FR14, slice 6); recording the Dataproc runtime *sub-version*
and connector version in metadata (FR14, slice 6; 5a records `spark_version` in metadata and `runtimeConfig.version` in
the local batch record); a GitHub Actions workflow and a WIF-bound submitter SA for the Big Join (slice 6/7; the pilot is
laptop-run as the owner); removing `roles/editor` from the default compute SA (Data handling > IAM); the GCS connector
for local `local[*]` against `gs://` (the PRD's degraded mode; documented, not built); label v2; the lockfile-drift CI
check and the terraform-fmt pre-commit hook (still-open TODOs, unchanged).

## Surfaces touched

New:
- `prereg.yaml` (repo root)
- `src/readmission_risk/pipeline/fingerprint.py` (stdlib + pandas; imported by the Spark job, the loader and the scorer)
- `src/readmission_risk/pipeline/storage.py` (local vs Hadoop-FileSystem storage seam; ships with the Spark job;
  imports pyspark lazily inside `HadoopStorage`, so importing the module needs no pyspark)
- `src/readmission_risk/ops/__init__.py`, `ops/prereg.py`, `ops/spark_zip.py`, `ops/gcloud.py`, `ops/generation_vm.py`,
  `ops/dataproc_batch.py` (laptop-side operations; never shipped to Spark, never imported by the scorer)
- `scripts/build_spark_zip.py`, `scripts/launch_generation.py`, `scripts/submit_big_join_batch.py` (thin CLIs)
- `infra/network.tf`, `infra/compute.tf`, `infra/generation/startup.sh`; edits to `infra/apis.tf`, `infra/storage.tf`,
  `infra/iam.tf`, `infra/variables.tf`, `infra/outputs.tf`
- tests: `tests/pipeline/test_fingerprint.py`, `tests/pipeline/test_storage.py`, `tests/ops/` (new package:
  `__init__.py`, `test_prereg.py`, `test_spark_zip.py`, `test_generation_vm.py`, `test_dataproc_batch.py`)

Changed (line numbers checked 2026-10-12):
- `src/readmission_risk/pipeline/big_join.py`: `BigJoinConfig` (L96-102), `_count_data_rows` (L105-107, removed),
  `load_synthea_tables` (L110-123), `build_big_join` (L456-548: output-exists L468-469, summary read L471-481, write +
  read-back L520-525, metadata L527-540)
- `src/readmission_risk/pipeline/gold_metadata.py`: version 2, new fields, the definition constant, text-level
  serialize/parse split out of `write_gold_metadata` (L123-134) / `read_gold_metadata` (L137-182)
- `src/readmission_risk/pipeline/spark_session.py`: signature (L14)
- `src/readmission_risk/pipeline/generation.py`: config (L55-66, plus `__post_init__`), `build_synthea_command`
  (L92-117), summary (L208-216)
- `src/readmission_risk/models/data.py`: loader fingerprint check (after the count check, L188-192)
- `src/readmission_risk/models/training.py`: one conditional tag next to L263-266; one param `gold_source`
- `src/readmission_risk/models/split.py`: new constant `SPLIT_SUMMARY_KEYS` (the summary dict's keys, L195-215); no
  behaviour change
- `src/readmission_risk/models/bundle_export.py`: definition choice (L88-89)
- `src/readmission_risk/bundles/manifest.py`: imports the new constant from `pipeline.gold_metadata` (stdlib-only, so the
  module's "Standard library only" rule holds)
- `src/readmission_risk/scoring/bundle_guard.py`: `_FINGERPRINT_DEFINITIONS` (L70); guard 6 (L385-398) gains a metadata
  cross-check
- `scripts/run_big_join.py` (L20-27, L40-55), `scripts/generate_synthea_data.py` (args), `scripts/train_models.py` (L45)
- `tests/conftest.py`: `_no_real_gcp` also patches `readmission_risk.ops.gcloud.default_runner`
- `tests/models/helpers.py::write_test_gold_metadata` (L199-221), `tests/pipeline/test_gold_metadata.py` (builder L20-34)
- `Makefile`, `docs/setup.md`, `requirements.in` (`pyyaml` made a direct pin; it is
  already locked at 6.0.3 as a transitive dependency, so the recompile changes only the `# via` annotation)

Data: new GCS prefixes `gs://synthea-readmission-risk-raw/v2/<run>/` and `gs://synthea-readmission-risk-gold/v2/<run>/`
(5b creates `<run>` = `pilot_20k` only). Existing `gs://…-gold/full_run_2b/` and local `data/` are not touched.

## Interfaces

### 1. `pipeline/fingerprint.py` (FR12) — stdlib + pandas, no pyspark

```python
from .gold_metadata import FINGERPRINT_DEFINITION_ROWHASH_V1   # = "spark-rowhash-v1"; the constant lives in the stdlib-only module
GOLD_COLUMN_KINDS: dict[str, str]   # all 24 gold columns -> one of "string", "int", "double", "timestamp"
#   string: patient_id, encounter_id, admission_reason_code, admission_reason_description, payer_ownership,
#           provider_specialty, gender, race, ethnicity, marital_status
#   int:    is_readmitted, organization_utilization, prior_encounter_count, prior_inpatient_count, prior_emergency_count,
#           active_condition_count, active_medication_count, procedure_count_window, active_careplan_count,
#           observation_count_window
#   double: age_at_index_years
#   timestamp: index_start, index_stop, label_horizon

def canonical_row_bytes(frame: pd.DataFrame) -> list[bytes]: ...
def partial_sums(frame: pd.DataFrame) -> tuple[int, int, int]: ...   # (n_rows, sum_h1, sum_h2), Python ints, exact
def combine(n_rows: int, sum_h1: int, sum_h2: int) -> str: ...      # 64 lowercase hex
def rowhash_fingerprint(frame: pd.DataFrame) -> str: ...            # combine(*partial_sums(frame)); never mutates frame
```

Canonical row (the definition; any change is a new definition name):
- Columns in `sorted(GOLD_COLUMN_KINDS)` order (name order, so column order in the frame never matters); a frame whose
  column set differs from `GOLD_COLUMN_KINDS` raises `ValueError` naming the missing/extra columns.
- Each cell becomes `<name>=<token>`; cells joined with `\x1f`; the row encoded UTF-8.
- Extraction (fixed, so every caller presents the same Python objects): for each column in name order,
  `values = frame[col].astype(object).tolist()` (datetime64 columns then yield `pd.Timestamp` / `pd.NaT`, numeric
  columns Python or NumPy scalars, strings `str` or `None`/`pd.NA`).
- Null: decided FIRST, before any type check, by `pd.isna(v) is True` for a scalar `v` (covers `None`, `pd.NA`,
  `pd.NaT`, `numpy.datetime64("NaT")`, Python/NumPy float NaN); token `N`. (pandas holds
  nulls in double and nullable-int columns as NaN, and Arrow-backed / pandas-3 string columns as `pd.NA`; a genuine
  NaN therefore hashes like a null; the Big Join cannot produce one.)
- string: `S<len>:<value>` (`len` = Python `len`, code points; the length prefix makes the encoding injective, so
  separator characters inside values cannot collide). A non-`str` value raises `ValueError`.
- int: `I<decimal>` of `int(v)`. Accepts Python ints, NumPy integers, and integer-valued Python/NumPy floats (Arrow →
  pandas turns a nullable int column with nulls into float64). Rejected with `ValueError`: `bool`, `numpy.bool_`, a
  non-integer float.
- double: `D<float.hex(float(v))>` (exact IEEE-754, platform- and JDK-independent; `-0.0` and `0.0` differ, identically on
  both sides). `bool`/`numpy.bool_` rejected.
- timestamp: `T<YYYY-MM-DDTHH:MM:SS.ffffffZ>` in UTC, from `pd.Timestamp(v)`. Accepted input types only:
  `pd.Timestamp`, `datetime.datetime`, `numpy.datetime64` (and the nulls above); a `str`, `int`, `float` or any other
  type raises `ValueError` naming the column (test `test_timestamp_rejects_str_and_int`). A tz-aware value is converted to UTC; a
  tz-naive value is taken as UTC (callers guarantee it: the Spark path asserts the session time zone is `UTC`, the
  loader produces tz-aware UTC). A non-zero `nanosecond` raises `ValueError`.
- Per row: `h1 = int.from_bytes(hashlib.sha256(row).digest()[:8], "big")`,
  `h2 = int.from_bytes(hashlib.blake2b(row, digest_size=8, person=b"rowhash-v1").digest(), "big")`. Sums are
  multiset-sensitive (a duplicated row changes them; XOR would cancel) and order-independent.
- `combine` = `hashlib.sha256(f"spark-rowhash-v1\n{n_rows}\n{sum_h1}\n{sum_h2}\n".encode("ascii")).hexdigest()`.

**Deviation from the PRD's wording, stated explicitly:** FR12 says "canonical per-row strings … fixed float format",
which suggests Spark SQL expressions (`format_string`/`date_format`). It is implemented instead as ONE Python
canonicalizer that runs inside Spark's Python workers (`mapInPandas`) for the Spark path and directly for the loader /
guard path. Reason: every Spark double→string conversion goes through Java (`Double.toString`, `DecimalFormat`,
`Formatter`), whose shortest-digit output changed in JDK 19 (JDK-4511638), and this project runs Java 17 (CI
`lint-and-test`) and Java 21 (Dataproc runtime 3.0); a fingerprint that differs by JDK would be useless. The Spark
contribution is the distributed, order-independent application plus the `decimal(38,0)` accumulation, exactly as FR12
asks. The two paths still differ in how values reach the canonicalizer (Arrow batches inside Spark vs the loader's
normalized dtypes), which is what the agreement test checks.

Spark side, in `big_join.py` (pyspark):

```python
def spark_rowhash_fingerprint(spark: SparkSession, gold: DataFrame) -> tuple[str, int]:
    """(fingerprint, n_rows). Raises RuntimeError unless spark.conf.get("spark.sql.session.timeZone") == "UTC".
    gold.mapInPandas(per-batch partial_sums -> one row of three decimal strings, schema "n string, s1 string, s2 string"),
    then cast each to decimal(38,0) and F.sum. Over zero rows the sums are null -> 0."""
```
Range: per-row values < 2^64 ≈ 1.8e19; with ≤ 10^7 rows the sums stay < 2e26, far below `decimal(38,0)`'s 10^38, so ANSI
mode cannot overflow. `build_big_join` calls it on the Parquet it just wrote, read back (like today's count), so the
recorded fingerprint describes the bytes on storage.

### 2. `gold_metadata.py` (FR12) — still stdlib only

- New constants: `FINGERPRINT_DEFINITION_ROWHASH_V1 = "spark-rowhash-v1"`; `GOLD_METADATA_VERSION = 2`;
  `SUPPORTED_GOLD_METADATA_VERSIONS = (1, 2)`.
- `GoldMetadata` gains five fields, after the existing ones, each with default `None` (`V2_ONLY_FIELDS`, in this order):
  `gold_fingerprint: str | None`, `gold_fingerprint_definition: str | None`, `spark_version: str | None`,
  `generation_summary: dict | None`, `build_provenance: dict | None`. `build_provenance` (v2: a dict of non-empty str
  → non-empty str, possibly empty for a local run) records the code that built the gold: `run_big_join.py` gains a
  repeatable `--provenance KEY=VALUE` (argparse `action="append"`, then the shared stdlib parser
  `readmission_risk.pipeline.generation.parse_provenance_args(values: list[str] | None) -> dict[str, str]`, which raises
  `ValueError` on a missing `=`, an empty key or value, or a repeated key; both CLIs turn that into `parser.error(...)`,
  exit 2) passed to `BigJoinConfig.provenance: Mapping[str, str] | None = None` (`None` → `build_provenance = {}` in
  the written v2 metadata); the submitter passes `head_commit`, `zip_sha256`, `main_sha256` (of
  `run_big_join.py`), `prereg_commit`, `batch_id`. Deep-copied like `generation_summary`. The `metadata_version` default becomes 2 (the writer's version).
- `_validate`: version 2 ⇒ the three strings are non-empty, `build_provenance` is a dict (possibly empty) of non-empty
  str → non-empty str (`None` refused), `gold_fingerprint` fullmatches `[0-9a-f]{64}`, definition ==
  `FINGERPRINT_DEFINITION_ROWHASH_V1`, and `generation_summary` is a dict with str keys whose `reference_date` equals
  `metadata.reference_date` (the parsed content of the raw run's `generation_summary.json`, embedded so the gold's
  lineage to its raw data and prereg survives the raw bucket's 90-day deletion; see §8 `provenance`). The writer and the
  parser store `copy.deepcopy` of the dict, so mutating the caller's object never changes validated metadata; the frozen
  dataclass is therefore unhashable (`__hash__` raises `TypeError`); nothing in the code hashes it (checked: no
  `hash(`/set/dict-key use of `GoldMetadata`). Version 1 ⇒ all
  five are `None`. Any other version: `unsupported version N (this code reads 1, 2)`.
- `metadata_to_dict(metadata) -> dict`: `asdict(metadata)` with the five v2-only keys removed when the version is 1 (a v1
  dict's key set is exactly today's 12). `metadata_to_json(metadata) -> str`: validate, then
  `json.dumps(metadata_to_dict(m), indent=2, sort_keys=True) + "\n"` (a v1 file's bytes are exactly today's).
- `parse_gold_metadata(text: str, source: str) -> GoldMetadata`: today's JSON checks (duplicate keys, top-level object),
  then reads `metadata_version` first (missing → "missing key(s) ['metadata_version']"; `type(v) is not int` (so `True`
  and `1.0` are refused, as `BAD_VALUES` L170 expects) or not in `SUPPORTED_GOLD_METADATA_VERSIONS` → the
  `metadata_version` error naming the key, before any key-set check), then
  version-aware key checks: a v1 file must have exactly today's 12 keys, a v2 file those + the five; then `_validate`.
  All errors `ValueError` prefixed with `source`.
- `write_gold_metadata(gold_dir: Path, …)` and `read_gold_metadata(gold_dir: Path)` keep their signatures and behaviour
  (local, atomic temp + `os.replace`) and call these two.
- The existing `data/gold/full_run_2b/_gold_metadata.json` (version 1) reads unchanged.

### 3. `pipeline/storage.py` (FR11)

```python
class Storage(Protocol):
    def join(self, base: str, *parts: str) -> str: ...
    def exists(self, uri: str) -> bool: ...
    def is_file(self, uri: str) -> bool: ...
    def has_data_row(self, uri: str) -> bool: ...        # True iff >= 2 lines (header + one row); reads <= 2 lines; FileNotFoundError if missing
    def read_text(self, uri: str) -> str: ...           # UTF-8; FileNotFoundError if missing
    def write_text_new(self, uri: str, text: str) -> None: ...  # FileExistsError if it exists; local: temp + os.replace

class LocalStorage: ...                                 # pathlib; join = str(Path(base, *parts))
class HadoopStorage:
    def __init__(self, spark: SparkSession) -> None: ...  # join = base.rstrip("/") + "/" + "/".join(parts)

def storage_for(location: str | Path, spark: SparkSession | None) -> tuple[Storage, str]:
    """Path, or a str with no "<scheme>://" prefix -> (LocalStorage(), str(location));
    'gs://bucket/...' -> (HadoopStorage(spark), uri with one trailing '/' stripped); ValueError if spark is None;
    any other '<scheme>://' (s3://, file://, ...) -> ValueError('unsupported location scheme ...'). The set of schemes
    routed to HadoopStorage is the module constant _HADOOP_SCHEMES = ("gs",) (a test seam; production never changes it).
    Mixing local input with gs:// output (or the reverse) is allowed; each location gets its own storage."""
```
`HadoopStorage` uses `spark._jvm.org.apache.hadoop.fs.Path(uri).getFileSystem(spark._jsc.hadoopConfiguration())`
(`exists`, `getFileStatus().isFile()`, `open` + `java.io.BufferedReader(InputStreamReader(…, "UTF-8"))`, `create(path,
False)` + `write(bytearray)`), so it uses exactly the filesystem and credentials Spark itself uses (the GCS connector on
Dataproc). It needs classic (non-Connect) Spark: a Dataproc Serverless PySpark *batch* runs classic Spark (Spark Connect
applies to interactive sessions); the constructor raises `RuntimeError("HadoopStorage needs a classic SparkSession (no
_jvm on this session)")` if `spark` lacks `_jvm`/`_jsc`, so a mismatch fails in the first second of the batch.
Error mapping: every method checks `fs.exists(path)` first and raises the Python exception the Protocol names
(`FileNotFoundError` / `FileExistsError`) with the URI in the message; any `py4j.protocol.Py4JJavaError` raised by a Hadoop
call (including `getFileSystem` failing with "No FileSystem for scheme \"gs\"" when a local Spark has no GCS connector)
is re-raised as `OSError(f"{uri}: {java message's first line}")` from the original. Catching it needs
`from py4j.protocol import Py4JJavaError` (imported inside `HadoopStorage`'s methods): **FR3 widening**, `py4j` is added to
`tests/pipeline/test_runtime_dependencies.py::RUNTIME_BUNDLED_IMPORTS_ALLOWED` as `{"py4j": "py4j"}`; it is PySpark's own
hard dependency, so every PySpark install, Dataproc's included, ships it, and `py4j==0.10.9.9` is already hash-pinned in
`requirements-dataproc-3.0.txt` (line 135) and `requirements.txt`, so that test's lockfile check passes unchanged. `scripts/run_big_join.py` adds
`OSError` to the exceptions it reports as `ERROR:` with exit 1, appending, for the scheme case, "local Spark has no GCS
connector; run this on Dataproc (the PRD's local[*]-on-a-VM fallback is not built)". It is unit-tested against `file://` URIs (Hadoop's local filesystem) by constructing `HadoopStorage`
directly; `storage_for` never routes `file://` to it (local paths stay on pathlib, so no `.crc` side files appear in local
gold directories). `LocalStorage.has_data_row` replaces `_count_data_rows` (the existing `== 0` check is exactly "has no
data row"; reading two lines instead of counting matters at 200k, where `procedures.csv` is several GB).

### 4. `big_join.py` changes (FR11, FR12)

- `BigJoinConfig.input_dir: Path | str`, `output_dir: Path | str`. A `__post_init__` raises
  `ValueError("gs:// locations must be passed as str, not Path: …")` if either field is a `Path` whose string starts with
  `gs:/` (`Path("gs://b/x")` silently collapses to `gs:/b/x`); the other existing validations stay where they are.
- `load_synthea_tables(spark, input_dir: Path | str)`: same order of checks, same message texts (the path rendered as
  today for local paths, the URI for gs://), via `storage_for`. Spark reads `storage.join(input, "csv", name)` as a str.
- `build_big_join`: output-exists check, summary read (now validated at read time, before any Spark work: the parsed
  JSON must be a dict, else `ValueError(f"{summary_path} must hold a JSON object")`; the existing `reference_date` checks
  follow, so a malformed summary can never leave Parquet without metadata, and a JSON string no longer raises
  `TypeError`), Parquet write and read-back via strings; then
  `fingerprint, n_fp = spark_rowhash_fingerprint(spark, written)`; `RuntimeError` if `n_fp != n_rows` (internal
  consistency); writes metadata v2 (`gold_fingerprint`, `gold_fingerprint_definition`, `spark_version=spark.version`,
  `generation_summary` = the summary object it already parsed for the `reference_date` check)
  via `output_storage.write_text_new(join(output, GOLD_METADATA_FILENAME), metadata_to_json(m))` for gs:// and the
  unchanged `write_gold_metadata` for local. Written LAST, as today. Return value unchanged (the gold DataFrame).
- Not changed: every transformation function, `GOLD_TABLE_COLUMNS`, the label and its rules.

### 5. `spark_session.py` (FR11)

```python
@dataclass(frozen=True)
class BuilderSettings:
    master: str | None             # None: the builder gets no .master() call
    conf: dict[str, str]           # applied with builder.config(k, v), in sorted key order; always contains
                                   # "spark.sql.session.timeZone": "UTC"
    env_defaults: dict[str, str]   # os.environ.setdefault for each; {"PYSPARK_PYTHON": sys.executable} iff master
                                   # starts with "local", else {}

def builder_settings(master: str | None, conf: Mapping[str, str] | None) -> BuilderSettings: ...
    # ValueError if conf sets spark.sql.session.timeZone to anything but "UTC"
def make_spark_session(app_name: str = "big-join", *, master: str | None = "local[*]",
                       conf: Mapping[str, str] | None = None) -> SparkSession: ...
    # applies builder_settings(...), getOrCreate(), then spark.conf.set("spark.sql.session.timeZone", "UTC") as today
```
Existing callers (`tests/conftest.py`, `scripts/run_big_join.py`) keep working unchanged by default.

### 6. CLIs (FR11)

- `scripts/run_big_join.py`: `--input-dir`/`--output-dir` become `str`; new `--spark-master` (default `local[*]`; the
  literal `external` ⇒ `master=None`, used by the Dataproc batch); after the build it reads the metadata via the output's
  storage + `parse_gold_metadata` and prints as today, plus `Fingerprint: <hex> (spark-rowhash-v1)`. Errors as today
  (exit 1 with `ERROR:`).
- `scripts/train_models.py`: `--gold-dir` becomes `str`; BEFORE any download the CLI validates `--reference-date`,
  `--test-start-date` and `--train-start-date` with `parse_yyyymmdd` (exit 1 with the field name, as today), so
  `tests/models/test_cli.py:89`'s "rejected before any side effect" also holds for `gs://`; then it wraps training in
  `warehouse.gold_source.materialized_gold_dir(gold_dir)` (slice 4's download; local paths pass through untouched;
  512 MB / 1,000-object limits unchanged; 200k gold extrapolates to ≈ 25 MB). The original `--gold-dir` string is passed
  to `TrainingConfig` as a new last field `gold_source: str | None = None` and logged as MLflow param `gold_source`
  (`config.gold_source if not None else str(config.gold_dir)`; existing constructions in tests stay valid), so a run trained from
  `gs://` is traceable (error messages inside training may show the temporary path; the CLI's own messages name the
  URI). The loader stays local-only, so `models/data.py`'s `is_dir()`/`glob` need no change: FR11 is met at the CLI
  layer, the same seam slice 4 used. `gold_source` is not a manifest field (the manifest's `params` subset is fixed), so
  bundle ids are unaffected.
- `scripts/generate_synthea_data.py`: new optional `--clinician-seed INT`, `--end-date YYYYMMDD`, `--jvm-timezone TZ`, `--included-files`
  (comma-separated, or `default` ⇒ the 10 `EXPECTED_CSV_TABLES`), `--java-max-heap` (e.g. `96g`),
  `--require-alive-population`, repeatable `--provenance KEY=VALUE` (`action="append"` + the shared
  `parse_provenance_args` in `pipeline/generation.py`; an error → `parser.error`, exit 2, nothing generated).

### 7. Loader, training, exporter, guard 6 (FR12, D3)

- `load_gold_table_with_metadata` (models/data.py): immediately after the dtype normalization block (L194-203: label
  int8, counts int64, age float64, strings object, timestamps UTC) and before the duplicate-`encounter_id` check (L205),
  if `metadata.metadata_version == 2`, recompute `rowhash_fingerprint(df)` on that normalized frame (order-independent,
  so before the sort) and raise `ValueError` naming both values if it differs from
  `metadata.gold_fingerprint` ("the table was edited, or is not the one its metadata describes"). Version 1: no check
  (as today, stated in the docstring). This closes CLAUDE.md's "edits that preserve both counts are NOT detected" gap
  for v2 gold tables, as an INTEGRITY check, not a security control: someone who edits the Parquet can also rewrite the
  metadata (e.g. as version 1, which skips the check). Bundles exported as `spark-rowhash-v1` stay protected regardless,
  because guard 6 recomputes from the frame and compares with the manifest, which the gold directory cannot change.
- `training.py`: if `metadata.metadata_version == 2`, add tag `gold_fingerprint_spark_rowhash_v1 =
  metadata.gold_fingerprint` (already verified equal to the frame by the loader) next to the unchanged
  `gold_fingerprint` (pandas-csv) tag. Version-1 gold (including `full_run_2b` and every existing test fixture) gets no
  new tag, so runs trained on it export exactly as today. This is intended: the rowhash definition is used only where
  the gold carries a Spark-computed value to compare against.
- `training.py:293` logs `asdict(metadata)` as the `gold_metadata.json` MLflow artifact; it switches to
  `metadata_to_dict(metadata)`, so a v1-gold run's artifact is byte-for-byte today's (no three null keys).
- `bundle_export.py`: `gold_fingerprint_definition` = `"spark-rowhash-v1"` and `gold_fingerprint` = the new tag's value
  if the run has the tag; otherwise `FINGERPRINT_DEFINITION_V1` and the `gold_fingerprint` tag (today's behaviour, so
  re-exporting the v1 runs is still an idempotent no-op with the same `bundle_id`).
- `bundles/manifest.py`: re-exports `FINGERPRINT_DEFINITION_ROWHASH_V1` (`from readmission_risk.pipeline.gold_metadata
  import FINGERPRINT_DEFINITION_ROWHASH_V1`, listed in the module's names; stdlib-only, so manifest.py stays "Standard
  library only") so that `bundle_export.py` and `bundle_guard.py` keep importing BOTH definition constants from one place,
  `bundles.manifest`. Its source of truth is `gold_metadata.py` (the Spark job writes it and must not import `bundles`);
  `FINGERPRINT_DEFINITION_V1` stays defined in manifest.py. No new validation in manifest.py (guard 6 checks definitions).
- `bundle_guard.py`: `_FINGERPRINT_DEFINITIONS` gains `FINGERPRINT_DEFINITION_ROWHASH_V1: rowhash_fingerprint`.
  Guard 6 is otherwise unchanged (still recomputes from the loaded frame; never trusts a recorded value alone) and
  gains one strengthening: if the manifest's definition is `spark-rowhash-v1` and the gold metadata is version 2, the
  metadata's recorded `gold_fingerprint` must also equal the recomputed value (refusal message names both). In the real
  scoring path (`scoring/pipeline.py:89` loads through `load_gold_table_with_metadata`) the loader already refuses such a
  table, so this branch is defence in depth for direct `validate_bundle(bundle_uri, gold=…, metadata=…)` callers; its
  tests call `validate_bundle` directly. No guard is weakened; the guard count stays 14 (this is part of guard 6).

### 8. `generation.py` (FR13)

- `SyntheaGenerationConfig` gains `clinician_seed: int | None = None`, `end_date: str | None = None` (YYYYMMDD, validated
  with `parse_yyyymmdd`-equivalent stdlib logic, must be > `reference_date`), `jvm_timezone: str | None = None` (→
  `-Duser.timezone=<value>` directly after `"java"`; must fullmatch `[A-Za-z_]+(/[A-Za-z_]+)*`), `included_files: tuple[str, ...] | None =
  None`, `java_max_heap: str | None = None`, `require_alive_population: bool = False`, `provenance: Mapping[str, str] |
  None = None`. All at their defaults ⇒ today's argv exactly. The summary ALWAYS gains the new keys, `provenance` included (null when unset);
  existing summary tests assert individual keys only (`test_generation.py:263-267`), so they pass unchanged. `provenance` (keys and values non-empty str) is written verbatim into the
  summary under key `provenance`; the VM passes `run`, `prereg_commit`, `prereg_sha256`, `head_commit`, `zip_sha256`,
  `synthea_jar_sha256` (the hash its `sha256sum -c` just verified)
  through the CLI's repeatable `--provenance KEY=VALUE`. Via the Big Join's `generation_summary` metadata field (§2) this
  links every v2 gold table to its raw run and prereg durably.
- Validation in a new `__post_init__` (the dataclass is frozen; validation only reads): `ValueError` if `included_files`
  does not contain every `EXPECTED_CSV_TABLES` entry, contains a duplicate, or a name not ending in `.csv`; if
  `java_max_heap` does not fullmatch `[1-9][0-9]*[mg]`; if `clinician_seed` is a bool or not an int.
- argv order: `["java", "-Duser.timezone=<tz>" (if set), "-Xmx<heap>" and "-XX:+ExitOnOutOfMemoryError" (both only if java_max_heap is set; a worker-thread
  OOM then ends the JVM non-zero instead of leaving a silently short population), "-jar", jar, "-p", n, "-s", seed,
  "-cs", cs (if set), "-r", date, "-e", end_date (if set),
  "--exporter.baseDirectory", …, (existing exporter flags unchanged), "--exporter.years_of_history", y,
  "--exporter.csv.included_files", ",".join(files) (if set), state]` — the state stays last (positional).
- `require_alive_population=True` (the CLI flag `--require-alive-population`, which the VM passes): after validation,
  count `patients.csv` rows with an empty `DEATHDATE` (stdlib `csv`); fewer than `population_size` →
  `SyntheaValidationError`. Its constructor gains a keyword-only `message: str | None = None` (default: today's
  "missing=…" text, so existing raises and tests are unchanged); this raise passes the validation result it already has
  plus `message=f"Synthea exited 0 but produced {n} alive patients, expected {population_size}"`. Synthea's `-p` counts alive patients (measured: 1,000 alive of 1,168).
- `generation_summary.json` gains: `clinician_seed`, `end_date`, `n_alive_patients` (null unless counted),
  `included_files` (list or null), `years_of_history`, `state`,
  `java_max_heap`, `jvm_timezone`, `peak_child_rss_bytes`
  (`resource.getrusage(RUSAGE_CHILDREN).ru_maxrss` after the subprocess: KiB on Linux, bytes on macOS, normalized to
  bytes via `sys.platform`; it is the largest peak RSS among the finished children of the generation process; the
  only child is the `java` process, so on the VM it is the JVM's peak). Existing keys unchanged.
- `generation.py` stays stdlib-only, so it runs on the VM's system Python from the zip (enforced by a test, below).

### 9. `prereg.yaml` (FR10) — Phase A, committed in 5a

Strict schema validated by `ops/prereg.py::load_prereg(path) -> Prereg` (`yaml.load(text, Loader=_UniqueKeySafeLoader)`,
a `yaml.SafeLoader` subclass whose mapping constructor raises `PreregError` on a duplicate key at any level, like
`_gold_metadata.json`'s `_no_duplicate_keys`; tests `test_duplicate_key_refused_top_level` and
`test_duplicate_key_refused_nested`; nested frozen dataclasses;
unknown or missing keys refused at every level; every leaf type-checked). Content (values are the proposal; the owner
signs the numbers off at G-a before the commit):

```yaml
prereg_version: 1              # literal int 1
phase: A                       # literal "A"; Phase B is a new top-level key appended after validation (slice 6)
registered_on: "2026-10-12"
populations:
  pilot_20k: {seed: 17, clinician_seed: 17, population: 20000, role: operational_pilot}   # analysis use: none (D5)
  main_200k: {seed: 29, clinician_seed: 29, population: 200000, role: validation}
generation:
  reference_date: "20261012"
  end_date: "20261013"          # Synthea -e; must equal reference_date + 1 day (validator): the simulation must cover the whole reference day, which the Big Join treats as observed
  jvm_timezone: UTC             # -Duser.timezone; Synthea's output depends on the JVM zone (measured)
  state: Massachusetts
  years_of_history: 10
  included_files: [patients.csv, encounters.csv, conditions.csv, medications.csv, procedures.csv,
                   careplans.csv, observations.csv, payers.csv, providers.csv, organizations.csv]
  synthea_jar_sha256: "ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1"   # tools/synthea/synthea-with-dependencies.jar, hashed 2026-10-12
  synthea_build_version: "v3.4.0-18-ga07a65555"      # from the jar's MANIFEST.MF Build-Version
  jdk: {distribution: temurin, version: "21.0.12.1+1", file: OpenJDK21U-jdk_x64_linux_hotspot_21.0.12.1_1.tar.gz,
        sha256: "ce79869e1307ed8ee1e2baa86a412b1eb5b75d10a01006d788a6f968bcfaee94"}
        # Adoptium's published .sha256.txt for that file (release 2026-08-19, checked 2026-10-12); recomputed after download
label:
  definition: unplanned_readmission_v1
  planned_procedure_codes: ["33195004", "367336001", "703423002"]
  readmission_window_days: 30
  lookback_years: 1
split:
  test_start_rule: {kind: jan1_of_reference_year_minus_years, years: 3}   # resolves to 20230101 for 20261012
  test_start_date_resolved: "20230101"
  train_start_date: null
  patient_disjoint: true
  label_horizon_purge: true
training:
  seed: 42
  logistic_regression: {C: 1.0, solver: lbfgs, max_iter: 2000, class_weight: null}
  xgboost: {n_estimators: 300, max_depth: 4, learning_rate: 0.05, subsample: 0.8, colsample_bytree: 0.8,
            min_child_weight: 5, reg_lambda: 1.0, tree_method: hist, n_jobs: 1}
  calibration: {method: sigmoid, cv_folds: 5, ensemble: true}
metrics:
  primary: roc_auc
  secondary: [brier_score, brier_skill_score, observed_expected_ratio, expected_calibration_error, pr_auc, log_loss]
  definitions:
    observed_expected_ratio: sum(y_true) / sum(y_prob)     # > 1 means under-prediction
    expected_calibration_error: {n_bins: 10, binning: quantile}
    brier_skill_score_reference: train_prevalence
  reported_quantities: [n_test, n_test_positive, n_dropped_label_horizon_purge, n_dropped_label_window_purge,
                        n_dropped_censor_buffer]     # keys of models.split's summary dict (split.py:195-...)
bootstrap: {kind: patient_clustered_paired_percentile, n_resamples: 2000, seed: 20261012, ci_level: 0.95}
power:
  min_test_positives: 400               # M7; below it on main_200k: results reported as underpowered, no promotion
  population_adjustment:                # the ONE pre-registered way the pilot may change an analysis value
    projected_test_positives: pilot_test_window_positives * main_population / pilot_population
    trigger_below: 500                  # 1.25 x min_test_positives
    action: raise main_200k.population once, before its generation, to the smallest multiple of 50000 whose projection is >= 500, via a logged deviation; seeds unchanged; never lowered
    cap: 400000                         # above the cap: generate at the cap and report as possibly underpowered
    pilot_test_window_positives: n_test_positive of models.split.chronological_group_split on the pilot gold with this prereg's split settings (the same quantity M7 counts; no training)
promotion:                               # FR15
  comparators: [champion, reason_code_lookup]   # champion = frozen v1 logistic-regression bundle, scored on main_200k's test window
  roc_auc_rule: paired_ci_lower_gt_zero          # (challenger - comparator) for EACH comparator
  brier_noninferiority: {margin_fraction_of_champion_brier: 0.05}   # upper CI of (challenger - champion) <= 0.05 * champion point Brier
  reason_code_lookup_smoothing: 10.0
frozen_v1:                               # FR16
  models: [logistic_regression, xgboost_calibrated]   # the readmission-risk-v1 runs; bundle ids recorded in Phase B
  report: [roc_auc, brier_score, observed_expected_ratio]   # each with the bootstrap CI above
  acceptance: {roc_auc_ci_lower_min: 0.80, observed_expected_ci_must_overlap: [0.80, 1.25]}
monitoring:                              # FR19
  matured_cohort_weeks: 13
  min_positives_floor: 20                # below it: "insufficient data", never an alert
  alert_rule: {metric: observed_expected_ratio, ci: exact_poisson_95_on_observed, condition: ci_excludes_1_and_point_outside_band, band: [0.80, 1.25]}
  auc: report_only
outcomes:
  lookup_still_wins: >-
    If the challenger does not beat the reason-code lookup on ROC-AUC under the rule above, the champion is not
    replaced, and docs/results.md leads with "the lookup is at least as good as the models" with its intervals. This is
    a reported finding, not a failure.
deviations: []
```

Validator rules (each tested):
- Literal-valued fields (exact string, else refused): `phase` = `A`; `populations.*.role` ∈ {`operational_pilot`,
  `validation`} with exactly one of each; `split.test_start_rule.kind` = `jan1_of_reference_year_minus_years`;
  `bootstrap.kind` = `patient_clustered_paired_percentile`; `promotion.roc_auc_rule` = `paired_ci_lower_gt_zero`;
  `promotion.comparators` = `[champion, reason_code_lookup]`; `monitoring.alert_rule.metric` =
  `observed_expected_ratio`, `.ci` = `exact_poisson_95_on_observed`, `.condition` =
  `ci_excludes_1_and_point_outside_band`; `monitoring.auc` = `report_only`; `metrics.primary` = `roc_auc`;
  `metrics.definitions.observed_expected_ratio` = `sum(y_true) / sum(y_prob)`; `…expected_calibration_error.binning` =
  `quantile`; `jdk.distribution` = `temurin`; `training.calibration.method` ∈ {`sigmoid`, `isotonic`}.
  Free strings (non-empty): `registered_on` (YYYY-MM-DD), `outcomes.lookup_still_wins`, `synthea_build_version`,
  `jdk.version`, `generation.state`.
- Run names (keys of `populations`): fullmatch `[a-z0-9_]{1,30}`; exactly `pilot_20k` and `main_200k` in Phase A.
- Seeds: the four population seeds (`seed`, `clinician_seed` of both populations) — the two `seed`s are distinct from
  each other and ≠ 42 (v1's population seed); each population's `clinician_seed` is an int ≥ 0. `training.seed` and
  `bootstrap.seed` are not covered by this rule (model/resampling seeds, not population seeds).
- Equal to code constants: `label.definition` = `LABEL_DEFINITION`; `planned_procedure_codes` =
  `PLANNED_PROCEDURE_CODES`; `readmission_window_days` = `BigJoinConfig`'s default (30) and `lookback_years` = its
  default (1); `generation.included_files` = `EXPECTED_CSV_TABLES` (order included); `training.xgboost` =
  `modeling.XGB_PARAMS` minus `objective`/`eval_metric`; `training.logistic_regression` =
  `{k: build_logistic_regression(0).named_steps["model"].get_params()[k] for k in ("C", "solver", "max_iter",
  "class_weight")}`; `training.calibration.cv_folds` = `TrainingConfig`'s `calibration_cv_folds` default (5, read from
  `dataclasses.fields(TrainingConfig)`; a new `test_cli_defaults_equal_config` in tests/ops loads
  `scripts/train_models.py` by path and checks its argparse defaults equal the `TrainingConfig` defaults). (These equalities make an
  accidental drift between code and pre-registration a failing test.)
- Dates: `generation.reference_date` a valid YYYYMMDD ≠ `20260916` (v1's); `generation.end_date` = `reference_date` + 1 day; `generation.jvm_timezone` = `UTC`; `test_start_date_resolved` = the rule
  applied to `reference_date`.
- Numbers: `synthea_jar_sha256` and `jdk.sha256` fullmatch 64 hex; `0 < margin_fraction_of_champion_brier < 1`;
  `n_resamples >= 1000`; `0 < ci_level < 1`; band low < 1 < high; `min_positives_floor >= 1`; populations > 0.
- Every other leaf: type-checked, and booleans `split.patient_disjoint`, `split.label_horizon_purge`,
  `training.calibration.ensemble` must be `true` (the code cannot do otherwise); `split.train_start_date` must be null
  (= `TrainingConfig`'s default); `metrics.secondary` and `reported_quantities` non-empty, duplicate-free, each from a
  known set (`secondary`: keys of the metrics dict that `evaluate_probabilities(np.array([0, 1, 0, 1]),
  np.array([0.1, 0.9, 0.2, 0.8]), train_prevalence=0.5).metrics` returns at run time, plus `observed_expected_ratio`;
  `reported_quantities`: members of a new constant `models.split.SPLIT_SUMMARY_KEYS: tuple[str, ...]` (the summary
  dict's keys in order; `test_split_summary_keys_constant` in tests/models asserts it equals the keys of a real
  `chronological_group_split` result on the existing split fixture), which P1b reads); `frozen_v1.models` = `MODEL_NAMES`; positive ints/floats where counts or thresholds.
  Further code equalities: `training.seed` = `TrainingConfig.seed` default; `calibration.method` =
  `TrainingConfig.calibration_method` default; `promotion.reason_code_lookup_smoothing` = `reason_code_lookup`'s
  `smoothing` default; `expected_calibration_error.n_bins` = `evaluate_probabilities`' `n_bins` default (read with
  `inspect.signature`). Deliberately NOT tied to code now: `bootstrap.n_resamples` 2000 and `bootstrap.seed` (today's
  CLI default is 500 and the bootstrap draws from the training seed): slice 6's evaluation reads them from
  `prereg.yaml`, and its design must not run on code defaults.
- `power.population_adjustment`: literal strings as shown; `trigger_below` = 1.25 × `min_test_positives`; `cap` ≥ the
  `main_200k` population.
- `deviations`: a list of entries `{date: "YYYY-MM-DD", commit: 40-hex str, field: dotted path str (must exist in the
  document), old: any YAML value, new: any YAML value, reason: non-empty str}`. Semantics: a deviation edits the field in
  place (the document always states the values in force) AND appends an entry recording old and new; every rule above
  validates the values in force; `commit` is the commit introducing the deviation, filled in a follow-up commit (or
  `"pending"` until then, accepted only as the last entry). Empty when Phase A is first committed; `test_repo_prereg_is_valid`
  validates whatever the file holds.
- Code equality is enforced at RUN time too, not only by tests (CI is a signal, not a merge gate):
  `ops/prereg.py::check_prereg_against_code(prereg) -> None` (raises `PreregError` listing every mismatch) imports the
  code constants lazily inside the function (`pipeline.big_join`, `models.modeling`, `models.training`,
  `models.baselines`, `models.evaluate`, `models.constants`, `pipeline.generation`); the launcher and the submitter call
  it as preflight **P1b** (the laptop environment has pyspark and sklearn). `tests/ops/test_prereg.py` calls the same
  function, so test and runtime share one implementation.

`ops/prereg.py` also provides:
```python
@dataclass(frozen=True)
class PreregRecord:
    head_commit: str          # HEAD (40 hex): the code that is launched
    prereg_commit: str        # last commit touching prereg.yaml (`git log -1 --format=%H -- prereg.yaml`)
    prereg_committed_at: str  # its committer date, ISO 8601 UTC (M6 evidence)
    sha256: str               # of prereg.yaml's committed bytes
class PreregError(Exception): ...
def check_prereg_committed(repo_root: Path, *, run_git: Callable = subprocess.run) -> PreregRecord:
    """Refuses (PreregError) unless: prereg.yaml is tracked; `git status --porcelain` is empty (whole tree clean, so the
    zip and the launched code are exactly HEAD); after `git fetch --quiet origin` (refreshes the remote-tracking refs, so
    a stale ref cannot vouch for HEAD), HEAD is contained in at least one `origin/*` branch
    (`git branch -r --contains HEAD` lists one). `prereg_committed_at` is the committer date (settable by hand); the
    stronger M6 evidence, the time the prereg commit reached GitHub (its PR's merge time, `gh pr list --search <sha>
    --state merged --json mergedAt`), is recorded by hand at L3."""
```

### 10. `ops/spark_zip.py` (FR3)

```python
def spark_job_package_files(repo_root: Path) -> list[str]:
    """Archive names, sorted: 'readmission_risk/__init__.py' + every 'readmission_risk/pipeline/**/*.py' that git TRACKS
    (`git ls-files -z src/readmission_risk/__init__.py src/readmission_risk/pipeline`, filtered to *.py, 'src/' stripped),
    so an untracked or ignored file can never ship. Same set as
    tests/pipeline/test_runtime_dependencies.py::SPARK_JOB_SOURCES minus the script (a test compares them)."""
def build_spark_zip(repo_root: Path, out_dir: Path) -> tuple[Path, str]:
    """Writes out_dir/readmission_risk_spark_job-<sha256[:12]>.zip; returns (path, sha256 hex). Idempotent."""
```
Cross-platform deterministic: `ZIP_STORED` (no compression, so the bytes do not depend on the zlib build; the job is
< 200 KB), entries sorted, `ZipInfo` date 1980-01-01 00:00:00, `external_attr = 0o644 << 16`, `create_system = 3`, no
directory entries, no `__pycache__`, file bytes read as-is. The main script `scripts/run_big_join.py` is shipped as a
separate artifact (Dataproc's main-python-file argument). CLI `scripts/build_spark_zip.py [--out dist/]` prints path and
sha256; `make spark-zip`. The launcher and the submitter call `build_spark_zip(repo_root, repo_root / "dist")`
themselves (after P1, so the tree is clean and the zip is HEAD's tracked code).

### 11. `ops/gcloud.py`, `ops/generation_vm.py`, `ops/dataproc_batch.py` (FR13, FR14 cap)

`ops/gcloud.py` (**stdlib plus exactly `readmission_risk.gcp`**, enforced by an AST test that allows the stdlib and that
one module, because `tests/conftest.py` imports it in every environment, including CI's Dataproc-runtime job;
`src/readmission_risk/ops/__init__.py` is empty, also asserted, so importing the package pulls in nothing else):
```python
from readmission_risk.gcp import PROJECT_ID as PROJECT   # gcp.py is import-safe (no module-level google import); one home
Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]
GCLOUD_ENV = {"CLOUDSDK_CORE_PROJECT": PROJECT,
              "CLOUDSDK_STORAGE_PARALLEL_COMPOSITE_UPLOAD_ENABLED": "False"}   # composite uploads create and DELETE
              # component objects; the create-only writers lack storage.objects.delete
def default_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
    # subprocess.run(argv, check=False, capture_output=True, text=True, env={**os.environ, **GCLOUD_ENV})
def resolve_runner(run: Runner | None) -> Runner:
    # `run if run is not None else default_runner`, looked up on this module AT CALL TIME (module attribute access), so
    # tests/conftest.py's monkeypatch of readmission_risk.ops.gcloud.default_runner is effective; every ops function
    # takes `run: Runner | None = None` and calls resolve_runner(run); no `run=default_runner` default arguments and no
    # `from .gcloud import default_runner` anywhere (a static test greps ops/ and scripts/ for both).
class GcloudError(Exception): ...   # raised by run_json/run_ok on a non-zero exit, carrying argv[:4] and stderr tail
def run_ok(run: Runner, argv: list[str]) -> str: ...
def run_json(run: Runner, argv: list[str]) -> object: ...   # appends --format=json
def list_objects(run: Runner, prefix_uri: str) -> list[str]:
    # `gcloud storage ls --recursive <prefix_uri>/**`; exit 0 -> the object URLs; exit 1 with stderr containing
    # "matched no objects" -> []; any other non-zero exit -> GcloudError. The only way "empty prefix" and "marker
    # absent" are decided (P4, P5, polling, layout checks).
def object_exists(run: Runner, uri: str) -> bool: ...   # same rule with `gcloud storage ls <uri>`
def list_labelled(run: Runner, kind: str) -> list[dict]:
    # kind in {"instances", "disks"}: `gcloud compute <kind> list --filter=labels.purpose=synthea-generation
    # --format=json` (exit 0 with [] when none); used by P3, the positive control and the proof.
```
(The ops modules' stdlib-only rule covers `gcp.py` too: it imports only `os`, `re`, `typing` at module level.)
Tests use a fake runner; `tests/conftest.py::_no_real_gcp` additionally patches `readmission_risk.ops.gcloud.default_runner`
(by dotted string, like the existing factories) to raise.

Constants (`ops/generation_vm.py` / `ops/dataproc_batch.py`; `PROJECT` from `ops/gcloud.py`): `REGION =
"us-central1"`, `DEFAULT_ZONE = "us-central1-a"`, `RAW_BUCKET = "synthea-readmission-risk-raw"`, `GOLD_BUCKET =
"synthea-readmission-risk-gold"`, `ARTIFACTS_BUCKET = "synthea-readmission-risk-artifacts"`, `STAGING_BUCKET =
"synthea-readmission-risk-dataproc-staging"`, `TEMPLATE_PREFIX = "synthea-generator-"`,
`TEMPLATE_CLASS_FOR_RUN = {"pilot_20k": "pilot", "main_200k": "main"}`, `GENERATION_LABEL =
("purpose", "synthea-generation")`, `GENERATOR_SA = "synthea-generator@…"`, `RUNTIME_SA = "dataproc-runtime@…"`,
`SUBNET = "projects/synthea-readmission-risk/regions/us-central1/subnetworks/spark-us-central1"`, `MAX_EXECUTORS = 7`,
`RUNTIME_VERSION = "3.0"`, `MACHINE_VCPUS = 32`, `WAIT_MARGIN_SECONDS = 300`. `test_constants_match_terraform` checks
exactly these literal strings occur in `infra/*.tf`: `RAW_BUCKET`, `GOLD_BUCKET`, `ARTIFACTS_BUCKET`, `STAGING_BUCKET`,
`TEMPLATE_PREFIX`, `"spark-us-central1"` (SUBNET's last segment), `"synthea-generator"` and `"dataproc-runtime"` (the SA
account ids), `"synthea-generation"` (the label value), `"n2-standard-32"`, and `REGION`. `MAX_EXECUTORS`,
`RUNTIME_VERSION`, `DEFAULT_ZONE` and `PROJECT` are not in Terraform and are checked only by the argv tests. There is no
hard-coded run duration: the launcher reads it from the template (step 3a). Template names are
`synthea-generator-<class>-<seconds>s` (§12), so the launcher resolves the template for a run with
`gcloud compute instance-templates list --filter="name ~ ^synthea-generator-<class>-[0-9]+s$" --format=json` and
requires exactly one match (zero → refused "no template for class <class>", e.g. `main` before slice 6 adds it; several
→ refused).

**Artifacts** (content-addressed; uploaded by the CLIs as the owner): `gs://synthea-readmission-risk-artifacts/sha256/
<hex>/<filename>` for the Synthea jar, the JDK tarball, the Spark zip, `scripts/generate_synthea_data.py` and
`scripts/run_big_join.py`. Upload, under `GCLOUD_ENV` (no composite uploads), is preceded by an existence check, so no
stderr parsing of a precondition failure is needed: `object_exists(run, uri)` (the `list_objects` rule); if it exists,
`gcloud storage objects describe <uri> --format=json` and compare its `md5_hash` field (base64 MD5; present for every
non-composite object) with the local file's `base64(md5(bytes))`: equal → reuse, else refuse ("artifact name
collision"); if it does not exist, `gcloud storage cp <file> <uri> --if-generation-match=0`, and ANY non-zero exit is a
refusal (a concurrent upload is not expected; the operator reruns). The fake runner uses the describe shape
`{"name": …, "size": …, "md5_hash": …}`; L3 records one real `describe` output in Results and, if its key differs, the
code and fixture are corrected before L4.

**Generation launcher** `scripts/launch_generation.py --run pilot_20k [--zone us-central1-a] --jdk-tarball PATH
[--jar-path tools/synthea/synthea-with-dependencies.jar] [--java-max-heap 96g] [--allow-main-run] [--dry-run]
[--poll-seconds 30]` (`--dry-run` runs P1, P1b, P2 and P5, builds the zip and hashes every artifact, then prints every gcloud
argv it would run, with the template's run duration shown as `<from template>`; no gcloud call at all):
1. Preflight, in order; first failure → exit 1 `REFUSED: <reason>`:
   P1 `check_prereg_committed`; P1b `check_prereg_against_code` (immediately after P1; covered by the per-refusal tests
   like every P); P2 run name in `prereg.populations`, `main_200k` requires `--allow-main-run`, and `--zone` starts with `REGION + "-"`
   (the subnet is regional);
   P3 no instance AND no disk with label `purpose=synthea-generation` in any zone (`list_labelled`; a leftover disk is
   refused here with its delete command, not discovered later by the positive control) and no Dataproc batch in state
   `PENDING`/`RUNNING` in us-central1;
   P4 `gs://…-raw/v2/<run>/` holds no object; P5 the local jar's and JDK's sha256 equal the prereg's;
   P6 quota: `gcloud compute regions describe us-central1` shows `N2_CPUS` and `CPUS` with `limit - usage >= 32`, and
   `gcloud compute project-info describe` shows `CPUS_ALL_REGIONS` `limit - usage >= 32` (measured 2026-10-12: N2_CPUS
   200, CPUS 200, CPUS_ALL_REGIONS 32, all unused; `PREEMPTIBLE_CPUS` is 0, which on GCE means spot VMs draw on the
   regular quota; `SSD_TOTAL_GB` 500 covers the 200 GB pd-balanced disk).
2. Build the zip; upload artifacts.
3a. Resolve the run's template (above), then read it once: `gcloud compute instance-templates describe <template> --format=json` →
   `max_run_seconds = int(properties.scheduling.maxRunDuration.seconds)` (the single source of truth is Terraform's
   `var.generation_templates`); wait timeout = `max_run_seconds + WAIT_MARGIN_SECONDS`.
3. Create: `gcloud compute instances create <name> --zone <zone> --source-instance-template=projects/synthea-readmission-
   risk/global/instanceTemplates/<template> --labels=purpose=synthea-generation,run=<run_dashed>,prereg=<prereg_commit[:12]>
   --metadata-from-file=startup-script=infra/generation/startup.sh --metadata=enable-oslogin=TRUE,block-project-ssh-keys=true,
   <run keys>`. `<name>` = `synthea-gen-<run_dashed>-<UTC yyyymmddhhmm>`; `<run_dashed>` = run name with `_` → `-` (run
   names are validated in `prereg.yaml` as `[a-z0-9_]{1,30}`, so every label value matches GCE's `[a-z0-9_-]{0,63}` and
   the instance name stays ≤ 63 chars; `test_labels_sanitised` asserts both regexes for every label value and the name).
   The purpose label and every metadata key are passed explicitly, so correctness does not depend on whether gcloud
   merges or replaces template labels/metadata; the startup script lives only here (not in the template), so editing it
   never forces a template replacement. Run metadata keys: `run`, `seed`, `clinician_seed`, `end_date`, `jvm_timezone`, `population`,
   `reference_date`, `state`, `years_of_history`, `included_files` (`;`-joined; `,` is gcloud's separator),
   `java_max_heap`, `raw_prefix`, `head_commit`, `prereg_commit`, `prereg_sha256`, `jar_uri`, `jar_sha256`, `jdk_uri`, `jdk_sha256`,
   `zip_uri`, `zip_sha256`, `gen_script_uri`, `gen_script_sha256`. Every generation parameter comes from `prereg.yaml`;
   only `java_max_heap` is operational.
4. Positive control (immediately): `gcloud compute instances describe <name> --zone <zone> --format=json` must show label
   `purpose=synthea-generation`, metadata `startup-script` whose sha256 equals the local file's,
   `scheduling.provisioningModel == "SPOT"`, `scheduling.instanceTerminationAction == "DELETE"`,
   `scheduling.maxRunDuration.seconds == str(max_run_seconds)` (the template's value, step 3a), no `accessConfigs`, and
   service account `GENERATOR_SA`; `gcloud compute disks describe <name> --zone <zone> --format=json` (the boot disk takes
   the instance's name) must show label `purpose=synthea-generation`; and the instance AND disk list filters used by P3
   and the teardown proof must each list exactly this instance/disk (so the later "empty" result is not vacuous). Any
   mismatch → delete immediately, exit 2.
5. Wait: poll `gs://…-raw/v2/<run>/_DONE` / `_FAILED` and the instance's existence every `--poll-seconds`, until a marker
   appears, the instance disappears, or the wait timeout (step 3a) passes.
6. Teardown, always (`try/finally`, including after `KeyboardInterrupt`): `gcloud compute instances delete <name> --zone
   <zone> --quiet` if it exists; then the proof: the instance and disk lists filtered on `labels.purpose=synthea-generation`
   are both empty.
7. Print the run summary from `generation_summary.json` + `run_provenance.json`, saved to `data/pilot/<run>/`
   (gitignored).
Exit codes: 0 done; 1 refused (preflight); 2 generation failed (`_FAILED`; positive control failed; the instance
vanished without a marker: "preempted or max-run-duration reached"; or the wait timeout passed with the instance still
present and no marker: "timed out", the launcher then deletes it); 3 teardown proof failed (a labelled instance or
disk remains: printed with its delete command); 130 interrupted (after teardown and proof ran). Precedence when several
apply: 3 (proof failed) > 2 (generation/positive control/layout/timeout failed) > 130 (interrupted) > 0; every message
that applies is printed.

**VM startup** `infra/generation/startup.sh` (bash). Its first lines, in this order: `mkdir -p /work`;
`exec > >(tee -a /work/startup.log) 2>&1` (both to the file, whose tail becomes `_FAILED`, and to the guest agent, which
forwards the startup script's output to Cloud Logging, the reason for the `logging.logWriter` grant; changed in the
pre-commit review from a plain file redirect, which kept the log only on the disk that teardown deletes. Trade-off: the
file is written by an asynchronous `tee`, so `on_error` sleeps 2 s before reading the tail; a heuristic, not a
guarantee); `set -Eeuo pipefail` (`-E` so
the ERR trap is inherited by functions and subshells); `trap on_error ERR`, where `on_error` logs the failing line and command (from `fail`: the caller's reason and line), then reads
`raw_prefix` itself from the metadata server (so it works before the main metadata reads; if even that read fails, it logs and
idles (`sleep infinity`) without uploading; `max_run_duration` (counted from VM start, so always before the launcher's
own wait runs out) deletes the VM, and the launcher reports exit 2 "the VM vanished without a marker"); its messages go to
stderr with the failing line and command (with `set -E` the trap also fires inside `$(...)`, whose stdout would swallow
them), and `meta` names an unreadable key;
then (only once: a `FAILED_SENT` flag guards it, and it runs `trap - ERR` first) uploads `_FAILED` with
`--if-generation-match=0` =
`tail -n 200 /work/startup.log`, then a tool check `for t in gcloud curl sha256sum tar python3; do command -v "$t" ||
fail "missing tool $t"; done` (after uploading, `on_error` ends with `sleep infinity`, so nothing runs after it and no
second upload can be attempted (the create-only SA could not overwrite one anyway); `fail` writes the message and calls
`on_error`; if `gcloud` itself is missing, `on_error`
falls back to a JSON-API upload of `_FAILED` with `curl` and a token from the metadata server), and only then the
metadata reads. (The tools are expected on GCE's Debian images, which ship `google-cloud-cli`; unverified for
`debian-13`, so the check makes a missing one fail in seconds with a named cause; L1 additionally records `gcloud
compute images describe-from-family debian-13 --project debian-cloud` in Results.) Synthea's
own stdout is captured and discarded by `run_synthea_generation`, which keeps a 20-line stderr tail in its error, so the
log holds the script's steps, the CLI's output and that tail): reads every run key from the metadata server
(`curl -sf -H Metadata-Flavor:Google …/instance/attributes/<key>`); downloads the four artifacts with `gcloud storage cp`
and verifies each with `sha256sum -c` (abort on mismatch); unpacks the JDK with `mkdir -p /opt/jdk && tar -xzf <jdk> -C /opt/jdk --strip-components=1` (Adoptium tarballs have one
top-level `jdk-21.0.12.1+1/` directory) and checks `/opt/jdk/bin/java -version 2>&1` contains `"21.0.12.1"` (the
`jdk.version` before `+`), else `fail`; runs
`env PATH=/opt/jdk/bin:$PATH PYTHONPATH=<zip> python3 <gen_script>
--population <population> --seed <seed> --clinician-seed <clinician_seed> --reference-date <reference_date>
--end-date <end_date> --jvm-timezone <jvm_timezone> --require-alive-population --output
/work/out --jar-path <jar> --state <state> --years-of-history <years_of_history> --included-files <included_files with ;
→ ,> --java-max-heap <java_max_heap> --provenance run=<run> --provenance prereg_commit=… --provenance prereg_sha256=… --provenance head_commit=…
--provenance zip_sha256=… --provenance synthea_jar_sha256=<jar_sha256>` (all from metadata), timing it; writes `run_provenance.json` with exactly these keys:
`run`, `metadata` (object: every run metadata key → value), `machine_type`, `zone`, `image` (from the metadata server's
`instance/image` key, which needs no IAM; `null` if the key is absent, never fatal), `vm_started_utc`, `generation_started_utc`,
`generation_finished_utc`, `generation_wall_seconds`, `upload_finished_utc`, `raw_bytes` (`du -sb /work/out/csv`). Then,
under `GCLOUD_ENV`'s composite-upload setting (`export CLOUDSDK_STORAGE_PARALLEL_COMPOSITE_UPLOAD_ENABLED=False`):
one `gcloud storage cp /work/out/csv/<table> "${raw_prefix}/csv/<table>"` per table named in `included_files` (each name already ends in `.csv`, giving objects
`${raw_prefix}/csv/patients.csv` etc., the layout `load_synthea_tables` reads; any other file Synthea might write is never
uploaded), then two create-only copies, `gcloud storage cp /work/out/generation_summary.json
"${raw_prefix}/generation_summary.json" --if-generation-match=0` and the same for `/work/run_provenance.json`, then `_DONE`
LAST (an empty object, also `--if-generation-match=0`). The launcher's step 7 verifies the layout: the prefix holds
exactly `csv/<each of the 10 tables>`, `generation_summary.json`, `run_provenance.json`, `_DONE` (anything else or
missing → exit 2). Step 7 prints: run, seeds, population, `generation_wall_seconds`, `peak_child_rss_bytes` (GiB),
`raw_bytes` (GiB), row counts of `patients.csv` and `encounters.csv`, VM total seconds (create → delete). Any
failure (an `ERR` trap) uploads `_FAILED` containing the last 200 log lines. It then idles (`sleep infinity`); the
launcher deletes the VM; if the launcher is gone, `max_run_duration` deletes it. (The guest never shuts itself down: a
guest-initiated shutdown stops rather than deletes the VM, which would leave a billed disk.) Image family
`debian-cloud/debian-13` (Python 3.13, gcloud preinstalled): **not pinned** (a family resolves to the newest image);
the resolved image is recorded in `run_provenance.json`. The JDK is pinned by sha256; the zip's generation code is
stdlib-only.

**Big Join batch submitter** `scripts/submit_big_join_batch.py --run pilot_20k [--allow-main-run] [--dry-run]
[--poll-seconds 30]`:
1. Preflight: P1 `check_prereg_committed`; P1b `check_prereg_against_code`; P2 run in prereg (+ main guard); P3 no labelled generation instance and no
   other active batch; P4 `gs://…-raw/v2/<run>/` has the exact layout above (`_DONE` present); P5 `gs://…-gold/v2/<run>/`
   holds no object; P6 quota: `CPUS_ALL_REGIONS` and regional `CPUS` each `limit - usage >= 32`, and regional
   `DISKS_TOTAL_GB` `limit - usage >= 3200` (the PRD's FR14 figure: driver + 7 executors; the per-node disk default of
   the standard tier is read from Google's Serverless properties page at implementation and the 3200 adjusted if it
   differs); P7 the raw run matches the prereg: its
   `generation_summary.json` (`gcloud storage cat`) has `seed`, `clinician_seed`, `population_size`, `reference_date`,
   `state`, `years_of_history`, `included_files`, `end_date`, `jvm_timezone` equal to the prereg's values for this run,
   `provenance.synthea_jar_sha256` equal to `generation.synthea_jar_sha256` (the VM verified the jar with `sha256sum -c`
   before running it and passes the verified hash as `--provenance synthea_jar_sha256=…`; generation.py itself hashes
   nothing, so tests never hash a real jar), `n_alive_patients >= population`, and
   `provenance.prereg_commit` an ancestor of HEAD (`git merge-base --is-ancestor`), so a raw run made before a later
   deviation commit is still accepted as long as its own parameters equal the values in force for this run. `--dry-run`: P1, P1b, P2, the zip build and hash, then prints the submit argv; no
   gcloud call.
2. Build the zip; upload it and `run_big_join.py`.
3. `gcloud dataproc batches submit pyspark <main uri> --async --batch=bigjoin-<run_dashed>-<UTC yyyymmddhhmm>
   --region=us-central1 --version=3.0 --py-files=<zip uri> --service-account=<RUNTIME_SA> --subnet=<SUBNET>
   --staging-bucket=<STAGING_BUCKET> --ttl=2h --labels=run=<run_dashed>,prereg=<commit[:12]>
   --properties=dataproc.tier=standard,spark.dynamicAllocation.maxExecutors=7,spark.executor.cores=4,spark.driver.cores=4,spark.sql.shuffle.partitions=128
   -- --input-dir gs://…-raw/v2/<run> --output-dir gs://…-gold/v2/<run> --reference-date <prereg reference_date>
   --lookback-years <prereg label.lookback_years> --readmission-window-days <prereg label.readmission_window_days>
   --spark-master external --provenance head_commit=<HEAD> --provenance zip_sha256=<…> --provenance main_sha256=<…>
   --provenance prereg_commit=<…> --provenance batch_id=<id>`; the batch record (step 5) also stores these. `dataproc.tier=standard` makes FR14's tier explicit (the property name is checked against
   Google's "Serverless pricing tiers" page at implementation; if it differs, the doc's value is corrected, not dropped).
   `spark.sql.shuffle.partitions=128` = 4 × the 32-vCPU cap, set explicitly per FR14 and recorded; AQE (on by default)
   may coalesce it.
4. Poll `gcloud dataproc batches describe <id> --region us-central1 --format=json` until state is `SUCCEEDED`, `FAILED`
   or `CANCELLED`.
5. Record (`data/pilot/<run>/batch_<id>.json`, gitignored, and printed): state, `runtimeConfig.version`, `createTime`,
   `stateTime`, wall-clock (`stateTime - createTime`), DCU-hours (`runtimeInfo.approximateUsage.milliDcuSeconds / 3.6e6`),
   shuffle GB-hours (`runtimeInfo.approximateUsage.shuffleStorageGbSeconds / 3600`). A pure
   `parse_batch_usage(describe: dict) -> BatchUsage(state, runtime_version, wall_seconds, dcu_hours: float | None,
   shuffle_gb_hours: float | None)` accepts the int64 fields as JSON strings or ints; if `approximateUsage` is absent
   (it is computed after completion), both are `None`, and the CLI re-describes every 30 s for up to 10 min after the
   terminal state before recording `None` with a printed "usage not yet populated; re-run `gcloud dataproc batches
   describe <id>` later" note.
Exit codes: 0 succeeded; 1 refused; 2 batch failed/cancelled (`stateMessage` printed, plus the manual gold-cleanup command
below); 130 interrupted (the batch keeps running and is bounded by its 2 h `ttl`; the message prints the cancel command).

### 12. Terraform (5a defines; 5b applies)

- `apis.tf`: add `compute.googleapis.com`, `dataproc.googleapis.com` (both already enabled outside Terraform, verified
  2026-10-12 with `gcloud services list`; Terraform adopts them, `disable_on_destroy = false`) and
  `policytroubleshooter.googleapis.com` (not enabled, verified 2026-10-12; L2 needs it; free).
- `network.tf`: `google_compute_network.spark` (name `spark`, `auto_create_subnetworks = false`),
  `google_compute_subnetwork.spark_us_central1` (name `spark-us-central1`, `10.10.0.0/20`, `private_ip_google_access =
  true`; the default network's us-central1 subnet has Private Google Access **off**, verified 2026-10-12), and
  `google_compute_firewall.spark_internal` (ingress, all protocols, source = the subnet range; Dataproc requires
  "internal communication on all ports between VM instances within the subnet"). No other ingress; no external IPs
  anywhere (the VM reaches GCS through Private Google Access; nothing needs the internet, which is why the JDK comes
  from the artifacts bucket instead of `apt`).
- `storage.tf`: `artifacts` bucket (us-central1, uniform access, PAP enforced, `force_destroy = true`, label
  `purpose = artifacts`) and `dataproc_staging` bucket (same, 30-day delete lifecycle, `soft_delete_policy` 0, label
  `purpose = dataproc-staging`). The raw bucket's existing 90-day rule covers `v2/`.
- `iam.tf` (additive `*_iam_member` only):
  - SA `synthea-generator`: on raw `roles/storage.objectCreator` + `roles/storage.objectViewer` (create-only plus read:
    `gcloud storage cp` reads object metadata, slice 3's finding) + `roles/storage.legacyBucketReader`
    (`storage.buckets.get`); on artifacts `objectViewer` + `legacyBucketReader`; project `roles/logging.logWriter`.
  - SA `dataproc-runtime`: project `roles/dataproc.worker` (the documented minimum); on raw and artifacts
    `objectViewer` + `legacyBucketReader`; on `dataproc_staging` `roles/storage.objectAdmin` + `legacyBucketReader`;
    on gold `objectViewer` + `legacyBucketReader` (unconditional: reads and listing) plus `roles/storage.objectAdmin`
    **with an IAM condition** `resource.name.startsWith("projects/_/buckets/synthea-readmission-risk-gold/objects/v2/")`
    (title `gold-v2-prefix-only`; Spark's output committer creates and deletes temporary objects; the condition keeps it
    away from `full_run_2b/`, which slice 4's scorer reads). `legacyBucketReader` everywhere covers the bucket-level
    `storage.buckets.get` the Hadoop GCS connector may call, which object-level roles and the object-name condition
    cannot grant.
  - The operator (the project owner) already has `iam.serviceAccounts.actAs`; no operator binding is added.
- `compute.tf`: `google_compute_instance_template.synthea_generator` with `for_each = var.generation_templates` (a map
  class → max run seconds) and `name = "synthea-generator-${each.key}-${each.value}s"` and NO `create_before_destroy` (removed in the pre-commit
  review: every template attribute forces replacement, and a same-name replacement created first fails with
  alreadyExists; when the duration changes the name changes too, and Terraform destroys the old template and creates the
  new one; instances created from a template are standalone and do not depend on it), so slice 6 adds the `main` class
  with the duration measured from the pilot without touching `pilot`.
  Body: `machine_type = "n2-standard-32"` (32 vCPU, 128 GB); `scheduling { provisioning_model =
  "SPOT", preemptible = true, automatic_restart = false, on_host_maintenance = "TERMINATE", instance_termination_action =
  "DELETE", max_run_duration { seconds = each.value } }` (`pilot` = 7200; all present and GA in provider
  8.5.0's schema, checked 2026-10-12 with `terraform providers schema`); `disk { source_image = "debian-cloud/debian-13",
  disk_type = "pd-balanced", disk_size_gb = 200, auto_delete = true, boot = true, labels = { purpose =
  "synthea-generation" } }`; `network_interface { subnetwork = <spark-us-central1> }` (no `access_config`);
  `service_account { email = synthea-generator, scopes = ["cloud-platform"] }`; `shielded_instance_config` all on;
  `metadata = { enable-oslogin = "TRUE", block-project-ssh-keys = "true" }` (no startup script: the launcher passes it
  per instance, so a script edit never replaces the template, and an instance created from the template by hand does
  nothing); `labels = { purpose = "synthea-generation" }`. `test_template_has_cost_bounds` (tests/test_infra.py, using that file's existing `_collapse` whitespace helper so
  `terraform fmt` alignment cannot break it) checks `infra/compute.tf` for `provisioning_model = "SPOT"`,
  `instance_termination_action = "DELETE"`, `max_run_duration`, `auto_delete = true` and the absence of `access_config`.
  Both new buckets use `location = upper(var.region)` literally (required by
  `test_buckets_enforce_uniform_access_and_public_access_prevention`).
- `variables.tf`: `generation_templates` (map(number), default `{ pilot = 7200 }`, validation: keys in {`pilot`,
  `main`}, each value 600-86400). The L1 plan count is unchanged (one template instance).
- `outputs.tf`: `generation_templates` = a map class → template self link (`{ for k, t in
  google_compute_instance_template.synthea_generator : k => t.self_link }`), both SA emails, the subnet self link.
- Every new resource carries `depends_on = [google_project_service.enabled]` (required by
  `tests/test_infra.py::test_every_resource_depends_on_enabled_apis`).
- Not touched: the default compute SA, WIF, existing buckets' settings, budgets.

### 13. Makefile targets

`spark-zip` (`python scripts/build_spark_zip.py`), `generate-vm RUN=<run> JDK_TARBALL=<path> [GEN_ARGS=…]`
(`python scripts/launch_generation.py --run $(RUN) --jdk-tarball $(JDK_TARBALL) $(GEN_ARGS)`; refuses without `RUN` and
`JDK_TARBALL`), `bigjoin-batch RUN=<run> [BATCH_ARGS=…]`. Both live targets print a one-line cost warning first.

## UX flow

5a (developer): `make spark-zip`; `pytest`; `make tf-validate`. No live effect.

5b (owner, laptop), in order: `terraform -chdir=infra plan/apply` → `make generate-vm RUN=pilot_20k JDK_TARBALL=…`
(generation on the VM; artifacts uploaded; VM proven gone) → `make bigjoin-batch RUN=pilot_20k` (gold under
`gs://…-gold/v2/pilot_20k/` with metadata v2) → verification L6-L8 → billing check L9. Pipeline: Synthea (VM, seeds 17/17)
→ raw CSV in GCS → Big Join on Dataproc (same code as local) → gold Parquet + `_gold_metadata.json` (with
`spark-rowhash-v1`) → the loader on the laptop recomputes and matches it. No training, no scoring, no BigQuery here.

## Data handling

- **Split integrity / leakage:** no change to the label, the split (chronological, patient-disjoint, censoring buffer,
  label-window and label-horizon purges) or any feature. The fingerprint is computed over the gold table only and is
  never a feature. `prereg.yaml` fixes the test cutoff as a formula of `reference_date`, the hyperparameters, the metric
  definitions (including O/E and ECE binning) and every decision rule before the data exists.
- **Holdout hygiene:** the 200k population is not generated in this slice. Pilot gold may be inspected for row counts,
  positive counts and the test-positive count (to project M7's power at 200k), never trained on or used to choose any
  analysis value; `prereg.yaml` is committed before the pilot runs.
- **Reproducibility:** Synthea output content is deterministic given `-s` AND `-cs` (measured; row order is not, and the
  fingerprint ignores order); the jar and JDK are pinned by sha256 in `prereg.yaml`; the Spark zip is byte-deterministic
  across platforms and content-addressed; every run records the prereg commit + sha256, the git commit, the artifact
  hashes, the resolved VM image, `spark_version`, and the Dataproc `runtimeConfig.version` (local batch record).
- **IAM:** two new least-privilege SAs, additive bindings only. The default compute SA (project `roles/editor`, flagged in
  slice 3) is used by neither workload: the template and the batch both name their own SA, and a test asserts neither the
  argv nor `infra/compute.tf` mentions `-compute@developer.gserviceaccount.com`. Removing its `roles/editor` is a
  non-additive change to a Google-managed grant and stays a named follow-up for the owner (recommended before v2.0).
- **Secrets:** none. No keys; the CLIs run as the owner's ADC; nothing committed carries the billing account, budget ids
  or the owner's email.

## Failure modes

| Case | Behaviour |
|---|---|
| `prereg.yaml` missing, invalid, uncommitted, modified, tree dirty, HEAD not on a remote | launcher/submitter refuse (exit 1), naming the check; nothing created |
| `main_200k` requested without `--allow-main-run` | refused |
| A generation VM or Dataproc batch already active; quota short | refused (P3/P6; the 32-vCPU quota is shared) |
| Raw or gold prefix not empty | refused (no mixing, no overwrite) |
| Raw run's summary does not match the prereg (seed, clinician seed, population, dates, time zone, tables, jar hash, alive count; or its `prereg_commit` is not an ancestor of HEAD) | submitter refuses (P7) |
| Raw layout wrong after upload (missing table, extra objects) | launcher exit 2 (step 7); submitter P4 refuses |
| Artifact hash mismatch (local vs prereg; an existing object's `md5_hash` differs; on the VM `sha256sum -c`) | refused locally / `_FAILED` on the VM |
| Template labels/metadata not applied as expected | positive control fails → VM deleted, exit 2 |
| Spot preemption or `max_run_duration` reached | VM deleted by GCE; launcher sees it vanish without `_DONE` → exit 2; raw prefix partial: the next attempt is refused by P4 until the owner runs the documented `gcloud storage rm -r gs://synthea-readmission-risk-raw/v2/<run>/` (deliberately manual) |
| Launcher killed mid-run (laptop asleep, terminal closed) | VM still deleted at `max_run_duration`; rerun's P3 refuses while it exists; the proof commands are documented to run by hand |
| Ctrl-C | teardown + proof run, exit 130 (3 if the proof fails) |
| Teardown proof finds a leftover | exit 3, message with the delete command |
| Synthea or output validation fails on the VM; JVM out of memory | `_FAILED` with log tail; exit 2 |
| A table empty / missing in raw | Big Join refuses as today (same messages, gs:// paths) → batch FAILED → exit 2 |
| Batch fails after Spark created output under `gold/v2/<run>/` (partial Parquet, `_temporary/`, placeholder objects) | exit 2; P5 and `build_big_join`'s output-exists check refuse a rerun until the owner runs the printed `gcloud storage rm -r gs://synthea-readmission-risk-gold/v2/<run>/` (owner only: the runtime SA could, but nothing automates deletion) |
| `Path("gs://…")` in `BigJoinConfig` | `ValueError` at construction |
| Unsupported scheme (`s3://`, `file://` via `storage_for`) | `ValueError` |
| Session time zone not UTC when fingerprinting | `RuntimeError` |
| Fingerprint input with unexpected columns / types (bool in int column, fractional int, sub-µs timestamp) | `ValueError` naming the column |
| Gold metadata v2 whose fingerprint does not match the Parquet | loader `ValueError` (training refuses; scorer refuses) |
| Gold metadata v1 (the v1 gold) | loads as today; no content check (unchanged, stated) |
| Empty gold (zero rows) | fingerprint of zero rows is defined (`combine(0,0,0)`); the loader still refuses zero rows as today |
| Dataproc runtime 3.0 unavailable, subnet or IAM wrong | batch fails at creation or first read → exit 2 with `stateMessage`; L2 catches IAM earlier; PRD degraded mode documented, not built |

## Verification criteria

### Gates

- **G-a (before `prereg.yaml` is committed):** owner sign-off via `AskUserQuestion` on every number: clinician seeds
  (17/29), `end_date` = `reference_date` + 1 (20261013), `jvm_timezone` UTC, margin 0.05, B 2000, bootstrap seed 20261012, power floor 400, frozen-v1 acceptance 0.80 / [0.80, 1.25],
  monitoring floor 20, band [0.80, 1.25], 13 weeks, shuffle partitions 128, the population-adjustment rule (trigger 500,
  steps of 50,000, cap 400,000), and the pilot acceptance bands (A3 [0.5%, 5%], A4 ≤ 10%). The JDK
  (`21.0.12.1+1`) and jar sha256 values are already measured (§9) and are re-verified from the files at implementation.
- **G-b (before 5a's commit):** `ruff check .`, `pytest`, `make tf-validate`, `zizmor --offline .` all green.

### Existing tests changed on purpose (and nothing else)

- `tests/models/helpers.py::write_test_gold_metadata` delegates to `tests/gold_helpers.py::write_gold_metadata_for_frame`
  and gains `version: int = 1`; with 1 it passes `metadata_version=1` explicitly (every existing caller keeps writing the
  exact v1 file it writes today); with 2 it computes `rowhash_fingerprint(df)` and sets the five v2 fields
  (`gold_fingerprint_definition` = rowhash, `spark_version="test"`, `generation_summary={"reference_date":
  <reference_date>}`, `build_provenance={}`).
- `tests/pipeline/test_gold_metadata.py`: the builder (L20-34) passes `metadata_version=1`; `raw_json()` (L37-38) uses
  `json.loads(metadata_to_json(m))` instead of `asdict` (so a v1 raw dict keeps today's 12 keys); the round-trip test's
  `len(asdict(metadata)) == 12` (L65) becomes `len(metadata_to_dict(metadata)) == 12`; the `BAD_VALUES` case
  `("metadata_version", 2)` (L148) becomes `("metadata_version", 3)` (2 is now valid; with §2's parse order the error
  still names `metadata_version`). New v2 tests are added.
- `tests/pipeline/test_big_join.py`: no existing assertion changes (none checks the version or key set today); the new
  assertions are the additions listed below.
- `tests/test_infra.py`: `test_iam_is_additive_only`'s count 12 → 28 (16 new `*_iam_member`: generator 6 = raw
  objectCreator, raw objectViewer, raw legacyBucketReader, artifacts objectViewer, artifacts legacyBucketReader, project
  logWriter; runtime 10 = project dataproc.worker, raw objectViewer, raw legacyBucketReader, artifacts objectViewer,
  artifacts legacyBucketReader, staging objectAdmin, staging legacyBucketReader, gold objectViewer, gold
  legacyBucketReader, gold objectAdmin with condition); `test_buckets_enforce_uniform_access_and_public_access_prevention`'s
  name list gains `artifacts`, `dataproc_staging`. New there: `test_gold_object_admin_is_conditioned_on_v2_prefix`,
  `test_generator_cannot_delete_raw` (no role with `objects.delete` on raw for the generator),
  `test_no_resource_uses_default_compute_sa`.
- `tests/pipeline/test_runtime_dependencies.py`: `RUNTIME_BUNDLED_IMPORTS_ALLOWED` gains `"py4j": "py4j"` (§3).
- `tests/test_tooling.py`: `EXPECTED_TARGETS` gains `spark-zip`, `generate-vm`, `bigjoin-batch`; `MAKEFILE_VARIABLES`
  (L31-33) gains `RUN`, `JDK_TARBALL`, `GEN_ARGS`, `BATCH_ARGS` (so an exported `RUN` in a developer's shell cannot change
  the tests); new
  `test_live_targets_refuse_without_required_variables`.
- `src/readmission_risk/models/training.py:293`'s artifact switches to `metadata_to_dict` (an MLflow artifact, not a
  test; listed because tests that read it must see today's v1 content, and do).
- Everything else (training, scoring, guard, export, warehouse tests) runs unchanged, because the session
  `scoring_env` trains on v1 test gold and therefore exports `pandas-csv-sha256-v1` bundles exactly as today.

### 5a tests (all offline, no GCP)

`tests/pipeline/test_fingerprint.py`
- `test_token_per_kind`, `test_null_tokens` (None, `pd.NA`, NaT, `float("nan")`, `np.float64("nan")` → `N`),
  `test_int_column_accepts_numpy_and_integer_valued_float`, `test_int_column_rejects_fraction`,
  `test_bool_and_numpy_bool_rejected` (int and double columns), `test_timestamp_naive_equals_aware_utc`,
  `test_timestamp_non_utc_aware_converted`, `test_sub_microsecond_rejected`, `test_column_order_irrelevant`,
  `test_row_order_irrelevant`, `test_duplicate_row_changes_fingerprint`, `test_single_cell_change_changes_fingerprint`,
  `test_wrong_column_set_rejected`, `test_input_not_mutated`, `test_known_answer_vector` (a fixed 3-row frame → a pinned
  hex string, protecting the definition from silent drift), `test_zero_rows_defined`.
- `test_gold_column_kinds_names_match_both_column_lists` (set equality with `models.data.EXPECTED_GOLD_COLUMNS` and
  `big_join.GOLD_TABLE_COLUMNS`), `test_gold_column_kinds_match_spark_gold_schema` (types of the Big Join fixture's
  output schema: Integer/Long → int, Double → double, String → string, Timestamp → timestamp; any other type fails).

`tests/pipeline/test_big_join.py` (additions)
- `test_spark_and_python_fingerprints_agree` — **the FR12 agreement test**, on a dedicated gold-shaped fixture (not
  `FIXTURE_ROWS`, which stays untouched): a 6-row Spark DataFrame with the gold schema (built from
  `GOLD_TABLE_COLUMNS` and the Big Join's output schema), containing a null `organization_utilization`, a null
  `payer_ownership` and `marital_status`, non-integer doubles in `age_at_index_years` (e.g. 41.23477070499658), a
  non-ASCII string, and timestamps with non-zero microseconds; written to Parquet in `tmp_path`, fingerprinted with
  `spark_rowhash_fingerprint` on the read-back frame, and compared with (a) `rowhash_fingerprint(pd.read_parquet(dir))`
  and (b) `rowhash_fingerprint(load_gold_table(dir))` after `tests.gold_helpers.write_gold_metadata_for_frame(dir, df,
  version=2)`. The test asserts the fixture really has those properties. **It must run in CI's
  `pipeline-dataproc-runtime` job** (Python 3.12, PySpark 4.0.1, Java 21, no mlflow/sklearn): it imports only pyspark,
  pandas, `readmission_risk.pipeline.*`, `readmission_risk.models.data` (pandas + `pipeline.gold_metadata` +
  `pipeline.fingerprint` only; `models/__init__.py` is empty, checked) and the new mlflow-free `tests/gold_helpers.py`
  (stdlib + pandas + `readmission_risk.pipeline`), with signature `write_gold_metadata_for_frame(directory: Path, df:
  pd.DataFrame, *, version: int = 1, **overrides) -> Path` and the same defaults `write_test_gold_metadata` has today
  (`tests/models/helpers.py:199-221`: label definition, `reference_date=REFERENCE_DATE_STR`, window 30, lookback 1,
  codes, counts from `df`'s `is_readmitted`); `REFERENCE_DATE_STR = "20260916"` moves into `tests/gold_helpers.py` and
  `tests/models/helpers.py` imports it from there (same value; no duplicate). `tests/models/helpers.py::write_test_gold_metadata` becomes a thin
  wrapper around `write_gold_metadata_for_frame` (same signature + `version`); passing both `version` and a conflicting
  `metadata_version` override raises `ValueError`. `test_gold_helpers_import_no_mlflow` (subprocess import, then
  `"mlflow" not in sys.modules and "sklearn" not in sys.modules`) protects this.
- `test_big_join_metadata_fingerprint_matches_loader` (end-to-end on `FIXTURE_ROWS`: the written metadata's fingerprint
  equals `rowhash_fingerprint(load_gold_table(output))`).
- `test_fingerprint_independent_of_partitioning` (`repartition(1)` vs `repartition(7)` → same value).
- `test_metadata_v2_written` (version 2, definition, 64 hex, `spark_version == spark.version`).
- `test_big_join_end_to_end_through_hadoop_storage` and `test_run_big_join_cli_through_hadoop_storage`: the offline proof of
  the `gs://` branch. `storage_for` gets one test seam, a module-level `_HADOOP_SCHEMES = ("gs",)`; the tests monkeypatch it
  to `("gs", "file")`, so `file:///…` input and output URIs (from `tmp_path.as_uri()`) take exactly the code path a
  `gs://` URI takes (string joins, `HadoopStorage` checks, summary read, Parquet write/read by URI, metadata via
  `write_text_new`, the CLI's post-build `parse_gold_metadata`). Both assert the same gold rows and the same fingerprint
  as the pathlib run on `FIXTURE_ROWS`. (Hadoop's local filesystem writes `.crc` side files there; the tests ignore them.)
  The CLI test loads `scripts/run_big_join.py` with `importlib.util.spec_from_file_location` (the pattern of the other
  script tests) and monkeypatches that module's `make_spark_session` to return a thin proxy around the session fixture
  whose `stop()` is a no-op and whose other attributes delegate (`__getattr__`), so `main`'s `finally: spark.stop()`
  cannot stop the shared session.
- `test_fingerprint_agreement_under_non_utc_process_tz`: the 6-row agreement fixture lives in
  `tests/pipeline/agreement_fixture.py` (`build_agreement_frame(spark) -> DataFrame`, importable, no test functions), used
  by both tests; runs a small script (`tests/pipeline/tz_agreement_probe.py`, not collected by pytest) in a subprocess with `TZ=America/New_York` and `PYSPARK_PYTHON=sys.executable` in its
  environment; the script builds its own session with `make_spark_session`, writes the agreement fixture, prints the
  Spark fingerprint and the Python one; the test asserts they are equal AND equal to the in-process (UTC) value. This
  catches any driver- or worker-local-zone dependence in the `mapInPandas` timestamp path, which CI's UTC runners and
  Dataproc cannot reveal. Runs in both CI test jobs (~20 s: a second JVM).
- `test_gs_path_object_rejected` (`BigJoinConfig(input_dir=Path("gs://b/x"), output_dir=tmp, reference_date="20260916")`
  → `ValueError`), `test_big_join_local_str_paths_equal_path_objects` (same output with `str` and `Path`).

`tests/pipeline/test_storage.py`
- `test_storage_for_routes_local_path_and_plain_str`, `test_storage_for_routes_gs`, `test_storage_for_rejects_other_schemes`,
  `test_gs_requires_spark`; for `LocalStorage` and `HadoopStorage` (`file://` via direct construction, using the session
  `spark` fixture): `exists`, `is_file` (directory → False), `has_data_row` (header only → False, header + 1 → True,
  missing → `FileNotFoundError`), `read_text` UTF-8 round trip (non-ASCII), `write_text_new_refuses_existing`;
  `test_local_write_leaves_no_crc`.

`tests/pipeline/test_gold_metadata.py` (additions)
- `test_v2_round_trip`, `test_v1_file_still_reads_and_reserializes_byte_identical` (today's exact key set and bytes),
  `test_v2_missing_fingerprint_refused`, `test_v1_with_fingerprint_refused`, `test_unknown_version_names_supported`,
  `test_parse_matches_read`, `test_v2_bad_hex_refused`, `test_v2_wrong_definition_refused`.

`tests/pipeline/test_spark_session.py` (additions)
- `test_builder_settings_local_default` (master `local[*]`, UTC in conf, `PYSPARK_PYTHON` default),
  `test_builder_settings_master_none` (master None, env_defaults empty), `test_conf_cannot_override_utc`,
  `test_conf_entries_passed_through`.

`tests/pipeline/test_generation.py` (additions)
- `test_command_unchanged_without_new_options`, `test_clinician_seed_flag_position`, `test_included_files_flag_before_state`,
  `test_included_files_must_cover_expected`, `test_included_files_rejects_duplicates_and_non_csv`,
  `test_java_max_heap_position_and_format`, `test_jvm_timezone_flag_position_and_format`, `test_summary_has_new_keys`, `test_peak_rss_normalised_per_platform` (monkeypatched `sys.platform` and `resource.getrusage`).
- `test_generation_code_is_stdlib_only` (AST walk of `pipeline/generation.py`, `readmission_risk/__init__.py`,
  `pipeline/__init__.py` and `scripts/generate_synthea_data.py`: every import is stdlib, `readmission_risk`, or
  `readmission_risk.pipeline.generation`).

`tests/models/test_data.py` (additions)
- `test_loader_accepts_v2_with_matching_fingerprint`, `test_loader_refuses_count_preserving_edit_v2` (write v2 metadata,
  then swap two `gender` values in the Parquet: counts unchanged, fingerprint differs → `ValueError`),
  `test_loader_v1_unchanged`.

`tests/models/test_training.py` (additions)
- `test_rowhash_tag_only_for_v2_gold` (v1 gold → no tag; v2 gold → tag equals the metadata's value),
  `test_gold_source_param_logged`.

`tests/models/test_bundle_export.py` (additions)
- `test_export_uses_rowhash_when_tag_present` (a `clone_run` of a `scoring_env` run with
  `tags={"gold_fingerprint_spark_rowhash_v1": <hex>}` → manifest definition `spark-rowhash-v1` and that value).
  The existing assertion at L56 (`pandas-csv-sha256-v1` for the untagged `scoring_env` runs) stays as the "unchanged"
  oracle, and the existing idempotent re-export test keeps proving the same `bundle_id`.

`tests/scoring/` (guard 6 additions; built with the existing `tamper_bundle` helper, which re-seals the manifest)
- Standalone tests in `tests/scoring/test_bundle_guard.py` (not `BundleCase` entries, which can only swap the bundle
  URI) calling `validate_bundle(uri, gold=…, metadata=…)` directly with a hand-built frame and `GoldMetadata`:
  `test_rowhash_bundle_matches_own_gold` (a `scoring_env` bundle re-sealed by `tamper_bundle(set={…definition: rowhash,
  gold_fingerprint: <hex of the scoring gold>})`, metadata v2 with the same hex → passes),
  `test_rowhash_bundle_refuses_edited_gold` (one cell of the frame changed → guard 6 message), and
  `test_rowhash_bundle_refuses_metadata_fingerprint_mismatch` (frame unchanged, metadata's recorded hex differs → the
  new guard-6 message). End to end, `test_score_refuses_edited_v2_gold` (a `score_run_date` run on a v2 gold copy with
  one swapped cell) expects the LOADER's message. The existing unknown-definition case is kept; the v1 (`pandas-csv`)
  guard cases run unchanged, so both definitions are covered.

`tests/ops/test_prereg.py`
- `test_repo_prereg_is_valid`, `test_prereg_matches_code_constants` (label, codes, window, lookback, tables, XGB/LR
  params, cv folds), `test_population_seeds_distinct_and_not_v1`, `test_reference_date_not_v1`,
  `test_test_start_rule_resolves`, `test_literal_fields_enforced` (parametrized over every literal field),
  `test_unknown_key_refused`, `test_missing_key_refused`, `test_numeric_bounds`, `test_deviation_entry_schema`; with a
  temporary git repo: `test_committed_check_passes`, `test_refuses_untracked`, `test_refuses_modified`,
  `test_refuses_dirty_tree`, `test_refuses_head_not_on_remote`. Hermetic git: a fixture in `tests/ops/conftest.py`
  sets `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_AUTHOR_NAME/EMAIL` and `GIT_COMMITTER_NAME/EMAIL`
  (`test` / `test@example.invalid`) via monkeypatch, and every `git` call in the fixture passes `-c commit.gpgsign=false
  -c init.defaultBranch=main -c core.hooksPath=/dev/null`; the "remote" is a bare repo created with `git init --bare`
  in `tmp_path`, added as `origin` by its filesystem path, so `check_prereg_committed`'s `git fetch origin` works
  offline. `test_prereg_jar_sha_matches_setup_script` checks `generation.synthea_jar_sha256` equals
  `scripts/setup_synthea.sh`'s `EXPECTED_SHA256` (L12).

`tests/ops/test_spark_zip.py`
- `test_zip_contents_equal_spark_job_sources` (compares against `SPARK_JOB_SOURCES` filtered through `git ls-files`, so
  an untracked work-in-progress file on a developer machine cannot fail it), `test_zip_is_byte_deterministic_and_pinned_format` (two builds → same
  sha256; every entry `ZIP_STORED`, date 1980-01-01, mode 0o644), `test_zip_importable_isolated` (subprocess
  `python -I -S -c "import sys; sys.path.insert(0, ZIP); import readmission_risk.pipeline.generation, readmission_risk.pipeline.gold_metadata"`
  with cwd = tmp: `-S` drops site-packages, so this proves the generation path needs nothing outside the stdlib and the
  zip), `test_no_pycache_or_dirs`.

`tests/ops/test_generation_vm.py`
- argv: `test_create_argv_uses_template_prereg_values_and_explicit_purpose`, `test_labels_sanitised`,
  `test_metadata_has_every_key`, `test_included_files_joined_with_semicolons`; preflight with a fake runner: one test
  per refusal P1-P6 (first failure wins), `test_main_run_requires_flag`; flow: `test_done_marker_then_delete_then_proof`,
  `test_positive_control_mismatch_deletes_and_exits_2`, `test_vanished_without_marker_exit_2`,
  `test_failed_marker_exit_2_and_proof_runs`, `test_leftover_disk_exit_3`, `test_keyboard_interrupt_tears_down_exit_130`,
  `test_existing_artifact_with_same_sha_accepted_other_refused`; `test_constants_match_terraform`,
  `test_default_compute_sa_never_used` (argv and `infra/compute.tf`), `test_dry_run_makes_no_runner_call` (the fake runner records zero calls).

`tests/ops/test_dataproc_batch.py`
- `test_submit_argv` (contains `--version=3.0`, `maxExecutors=7`, `dataproc.tier=standard`, `shuffle.partitions=128`,
  runtime SA, subnet, staging bucket, `--ttl=2h`, `--spark-master external`, and reference date, lookback and window
  taken from the prereg: asserted by passing a prereg with non-default values), preflight refusals P1-P6,
  `test_parse_batch_usage` (fixture JSON → DCU-hours = milliDcuSeconds / 3.6e6, shuffle GB-hours, wall-clock),
  `test_failed_batch_exit_2_prints_cleanup`, `test_keyboard_interrupt_exit_130_prints_cancel`.

`tests/conftest.py`: `_no_real_gcp` patches `readmission_risk.ops.gcloud.default_runner`; tests in
`tests/ops/test_gcloud.py`: `test_default_runner_refused_in_tests` (calling `resolve_runner(None)([...])` raises),
`test_gcloud_module_is_stdlib_only` (AST), `test_no_bound_default_runner` (no `= default_runner` default argument and no
`import default_runner` in `src/readmission_risk/ops/` or `scripts/`), `test_gcloud_env_disables_composite_uploads`.

`tests/ops/test_dataproc_batch.py` additions: `test_p7_refuses_raw_from_other_prereg`, `test_p7_refuses_seed_mismatch`
(one case per compared field), `test_parse_batch_usage_string_int64_and_missing_usage`.

`tests/pipeline/test_generation.py` additions: `test_provenance_written_verbatim`, `test_provenance_rejects_empty`,
`test_end_date_flag_and_validation`, `test_exit_on_oom_only_with_heap`, `test_alive_population_check` (fake
`patients.csv` with 3 alive + 2 dead rows: population 3 passes, 4 raises), `test_alive_check_off_by_default`.
`tests/pipeline/test_generation.py`: `test_parse_provenance_args` (missing `=`, empty key, empty value, repeated key →
`ValueError`; valid pairs → dict; `None` → `{}`). `tests/ops/test_generate_cli.py`: `test_generate_cli_provenance_error_exits_2`
(loads `scripts/generate_synthea_data.py` with `importlib.util.spec_from_file_location`; a bad `--provenance` →
`SystemExit(2)`). `tests/pipeline/test_gold_metadata.py` additions: `test_v1_with_build_provenance_refused`,
`test_v2_missing_build_provenance_refused`, `test_v2_build_provenance_must_be_str_to_str`.

`tests/ops/test_gcloud.py` additions: `test_list_objects_no_match_is_empty`, `test_list_objects_other_error_raises`.

`tests/ops/test_prereg.py` additions: `test_check_prereg_against_code_reports_every_mismatch`,
`test_deviation_pending_only_last`, `test_end_date_must_be_reference_date_plus_one`, `test_jvm_timezone_must_be_utc`.

`tests/ops/test_spark_zip.py` addition: `test_untracked_file_not_shipped` (temporary git repo).

`tests/pipeline/test_storage.py` addition: `test_hadoop_storage_rejects_connect_session` (an object without `_jvm`).

### 5b live checks (after 5a merges into `v2`; each step confirmed with the owner; results go into this note)

- **L0** Preconditions: 5a merged into `v2`; working tree clean at the merge commit; F4 read.
- **L1** `terraform plan` must show exactly **27 to add, 0 to change, 0 to destroy**: 3
  `google_project_service.enabled[...]` (compute and dataproc, already on, so a no-op enable; policytroubleshooter, a
  real enable), `google_compute_network.spark`, `google_compute_subnetwork.spark_us_central1`,
  `google_compute_firewall.spark_internal`, `google_storage_bucket.artifacts`, `google_storage_bucket.dataproc_staging`,
  2 `google_service_account`, 16 `*_iam_member`, `google_compute_instance_template.synthea_generator`. Owner confirms;
  `apply`; a second `plan` shows no changes; Results records `gcloud compute images describe-from-family debian-13
  --project debian-cloud` (the image the first VM will get).
- **L2** IAM, before any compute. Command form: `gcloud policy-intelligence troubleshoot-policy iam
  //storage.googleapis.com/projects/_/buckets/<bucket>/objects/<object> --principal-email=<sa> --permission=<perm>`
  (bucket-level checks use `//storage.googleapis.com/projects/_/buckets/<bucket>`). Pass = the overall access state is
  GRANTED (resp. NOT_GRANTED) as listed below. An UNKNOWN or conditional-unevaluated answer is NOT a pass. There is no
  live fallback probe (the operator has no impersonation binding on `dataproc-runtime`, deliberately), so on UNKNOWN the
  slice stops before L4 and the owner decides between (a) adding a temporary `serviceAccountTokenCreator` binding for a
  live delete probe on a sentinel object (then removing it) or (b) accepting "condition configured, Troubleshooter
  UNKNOWN" for `full_run_2b/`, recorded in Results. Checks, via Policy Troubleshooter, for
  `dataproc-runtime`: GRANTED `storage.objects.create`/`delete`/`get` on `…-gold/objects/v2/x`; DENIED
  `storage.objects.delete` on `…-gold/objects/full_run_2b/x`; GRANTED `storage.buckets.get` and `storage.objects.list`
  on the raw, gold, artifacts and staging buckets; GRANTED `storage.objects.get` on raw. For `synthea-generator`:
  GRANTED `storage.objects.create` on raw, DENIED `storage.objects.delete` on raw. The template describes with SPOT,
  DELETE, `maxRunDuration` 7200 s, no access config.
- **Recording rule for 5b:** the CLIs refuse a dirty tree (P1), so interim results are NEVER written into this tracked
  note during 5b: every step's outputs go to the gitignored `data/pilot/<run>/` (the CLIs already write there; manual
  checks append to `data/pilot/<run>/live-checks.md`). After L9 they are transcribed into Results in one docs PR into
  `v2`.
- **L3** `prereg.yaml`'s commit precedes everything below (record its commit hash, committer date and PR merge time in
  `data/pilot/pilot_20k/live-checks.md`).
- **L4** Pilot generation: `make generate-vm RUN=pilot_20k …` exit 0; record VM total time, Synthea `duration_seconds`,
  `peak_child_rss_bytes`, raw size (`gcloud storage du -s`), row counts; the positive control's output (the label filter
  listed the live VM); teardown proof empty.
- **L5** Big Join: `make bigjoin-batch RUN=pilot_20k` exit 0; record wall-clock, DCU-hours, shuffle GB-hours,
  `runtimeConfig.version`, gold rows/positives.
- **L5 gold layout:** `gcloud storage ls --recursive gs://…-gold/v2/pilot_20k/**` must list only `part-*.parquet`,
  `_SUCCESS`, `_gold_metadata.json`, and optionally zero-byte directory-placeholder objects whose name ends in `/`
  (the GCS connector may create them; `warehouse/gold_source.py:57` already skips the prefix's own empty name, and the
  check allows exactly placeholders for `v2/pilot_20k/` itself) — the set `warehouse/gold_source.py:57-62` accepts; no
  `_temporary/`, no other marker; anything else stops the slice before L6 and is recorded (it would mean the committer on Dataproc behaves
  differently from local Spark, which L6's downloader would refuse).
- **L5a** Pilot gold acceptance (pass criteria; any failure stops the slice before slice 6, and is investigated, not
  waived): A1 `n_rows > 0`; A2 `n_planned_stays > 0` (the planned-procedure codes still match); A3 overall positive rate
  `n_positive / n_rows` within [0.5%, 5%] (v1 at 10k: 1.63%); A4 no all-null column among the 24, and `payer_ownership`
  null fraction ≤ 10% (v1 data: computed and recorded at implementation from `full_run_2b` for comparison);
  A5 `generation_summary` in the gold metadata equals the raw run's `generation_summary.json`, and its
  `provenance.prereg_sha256` equals the sha256 of `git show <provenance.prereg_commit>:prereg.yaml` (the prereg in force
  at generation; later deviation commits do not fail A5, consistent with P7); A6 the pilot's test-window positive count (as defined in `power.population_adjustment`) is
  recorded, and the pre-registered population rule is applied and recorded (deviation or "no change"). The A1-A6
  values are computed by a short documented snippet (pandas, on the downloaded gold) whose output is pasted into
  Results. A7 (on the raw data downloaded at L7): the latest `START` and `STOP` in `encounters.csv` are
  `< reference_date + 1 day` 00:00Z and the latest `START` is `>= reference_date` 00:00Z (the simulation covered the
  reference day and no more). If a failed check leads to a fix that changes an analysis value (label rules or codes,
  split, features), the fix is NOT made inside this slice: the slice stops, the change goes through a new decision
  (`/eg-prd`), and it is recorded as a `deviations` entry with reason prefix `pilot-sanity:` before `main_200k` exists;
  a label change also follows the `LABEL_DEFINITION` identity rule (`gold_metadata.py:28-32`, bump to `_v2`).
- **L6** FR12 cross-environment: on the laptop (Python 3.14, pandas 2.3.3), `load_gold_table_with_metadata` on
  `materialized_gold_dir("gs://…-gold/v2/pilot_20k")` succeeds AND the returned metadata has `metadata_version == 2`,
  `gold_fingerprint_definition == "spark-rowhash-v1"`, and `rowhash_fingerprint(frame) == metadata.gold_fingerprint`
  (asserted explicitly, so a v1-metadata gold cannot pass L6 vacuously), i.e. the Python recomputation equals the
  fingerprint Spark computed on Dataproc (Python 3.12, Spark 4.0.1, Java 21).
- **L7** (required since revision 5; ≈ 4.4 GB egress ≈ $0.5; owner confirms the download): download the pilot raw to
  `data/raw/v2_pilot_20k/`. (a) FR11 local-vs-cloud equality: run the local Big Join (`local[*]`, PySpark 4.1.3, Java 17)
  into `data/gold/pilot_20k_local`; both metadata files are version 2 with definition `spark-rowhash-v1`, and the local
  fingerprint must equal the Dataproc gold's. (b) A7 above. (c) Cross-machine
  Synthea determinism: regenerate the pilot on the laptop with the same argv except the heap (jar, JDK 21.0.12.1+1
  unpacked locally, `-Duser.timezone=UTC`, seeds, dates, tables; `-Xmx48g` instead of the VM's 96g, because the laptop has
  64 GB; 18 cores instead of 32 vCPUs). Heap size and thread count are ASSUMED not to change the output; this comparison is
  exactly the test of that assumption) into `data/raw/v2_pilot_20k_laptop/` (≈ 3 min, 4.4 GB local) and compare
  each table's sorted content with the VM's, except the four known non-deterministic `payers.csv` aggregate columns
  (compared with those columns dropped). Equal → the regeneration path is proven across machines; different → recorded,
  and the reproducibility claim is narrowed to "same machine type and thread count" in CLAUDE.md (this does not stop the
  slice, because the gold is pinned by its fingerprint and lineage either way).
- L6 or L7 failing stops the slice like L5a: no slice 6 until the cause is found (a fingerprint definition bug, a
  dtype path difference, or a real local-vs-Dataproc output difference) and fixed with a test (L7c excepted, see above).
- **L8** Nothing left running: labelled instances/disks empty; `gcloud dataproc batches list --filter="state=RUNNING OR
  state=PENDING"` empty.
- **L9** About 24 h later: the billing report (VM, Dataproc, GCS, egress) recorded and compared with the projection.
- Derived and recorded: the 200k projection (generation time × 10 and Big Join scaling, both stated as assumptions), the
  proposed slice-6 `max_run_duration` and heap, projected 200k test positives vs M7's 400, and whether the 200k run fits
  the ≤ $50 one-off budget.

## Out-of-scope follow-ups

- Slice 6: the 200k generation (`--allow-main-run`) with `max_run_duration`/heap from L4; check that main_200k's
  patient-Id set is disjoint from BOTH the pilot's and v1's (seed 42; the frozen-v1 models were trained on v1, so this is
  the check that protects FR16's external validation); the Big Join at 200k; FR14's runtime sub-version + connector version in
  metadata (metadata v3) and the BigQuery direct-write with the connector MERGE test; `observed_expected_ratio` in
  `models/evaluate.py`; retraining, the promotion gate (FR15), frozen-v1 (FR16), the portable export (FR0.3); Phase B
  of the prereg.
- A WIF-bound submitter SA and workflow for the Big Join (laptop-run in this slice).
- `serving.predictions` / `scoring_runs` carry `gold_fingerprint` without its definition (`ranking.py:161`,
  `warehouse/schemas/*.json`): once rowhash bundles are scored (slice 6) a 64-hex value there is ambiguous; add a
  `gold_fingerprint_definition` column (a BigQuery schema change in `serving`, owner-applied) before the first rowhash
  bundle is scored.
- The raw bucket's 90-day delete rule also covers `v2/main_200k/`: reproducibility then rests on regenerating with the
  pinned jar, JDK, `-s` and `-cs` (content-deterministic, measured) plus the generation summary embedded in the gold
  metadata; slice 6 decides whether to exempt `v2/main_200k/` (a lifecycle `matches_prefix` exclusion) for the
  validation period.
- Removing `roles/editor` from the default compute SA (owner decision; non-additive).
- GCS connector for local Spark against `gs://` (PRD degraded mode).
- CLAUDE.md / PRD corrections: "deterministic per seed" needs "and clinician seed"; v1's raw data is not regenerable
  byte-for-byte; `generation.py`'s comment names Synthea v4.0.0 while the jar reports `v3.4.0-18-ga07a65555`.
- Still-open TODOs carried unchanged: V-post and L-post (after v2 merges into `main`), F4, the terraform-fmt pre-commit
  hook, the lockfile-drift CI check, the pandas 3 check.

## Revision log

Revision 1 (after round 1): critic gaps → resolution. 1 manifest stdlib rule → constant moved to `gold_metadata.py`
(§1, §7). 2 helpers → `write_test_gold_metadata(version=1)` default + test_gold_metadata builder; the critic's
`tests/scoring/helpers.py:627` builds a `ModelBundle`, not `GoldMetadata` (checked), so it is unaffected ("Existing tests
changed on purpose"). 3/4 training tag → recorded only for v2 gold; scoring_env stays pandas-csv; rowhash covered by
`tamper_bundle`/`clone_run` tests (§7, tests). 5 startup reads every parameter from metadata (§11). 6 lookback/window
from prereg (§11, test). 7 `dataproc.tier=standard`, `shuffle.partitions=128` (§11). 8 seed rule scoped to population
seeds (§9). 9 O/E and ECE defined, hyperparameters pre-registered and tied to code by tests (§9). 10 `pd.NA`,
`numpy.bool_` (§1, tests). 11 name-set test (tests). 12 explicit labels/metadata + positive control (§11). 13
`ZIP_STORED` (§10). 14 `python -I -S` import test + AST stdlib test (tests). 15 `legacyBucketReader` + broader L2. 16
clinician seed measured and pinned (finding above, §8, §9). 17 partial-gold row + cleanup command (Failure modes). 18
wording softened + slice-6 disjointness check (D5, follow-ups). 19 quotas measured, P6 preflight (§11). 20 exit 130.
21 `${path.module}`. 22 `_no_real_gcp` named correctly. 23 `gold_source` param (§6). 24 image family not pinned, stated.
Readiness questions → 1 = critic 2; 2 = critic 3; 3 = critic 1; 4 `__post_init__` + full-config test (§4); 5 dedicated
fixture (tests); 6 jar absent → null (§8); 7 `__post_init__`, argv order (§8); 8 `BuilderSettings` (§5); 9 CLIs build the
zip themselves, `--jar-path` flag (§10, §11); 10 = critic 12; 11 = critic 19; 12 literal fields, deviation schema,
window/lookback equality (§9); 13 Makefile targets (§13). Comprehension notes: shorthand defined at the top; peak RSS
wording made precise (§8).

Revision 2 (after round 2): critic → 1 agreement test made mlflow-free (`tests/gold_helpers.py`), runs in the
Dataproc-runtime job. 2 `test_gold_metadata.py` edits enumerated (`raw_json`, len 12, `BAD_VALUES` 2 → 3, parse order),
`training.py:293` artifact via `metadata_to_dict`. 3 `ops/gcloud.py` stdlib-only, owns `PROJECT`; call-time runner lookup
(`resolve_runner`) + static test. 4 positive control checks the boot disk label and that both filters match. 5 composite
uploads disabled everywhere (`GCLOUD_ENV`, VM export). 6 exact upload commands + layout check (launcher step 7, P4). 7
pilot acceptance A1-A6 + pre-registered population-adjustment rule. 8 `generation_summary` embedded in metadata v2 +
`provenance` + submitter P7. 9 loader insertion point fixed (after L194-203, before L205). 10 guard-6 branch is defence in
depth, tested via `validate_bundle` directly; end-to-end expects the loader. 11 one source of truth (`gold_metadata`),
re-exported by `manifest.py`. 12 `gold_source: str | None = None`; version/override conflict raises. 13 run duration only
in Terraform, read from the template; startup script out of the template. 14 exact constant list. 15
`runtimeInfo.approximateUsage.*`, string int64, missing usage. 16 more code equalities; bootstrap B/seed explicitly slice
6's to honour. 17 `git fetch` + `head_commit`/`prereg_commit`. 18 predictions definition column → follow-up. 19 classic
Spark stated + constructor check. 20 90-day rule on `v2/main_200k` stated, slice-6 decision. Readiness → 1 = critic 2;
2 = critic 12; 3 = critic 10; 4 JDK 21.0.12.1+1 pinned with sha256; 5 = critic 14; 6 timeout → exit 2; 7 submitter
dry-run defined; 8 leaf rules (booleans pinned, lists from known sets, more equalities); 9 label regexes; 10 `PROJECT` in
`ops/gcloud.py`; 11 `run_provenance.json` keys + step-7 print; 12 Policy Troubleshooter API added; 13 manifest re-export
purpose.

Revision 3 (after round 3): critic → 1 rebutted with measurement: with the jar's default `excluded_files` still set,
`included_files` was honoured in three runs (2, 3 and 10 requested files → exactly those written); uploads are now per
named table anyway. 2 deviation semantics defined (in-place + entry; values in force validated); P7 uses prereg-commit
ancestry instead of sha equality. 3 image from the metadata server. 4 `-XX:+ExitOnOutOfMemoryError` + alive-population
check (+ P7). 5 runtime `check_prereg_against_code` (P1b). 6 v1-vs-main disjointness added to slice 6. 7 submitter P6
adds regional CPUS and DISKS_TOTAL_GB. 8 pilot positives = the split's `n_test_positive`. 9 L6/L7 failure stops the
slice. 10 determinism re-measured at 1k × 10 tables: found the wall-clock end-date effect and payers aggregates; `-e`
pinned. 11 zip from `git ls-files`. 12 Hadoop error mapping + CLI `OSError`. 13 `PROJECT` from `gcp.PROJECT_ID`. 14 log
tee. 15 PR merge time recorded at L3. Readiness → 1 test_infra/test_tooling edits enumerated; 2 `depends_on`; 3 = critic 3;
4 summary always gains keys (existing tests assert individual keys); 5 runtime equality (P1b); `prereg_version` literal
1; 6 test_big_join: additions only; 7 `list_objects`/`object_exists`/`list_labelled` semantics; 8 P3 refuses leftover
disks; 9 `--provenance` parse errors; `.gitignore` note accepted.

Revision 4 (after round 4; owner: "apply the fixes and run one more round"): readiness → 1 `py4j` added to the FR3
allowlist (already hash-pinned; PySpark's own dependency). 2 `SyntheaValidationError(message=…)`. 3 existence check +
`md5_hash` compare, no stderr parsing. 4 exact run-time sources for cv_folds and LR params. 5 dry-runs include P1b.
Critic → 1 `_HADOOP_SCHEMES` seam + two end-to-end tests through `HadoopStorage` on `file://`. 2 startup ordering, plain
redirect, `set -E`, tool check, curl fallback. 3 gold layout check at L5. 4 `type(v) is int`. 5 `n_test` and code-derived
name sets. 6 exact Troubleshooter command, UNKNOWN is not a pass. 7 deep copy, unhashable stated. 8 exact allowances,
empty `ops/__init__.py`. 9 tracked-files comparison. 10 timestamp input types. 11 tool check + image recorded at L1.

Revision 5 (after round 5; owner: "revise + one more round"): critic → 1 `end_date` = reference_date + 1 and
`-Duser.timezone=UTC` (both measured), A7 raw-coverage check. 2 summary validated at read time. 3 non-UTC agreement test
in a subprocess. 4 L7 made required and gains a cross-machine regeneration comparison. 5 hermetic git fixture. 6
`MAKEFILE_VARIABLES`. 7 `upper(var.region)`, `_collapse`. 8 exact L1 plan (27 to add). 9 generation.py hashes nothing
(the VM passes the verified hash); prereg jar hash tied to `setup_synthea.sh`. 10 A5 against the prereg at generation.
11 pilot-informed analysis changes stop the slice and go through `/eg-prd` + a `pilot-sanity:` deviation. 12 CLI dates
validated before download. Readiness → 1 proxy session with no-op `stop()`; 2 `on_error` reads `raw_prefix` itself; 3
P1b right after P1; 4 exit precedence 3 > 2 > 130 > 0; 5 `write_gold_metadata_for_frame` signature and defaults,
`REFERENCE_DATE_STR` moved.

Revision 6 (after round 6; readiness READY, critic 7): 1 `build_provenance` in metadata v2 + `--provenance` on the Big
Join CLI and the batch record. 2 zero-byte placeholders allowed in the L5 layout check. 3 templates per run class, named
with their duration (`create_before_destroy` was later removed in the pre-commit review); launcher resolves by name pattern. 4 the five contradictions fixed. 5
extraction method and `pd.isna` null rule fixed (NumPy NaT covered). 6 laptop heap `-Xmx48g`, assumption stated. 7 metric
names derived at run time from `evaluate_probabilities`. Readiness notes: agreement fixture module named; stale wording
fixed.

Revision 7 (after round 7; critic 9, readiness 6): critic → 1 five v2-only fields everywhere + `build_provenance` rule +
two tests. 2 5b recording rule (gitignored `data/pilot/<run>/`, one docs PR after L9). 3 duplicate-key YAML loader. 4 L6/L7a
assert v2 + definition + equality. 5 integrity-not-security wording. 6 `parse_provenance_args` in `pipeline/generation.py`.
7 `models.split.SPLIT_SUMMARY_KEYS` + test. 8 JDK `--strip-components=1` + version check. 9 zone/region preflight.
Readiness → 1 = critic 1; 2 `None` → `{}`; 3 = critic 6; 4 summary `provenance` always written; 5 `on_error` once, then
`sleep infinity`; 6 template output is a map.

**Implementation-time changes (2026-10-12, after the override; the code is authoritative):** the startup script is passed to
`gcloud compute instances create` by its absolute path (`<repo>/infra/generation/startup.sh`), so the launcher works from any
directory; the JDK tarball is downloaded into the gitignored `tools/jdk/` (an untracked file elsewhere would make P1's
clean-tree check refuse the launcher's own input); a gcloud failure during teardown is caught and reported with the manual
commands (exit 3); the startup log is `tee`d to Cloud Logging; `create_before_destroy` is not used. The prereg keeps the
JDK's file name under `generation.jdk.file` (not in the original schema sketch). Population/Generation are typed; the other
prereg sections are validated by the strict schema and kept as the parsed mapping (`Prereg.raw`).

## Results

(5b fills this in.)
