# v3 slice 3: the Streamlit dashboard app, offline (PRD FR10-FR19)

**Status:** design revision 10 (2026-11-01, `/eg-new-feature`), the binding text. **Gate note:** readiness closed in rounds 5
and 9; the critic never closed (round 9: 14 precision-level gaps, folded into revision 10 without re-review); the owner chose
"fix all, then implement" (override, 2026-11-01), slice 2's precedent. Design-check log: round 1: comprehension
passed, critic 13 gaps, readiness 8 questions (→ revision 2; owner decisions S5-S7). Round 2: critic 12, readiness 10 (→ 3; S8,
S9). Round 3: readiness 2 (→ 3.1), critic 6 (→ 4; S10, S11). Round 4: readiness 1 (→ 4.1), critic 13; at the 3-revision cap the
owner chose "fold in all fixes and re-run B/C" (→ 5; S12). Round 5: readiness CLOSED, critic 10; owner: "fix all, re-run B/C
once" (→ 6; S13-S15, S13 supersedes S8). Round 6: readiness 3 (→ 6.1), critic 10; owner: "fix all, one more B/C round" (→ 7).
Round 7: readiness 2 (→ 7.1), critic 10; owner: S16, S17 (S17 supersedes S1's placement), "fix all, one more B/C round" (→ 8).
Round 8: readiness 4 (→ 8.1), critic 11 (→ 9, no new owner decision). Round 9: readiness CLOSED, critic 14 (→ 10; S18;
owner override to implement). Branch `v3-slice3-dashboard-app` off `v3`
(`b46a940`); PR into `v3` (ruleset 24876876: PR required, 0 approvals), never into `main`. **Offline only:** no Terraform, no GCP
call, no live step (Cloud Run, the image and the live smoke are slice 4). PRD:
`notes/prds/v3-cloud-run-call-sheet-bigquery-gold-2026-10-28.md` (V2-V5, V7, V12, V13, V16, V17; FR10-FR19; NFR accessibility and
security; Implementation hints slice 3; Open questions 1, 3, 5). Slice 2 (`notes/eg-new-feature/v3-slice2-authorized-views-2026-10-31.md`,
owner decisions D1-D5, elephant resolutions E1/E2) is binding. Where the PRD and CLAUDE.md differ, the PRD wins; where this note
and the PRD differ, this note records an owner decision.

## Owner decisions (2026-11-01)

| # | Decision | PRD effect |
|---|---|---|
| S1 | *(placement superseded by S17; the versions and the watchdog finding stand)* **Pins in `requirements.in`:** `streamlit==1.66.1` (2026-10-21) and `altair==6.3.0` (2026-09-15), both older than the 7-day Dependabot cooldown, compiled by `make lock-main` with the existing pins kept (pandas 2.3.3, numpy 2.4.4, pyarrow 25.0.1, which satisfies Streamlit's `pyarrow!=25.0.0`). `watchdog==6.0.0` (Streamlit requires `watchdog<7` off macOS only, which pip-compile on macOS cannot see; 6.0.0 is the only 6.x) goes into the hand-maintained `requirements-linux-extras.txt`. Rejected: `requirements-dev.in` (compiled without `-c requirements.txt`, so it would resolve pandas 3 / numpy 2.5 and overwrite the holds when CI installs it second) and bringing forward slice 4's `requirements-dashboard.in`. | superseded by S17 (the shared-pin equality is D8, built here) |
| S2 | **M1 evidence = AppTest in CI** (+ slice 4's `/_stcore/health` and revision-digest smoke). No Playwright. | answers Open question 1 |
| S3 | **Subgroup calibration table in the model card only** (slice 5). The app shows no demographic data. | answers Open question 5 (placement only) |
| S4 | **Cache TTL 3 hours, at most 64 entries per loader; the PRD's four failure classes** exactly: `unreachable`, `bytes_cap`, `quota`, `empty`. An IAM refusal or any other exception raised inside a query call shows the `unreachable` panel (accepted; the server log has the class and traceback). | fixes FR15's TTL and FR16's classes |
| S5 | **`st.cache_resource`, not `st.cache_data`.** `st.cache_data` pickles its entries and calls `pickle.loads` on every hit (Streamlit 1.66.1 `runtime/caching/cache_data_api.py:856`), so the app would unpickle and the armed E2 trap trips on every rerun (measured by the critic). `st.cache_resource` keeps the returned object in memory without pickling (measured in a scratch app with `pickle.load`/`loads` patched to raise: hits, misses and per-argument entries all work). Loaders return immutable values. FR18's "never unpickles" stays literally true and the full trap stays armed in every dashboard test. | implements FR15's cache with FR18 intact |
| S6 | **V7 governs the clock, not recorded timestamps.** The simulated date is "today" in every view; `heartbeat.wall_now()` is the only wall-clock READ (freshness). The run-history tables still show each run's recorded wall time, headed "Ran at (wall clock, UTC)", next to its simulated/run date, as the ledger does. | interprets V7 |
| S7 | **Bundle ids come from the data.** The app cannot read `deploy/champion.json` (its reader `bundles.champion` is import-banned). Heartbeat: the monitored bundle (D5's rule; the bundle the weekly tick monitors, i.e. the champion). Footer: the distinct `model_bundle_id`s of every call-sheet row on screen (Top-N and the rest). | replaces FR10/FR11's "champion bundle" wording |
| S8 | *(superseded by S13)* **"Late" at anchor + 10 days, not 8.** `WEEKLY_CRON` retries hourly Monday-Wednesday and about 19% of slots fire, so a Monday 00:17 success followed by the next week's Wednesday 23:17 success is 9 d 23 h apart: an 8-day warning would fire in normal operation. Stale stays the ledger's rule (anchor + 2 periods = 14 days). | changes FR11's "warn after 8 days" to 10 |
| S9 | **`allowedHosts` keeps `localhost` and `127.0.0.1`, as a recorded deviation** from FR17's "restricted to the service host": DNS rebinding needs a hostile name that resolves to the server, and these two match only requests addressed to the machine itself; they let `streamlit run` work locally with the same file the image ships. Slice 4's test: the list minus `{"localhost", "127.0.0.1"}` equals the Cloud Run service's hosts. | deviation from FR17, owner-accepted |
| S10 | **The O/E 95% CI is a shaded bar per plotted week**, not a connected band: an area mark would bridge weeks without an O/E value and imply a CI that was never computed. | deviation from FR13's "shaded band" wording |
| S11 | **Entrypoint `scripts/dashboard_app.py`** (two code lines calling `readmission_risk.dashboard.app.main()`), not `streamlit run src/readmission_risk/dashboard/app.py`: running a file inside the package puts the package directory first on `sys.path`, making `errors`, `charts`, … importable as top-level modules (the class of CLAUDE.md's `./mlflow/` shadowing incident). `app.py` still holds the app; slice 4's Dockerfile runs the script. | deviation from FR10's "entrypoint `app.py`" |
| S16 | **Dashboard queries fail within about 30 seconds.** Slice 2's `queries.run` inherits google-cloud-bigquery 3.46.1's default retries (`DEFAULT_RETRY` 10 min, `DEFAULT_JOB_RETRY` 40 min), so an FR16 panel could take that long. The app uses a new `queries.run_bounded`: submission `retry=DEFAULT_RETRY.with_timeout(10)` and `timeout=10`, `job_retry=None` (a failed job is not re-run; the visitor's next interaction retries), `job.result(retry=DEFAULT_RETRY.with_timeout(20), timeout=20)`. `queries.run`/`run_job`/`dry_run` (the live checks) are unchanged. | bounds FR16 in time |
| S17 | **A separate dashboard lockfile now**, so the production jobs (`score.yml`, the four `monitor.yml` jobs, the weekly tick M3 rests on) never install Streamlit: `requirements-dashboard.in` = `-c requirements.txt` + `streamlit==1.66.1` + `altair==6.3.0`, compiled by a new `make lock-dashboard` (Python 3.14, macOS, the `lock-main` recipe shape) into hash-locked `requirements-dashboard.txt`; the constraint keeps every shared pin equal to `requirements.txt`'s (measured in a scratch compile: pandas 2.3.3, numpy 2.4.4, pyarrow 25.0.1, starlette 1.6.0, uvicorn 0.53.0, protobuf 6.33.6, pillow 12.3.0, …; watchdog absent). watchdog goes into a new hand-maintained `requirements-dashboard-linux-extras.txt`, NOT the production `requirements-linux-extras.txt`. Installed by `make setup` and CI's `lint-and-test` only. `requirements.in`/`.txt` and `requirements-linux-extras.txt` are not touched. Slice 4 decides whether FR20's image lockfile is this pair recompiled inside `python:3.14-slim` (plus google-cloud-bigquery) or a separate file. | brings FR20's file forward (macOS compile) |
| S18 | **A per-run circuit breaker bounds the page during an outage.** S16 bounds each query (~30 s), but a cold visit runs up to four in sequence (monitoring, scoring runs, published dates, call sheet), ~90-120 s of spinner against V2's 60 s. Within one script run, once a fetch is classified `unreachable`, every later fetch in that run returns `unreachable` without querying (logged `"dashboard query %s skipped (BigQuery unreachable earlier in this run)"` at WARNING, no exc_info) and shows its own unreachable panel. `quota` and `bytes_cap` do not trip it (other queries may still work). The next interaction starts a fresh run (fresh breaker). Worst case ≈ one query's bound. | bounds FR16 at page level |
| S12 | **The run history is filtered to the simulated clock**, like the call sheet (D1/D5): scoring runs with `run_date` after the clock or a non-NULL `gold_fingerprint` other than `SERVING_GOLD_FINGERPRINT` are hidden (the 2025-06-30 L-post dispatch, the stray v1 2025-07-31); monitoring ticks with `sim_date` after the clock or a non-NULL `model_bundle_id` other than the monitored bundle are hidden. Rows with a NULL `run_date`/`sim_date` (refusals before the date was known) stay. With no clock the app shows `NO_CLOCK_YET`/`NO_CLOCK_UNKNOWN` instead of the tables (§9 `_history` branches 2-3); `visible_*_runs(clock=None)`, which hides every dated published run / succeeded tick (as refined below), is a defensive rule for direct callers, tested in V-R. A caption counts the hidden rows. The heartbeat's lookups use the unfiltered rows. **Refined by the owner in the pre-commit review (2026-11-01):** the clock bound applies only to rows that can carry future content, succeeded ticks and published scoring runs; failed or refused ticks and unpublished runs always show (a failed weekly attempt is dated clock + 7 days because the clock moves only on success, and it is exactly what an operator needs). The other-gold and other-bundle rules are unchanged. | applies V7/D5 to FR14 |
| S13 | **"Late" is week-based**, superseding S8: a delayed Wednesday slot has run on Thursday 02:27 UTC (CLAUDE.md, slice 7), and `weekly.py` counts any scheduled run of the same ISO week, so no fixed day count bounds normal operation. `late_at` = Friday 00:00 UTC of the ISO week AFTER the anchor's ISO week (UTC): the next week's Monday-Wednesday slots plus a day for delayed slots have all passed without a scheduled weekly success. Anchor Monday 00:17 → late after 11 d 23 h 43 min; anchor Thursday 02:27 → 7 d 21 h 33 min. Always before `stale_at` (anchor + 14 d), because the anchor is at most 11 d 23 h 59 min before that Friday. | replaces FR11's "warn after 8 days" |
| S14 | **Muted weeks on the O/E chart** (`MUTED_VERDICTS` = `insufficient_data`, `incomplete_cohort`): a muted week WITH an O/E is a hollow grey point (and grey CI bar) with its verdict in the tooltip and the table, no per-point text. That is the common case for BOTH verdicts: `rule.py:79-81` computes the ratio and CI before `observed < min_positives_floor` yields `insufficient_data`, and `cohort.py:149-153` computes them for incomplete cohorts. A week WITHOUT an O/E (`insufficient_data` with `n_matured == 0`, `cohort.py:154-155`, or an incomplete cohort with nothing to compute) is a hollow grey marker on the chart's bottom edge (a "rug"), so the week is visible. One caption counts both kinds. Only alerts carry a text label. | deviation from FR13's per-point "text label" |
| S15 | **Server-level behaviour is verified in slice 4, not here** (AppTest bypasses Streamlit's server): `allowedHosts`, XSRF/CORS, the redacted error page, `/_stcore/health`, the kept starlette/uvicorn pins under the real server, and one look at the chart and page in light and dark mode. They are slice-4 verification items (Out-of-scope follow-ups). | none (S2 stands) |

Elephant resolutions (not owner decisions; flagged):
- **E3 (the shared stale rule cannot live in `monitoring/config.py` or `ledger.py`):** both import `readmission_risk.bundles`
  (`config.py:13-14`; `ledger.py:13` imports `.config`), which FR18 bans. The rule, `WEEKLY_CRON`, `_PERIODS`, `period_of` and
  `cadence_label`/`CADENCE_LABELS` (FR13's burn-in labels "as in the ledger") move into a new stdlib-only `monitoring/freshness.py`;
  `config.py` and `ledger.py` import them from there, so every existing import path keeps working.
- **E4 (`server.allowedHosts`):** Streamlit 1.66.1's option is an allow-list of WebSocket Host headers (empty = any host). The
  config names the deterministic Cloud Run host `dashboard-271458030145.us-central1.run.app` (service `dashboard`, project number
  271458030145, already public in `.github/workflows/*.yml`), plus the dev hosts of S9. Cloud Run also serves a legacy
  `dashboard-<hash>-uc.a.run.app` host, unknown until the service exists; slice 4 must add it (or prove it is unused), with the
  S9 test. Until then a visitor on the legacy host gets no WebSocket.
- **E5 (provenance the app cannot know offline):** the image's git commit comes from env var `READMISSION_RISK_GIT_COMMIT`
  (slice 4's Dockerfile sets it). The model card does not exist until slice 5, and v3 may merge into `main` at slice 4, so a
  link would be a public 404: until then `chrome.MODEL_CARD_URL = None` and the strip's third line reads "**Model card:** in
  preparation (published with v3.0)." with no link. Slice 5 sets the URL; a test fails if `MODEL_CARD_URL` is set while
  `docs/model-card.md` does not exist, or is None while it does.
- **E7 (isolation of rendering faults):** besides the four query classes, every rendered part (the notice + honesty strip, the
  heartbeat computation and its line, each of the three tabs, the footer) runs inside `guarded(...)`; an exception there (a
  presentation bug, not a query failure) shows the fixed text "<Part> could not be displayed. The other views still work." and is
  logged. This is not a fifth failure class: it names no cause. If the heartbeat computation fails, the tabs receive
  `Heartbeat.unavailable()` (state `unknown`, every field None).
- **E8:** superseded by owner decision S11 (`scripts/` holds no module named like a package the app imports).
- **E10 (a small behaviour change in the ledger badge):** `last_tick_badge` raised `TypeError` (from `tz_convert`) for a naive
  `started_at` and accepted ISO strings through `pd.Timestamp`; through `freshness.newest_scheduled_success` it raises
  `ValueError` for both. Neither occurs in real inputs (the store builds tz-aware `pd.Timestamp`s from REST rows); recorded,
  tested (V-F), accepted.
- **E9 (local runs and least privilege):** the app always uses `gcp.bigquery_client()` with default credentials: on Cloud Run
  the runtime SA `dashboard-runtime`; on a laptop (`streamlit run`, not part of this slice) the operator's own ADC, which
  can read more than the authorized views. So a local run does NOT exercise the view-only scoping; the slice-2 live checks
  (which always impersonate `dashboard-runtime`) remain the proof. A future `make` target for local runs must impersonate
  `dashboard-runtime` (named follow-up).

## Why

v3's reviewer gets ~60 s after a cold click (PRD V2): one screen must say what this is, that it ran recently, and show a ranked call
sheet; the owner needs the Monday check (tick ran, tests passed, O/E in band) in the same app. Slice 2 proved the data layer (three
authorized views, `dashboard-runtime`, the fixed queries, live). This slice builds the app on that layer and proves it offline with
Streamlit's `AppTest` and a fake BigQuery, so slice 4 only has to containerize and deploy it.

## Scope

**In:**
1. `scripts/dashboard_app.py` (entrypoint, S11); `src/readmission_risk/dashboard/app.py` (Streamlit layer) and pure helpers
   `dashboard/{errors,heartbeat,present,charts,chrome}.py`.
2. `.streamlit/config.toml` (repo root; FR17), asserted by a test.
3. Refactor: `monitoring/freshness.py` (E3); `ledger.py`'s rendered output byte-identical.
4. Refactor: `reporting/palette.py`, the dependency-free colour tokens (FR19); the README figures regenerate byte-identically.
5. Pins (S17): `requirements-dashboard.in`, `requirements-dashboard.txt` (via the new `make lock-dashboard`),
   `requirements-dashboard-linux-extras.txt`; `tests/test_lockfiles.py`, `Makefile` (`setup`, `lock-dashboard`), CI's
   `lint-and-test` install step and pip cache key, `docs/setup.md`'s install lines.
6. Tests (§Verification); the install lines of `README.md` and `docs/setup.md` (S17); CLAUDE.md (v3 architecture: a slice-3 bullet; Build & test: test count + the dashboard tests); this note.

**Out (named follow-ups):** Dockerfile, FR20's in-container (Linux) compile of the image lockfile, Cloud Run, `deploy.yml`, the live smoke, the legacy-host
entry (E4) and the documented manual accessibility pass on the deployed app (slice 4); the model card and its subgroup table
(slice 5); a `make` target that runs the app against live BigQuery; Playwright (S2); per-row contributions; materializing the
reason rate; deleting the 44 v1 rows (D1); any README or `docs/setup.md` change beyond S17's install lines (the README tour
is FR26, slice 5).

## Surfaces touched

| Layer | File | Change |
|---|---|---|
| deps | `requirements-dashboard.in` | new: a comment naming S17, then `-c requirements.txt`, `streamlit==1.66.1`, `altair==6.3.0` |
| deps | `requirements-dashboard.txt` | new, by `make lock-dashboard` (header command `pip-compile --generate-hashes --no-index --output-file=requirements-dashboard.txt requirements-dashboard.in`, as measured) |
| make | `Makefile` | `lock-dashboard:` (`## Recompile requirements-dashboard.txt under Python 3.14 on macOS, keeping existing pins`; the `lock-main` body with `pip-compile --generate-hashes --output-file=requirements-dashboard.txt requirements-dashboard.in` and an echo naming `requirements-dashboard-linux-extras.txt`), added to `.PHONY`; `lock-main`'s closing echo gains the sentence "Then run make lock-dashboard: requirements-dashboard.txt is constrained by requirements.txt."; `setup:` gains `$(BIN)/pip install --require-hashes -r requirements-dashboard.txt -r requirements-dashboard-linux-extras.txt` between the main and dev installs, and its `##` text says "four" sets |
| ci | `.github/workflows/ci.yml` (`lint-and-test` only) | the install step gains the same line between the main and dev installs; `cache-dependency-path` gains both new files |
| tests | `tests/test_tooling.py` | `test_dev_and_main_lockfiles_agree_on_shared_pins` (line 307) is extended to every co-installed pair: main/dev, main/dashboard, dashboard/dev (the four sets share one `.venv`; dev is not `-c` constrained, so D8 does not cover it; today's dashboard/dev overlap, click 8.5.0 and packaging 26.3, agrees); `EXPECTED_TARGETS` + `lock-dashboard`; a dry-run test like the lock-main one (`.venv-lock314/bin/pip-compile --generate-hashes --output-file=requirements-dashboard.txt requirements-dashboard.in`) |
| tests | `tests/test_lockfiles.py` | `LOCK_PAIRS` + `LockPair("requirements-dashboard.in", "requirements-dashboard.txt", DEV_PYTHON, "pip-compile --generate-hashes --no-index --output-file=requirements-dashboard.txt requirements-dashboard.in", "make lock-dashboard")`; `parse_in_file` accepts exactly the directive `-c requirements.txt` (any other `-` line still raises) and returns the constraint files separately (`parse_in_file(text) -> tuple[list[Requirement], list[str]]`, callers updated); `drift_problems(in_text, lock_text, *, pair, constraints: Mapping[str, str] | None = None)` (constraints = constraint file name → its lockfile text; `test_lock_pair_has_no_drift` passes `{name: (ROOT / name).read_text() for name in the .in's constraint files}`, the self-tests pass synthetic texts) skips parents starting with `-c ` in D6, and adds D8: for each constraint file named in the .in, if it is missing from `constraints` → `f"D8 {file}: constraint file not provided"`; else for every pin also pinned there with another version → `f"D8 {name}: pinned {v} but {file} pins {v_c}"` (canonical names, sorted); the extras test is parametrized over `(extras file, its lockfile)`: `(requirements-linux-extras.txt, requirements.txt)` and `(requirements-dashboard-linux-extras.txt, requirements-dashboard.txt)`; new self-tests for the `-c` parse, a D6 `-c` parent and a D8 mismatch |
| docs | `docs/setup.md` | the install block (lines 16-18) gains `pip install --require-hashes -r requirements-dashboard.txt -r requirements-dashboard-linux-extras.txt` between the main and dev lines; line 14 "three separate invocations" → "four"; line 41 "runs the three installs above" → "four"; the Make-targets table gains a `make lock-dashboard` row next to `lock-main`/`lock-dev` ("Recompiles requirements-dashboard.txt under Python 3.14 on macOS; run after make lock-main"); one sentence on S17 |
| docs | `README.md` | the quick start (lines 177-181) gains the same install line between the main and dev lines (otherwise a fresh clone's `pytest` fails collecting `tests/dashboard`); nothing else in the README |
| docs | `CLAUDE.md` | Build & test: "Install as three separate invocations" → four, naming the dashboard set and S17; the "Make targets" list gains `lock-dashboard` (with the slice-3 bullet) |
| deps | `requirements-dashboard-linux-extras.txt` | new: a header like `requirements-linux-extras.txt`'s naming streamlit and S17, then `watchdog==6.0.0 ; platform_system == "Linux" \` with `--hash=sha256:…` for every file PyPI lists for 6.0.0 (its wheels AND its sdist `watchdog-6.0.0.tar.gz`; pip on Linux picks `py3-none-manylinux2014_<arch>`). Its own header's hash line: "# Hashes: sha256 of every file PyPI lists for watchdog 6.0.0 (wheels and the sdist); watchdog is required by streamlit off macOS (platform_system != "Darwin"), which pip-compile on macOS cannot see." The production `requirements-linux-extras.txt` (header included) is NOT changed. |
| monitoring | `monitoring/freshness.py` | new (§Interfaces 1) |
| monitoring | `monitoring/config.py` | lines 19 (`WEEKLY_CRON`), 26 (`_PERIODS`) and 135-138 (`period_of`) removed; `from .freshness import _PERIODS, WEEKLY_CRON, period_of  # noqa: F401 -- re-exported; the dashboard imports freshness directly (FR18)` |
| monitoring | `monitoring/ledger.py` | lines 29 (`CADENCE_LABELS`) and 33-37 (`cadence_label`) removed and imported from `.freshness` (`cadence_label` is still used by `render_ledger`; `CADENCE_LABELS` re-exported with `# noqa: F401`); line 13 becomes `from .config import MonitoringConfig`; `last_tick_badge`'s inline rule (lines 127-134) replaced (§Interfaces 1) |
| reporting | `reporting/palette.py` | new: `Theme`, `_MARKERS`, `THEMES` moved verbatim from `style.py:49-77` (§Interfaces 2) |
| reporting | `reporting/style.py` | lines 49-77 removed; line 10 `from dataclasses import dataclass` removed (only `Theme` used it); `from .palette import Theme` (used by `house_style`/`new_axes` annotations); no `THEMES` re-export |
| reporting | `reporting/reliability.py:14`, `reporting/assets.py:28` | `Theme` / `THEMES` imported from `.palette` (the other names still from `.style`) |
| dashboard | `dashboard/__init__.py` | docstring: slice 3 adds the app; entrypoint `scripts/dashboard_app.py` |
| dashboard | `dashboard/queries.py` | + `run_bounded` (S16): `def run_bounded(client, query, *, submit_deadline: float = SUBMIT_DEADLINE_S, result_timeout: float = RESULT_TIMEOUT_S) -> list[dict]` with `SUBMIT_DEADLINE_S = 10.0`, `RESULT_TIMEOUT_S = 20.0`; body: `from google.cloud.bigquery.retry import DEFAULT_RETRY`; `job = client.query(query.sql, job_config=query_config(**config_kwargs(query)), retry=DEFAULT_RETRY.with_timeout(submit_deadline), job_retry=None, timeout=submit_deadline)` (the per-request HTTP timeout: `Client.query`'s default is None, and a retry timeout does not interrupt a hung request; `with_timeout`, not the deprecated `with_deadline`, google-api-core 2.42.0); `return [dict(row.items()) for row in job.result(retry=DEFAULT_RETRY.with_timeout(result_timeout), timeout=result_timeout)]` |
| tests | `tests/warehouse/fake_bigquery.py` | `FakeJob` gains `allow_timeout: bool = False` and `result_kwargs: dict | None = None`; `result()` records its kwargs and asserts `"timeout" not in kwargs` only when `allow_timeout` is False (the scorer's rule unchanged); `FakeBigQuery` gains `query_kwargs: list[dict] = field(default_factory=list)` and `query()` appends its extra kwargs (`retry`, `job_retry`, …) there, one dict per call, in call order. The `("query", sql, job_config, job_id)` call tuple is UNCHANGED (existing tests unpack exactly four elements: `tests/dashboard/test_queries.py:157`, `tests/warehouse/test_monitoring_sql.py:49`, `test_data_tests.py:106`, `test_staging.py:76`, `test_scoring_runs.py:37`) |
| dashboard | `dashboard/{app,errors,heartbeat,present,charts,chrome}.py` | new |
| scripts | `scripts/dashboard_app.py` | new, thin (§Interfaces 9) |
| config | `.streamlit/config.toml` | new (§Interfaces 8) |
| config | `pyproject.toml` | `[tool.pytest.ini_options] markers` gains `"no_fake_world: test_app.py tests that must keep the refusing gcp factories"` |
| tests | `tests/reporting/test_style.py:13-16`, `tests/reporting/test_reliability.py:13` | import `THEMES` from `readmission_risk.reporting.palette` |
| tests | `tests/dashboard/test_import_boundary.py` | extended (V-B) |
| tests | `tests/dashboard/{test_errors,test_heartbeat,test_present,test_charts,test_chrome,test_app,test_streamlit_config}.py`, `tests/dashboard/fake_world.py` | new |
| tests | `tests/monitoring/test_freshness.py`, `tests/reporting/test_palette.py` | new |
| docs | `CLAUDE.md`, this note | |

Not touched: `requirements.in`, `requirements.txt`, `requirements-linux-extras.txt` and every production workflow (S17); the
views; in `dashboard/queries.py` only `run_bounded` is added (its SQL, `run`, `run_job`, `dry_run`, `PublishedDate` and
`config_kwargs` stay as they are),
`warehouse/*`, Terraform, `scripts/publish_ledger.py` (stdlib, never imports the package; it does not compute the stale rule),
`scripts/render_ledger.py`, `tests/dashboard/conftest.py` (the full E2 trap stays autouse-armed for every dashboard test, S5).

## Interfaces

### 1. `src/readmission_risk/monitoring/freshness.py` (standard library only, no package import)

```python
"""The schedule facts shared by the run ledger and the dashboard heartbeat (PRD FR11): the weekly cron, its period, the cadence
labels and THE "stale after" rule. Standard library only and no package import, so the dashboard can use it (FR18)."""
WEEKLY_CRON = "17 * * * 1-3"              # moved verbatim from config.py:19, with its comment
_PERIODS = {WEEKLY_CRON: timedelta(days=7)}
def period_of(cron: str) -> timedelta      # moved verbatim (ValueError for an unknown cron)
CADENCE_LABELS = {...}                     # moved verbatim from ledger.py:29
def cadence_label(cadence, trigger) -> str # moved verbatim from ledger.py:33-37

def newest_scheduled_success(rows: Iterable[Mapping]) -> Mapping | None:
    """The row with the max (started_at, monitoring_run_id) among rows with trigger == "schedule", cadence == "weekly" and
    status == "succeeded"; None without one. Burn-in rows (cadence "burn_in", even with trigger "schedule"), dispatches,
    catch-ups and failed attempts never qualify. Every qualifying started_at must be a datetime with tzinfo (pd.Timestamp
    qualifies); ValueError otherwise."""

def stale_after(rows: Iterable[Mapping], *, cron: str) -> datetime | None:
    """newest_scheduled_success(rows)["started_at"].astimezone(UTC) + 2 * period_of(cron); None without such a row."""
```
`ledger.last_tick_badge`'s `else:` branch (cron not None) becomes:
```python
stale = stale_after(ordered.to_dict("records"), cron=cron)
if stale is None:
    message += " · schedule not started"
else:
    message += f" · stale after {_wall(stale)}"
```
`_wall(stale)` formats exactly as before. Equivalence: the old code took the first row of `ordered` (sorted by
`(pd.to_datetime(started_at, utc=True), monitoring_run_id)` descending) after the same three filters = the max of the same key.

### 2. `src/readmission_risk/reporting/palette.py` (standard library only)

```python
"""The house colour tokens (Okabe-Ito series on GitHub's light/dark ink and grey), shared by the matplotlib figures and the
dashboard's Altair charts (PRD FR19). Standard library only."""
@dataclass(frozen=True)
class Theme: ...                 # moved verbatim (name, ink, grey, series, markers, preview_background)
_MARKERS = {...}; THEMES = {...} # moved verbatim; the 10 hex values unchanged
def chart_colors(theme: Theme) -> dict[str, str]:
    """{"ink": theme.ink, "muted": theme.grey, "ok": theme.series["logistic_regression"],
    "alert": theme.series["xgboost_calibrated"], "background": theme.preview_background}.
    light: #1f2328 #6e7781 #0072B2 #D55E00 #ffffff; dark: #e6edf3 #8b949e #56B4E9 #E69F00 #0d1117."""
```

### 3. `src/readmission_risk/dashboard/errors.py`

```python
FailureClass = Literal["unreachable", "bytes_cap", "quota"]
PanelKind = Literal["unreachable", "bytes_cap", "quota", "empty"]
BYTES_CAP_REASONS = frozenset({"bytesBilledLimitExceeded"})
QUOTA_REASONS = frozenset({"quotaExceeded", "rateLimitExceeded"})  # == warehouse/gold.py:178 _QUOTA_REASONS (a test)

def exception_chain(exc: BaseException) -> list[BaseException]:
    """exc, then breadth-first each element's __cause__, __context__ and (google.api_core RetryError) .cause; each object at
    most once (by id); at most 20 elements."""
def classify(exc: BaseException) -> FailureClass:
    """reasons = every e.errors[i]["reason"] for the dict entries of each element's .errors list (non-list/absent ignored).
    "bytes_cap" if reasons & BYTES_CAP_REASONS; else "quota" if reasons & QUOTA_REASONS or any element is an instance of
    google.api_core.exceptions.TooManyRequests; else "unreachable" (S4). google.api_core is imported inside the function."""

PANEL = {
  "unreachable": "{what} could not be read from BigQuery right now. {still}",
  "bytes_cap":   "{what} was not loaded: the query would scan more than the {cap} MiB cost cap, so BigQuery refused it. {still}",
  "quota":       "{what} was not loaded: a BigQuery quota or rate limit was reached. Try again later. {still}",
  "empty":       "{what}: nothing to show yet. {still}",
}
def panel_text(kind: PanelKind, *, what: str, still: str) -> str   # cap = MAX_BYTES_BILLED // 2**20 (= 100); never the exception text
RENDER_FAILED = "{view} could not be displayed. The other views still work."   # E7
```

**Panel strings** (`what` / `still`), used by `app.py` verbatim:

| Where | what | still |
|---|---|---|
| heartbeat (monitoring query failed) | "The heartbeat" | "The call sheet may still work." |
| call sheet, dates query failed / empty | "The list of published run dates" | "The monitoring and run-history tabs may still work." |
| call sheet, rows query failed / empty | "The call sheet for <YYYY-MM-DD>" | "Other run dates and tabs may still work." |
| monitoring tab, query failed / no points | "The monitoring record" | "The call sheet may still work." |
| run history, scoring-runs failed / empty | "The scoring-run history" | "The monitoring-run table may still work." |
| run history, monitoring failed / empty | "The monitoring-run history" | "The scoring-run table may still work." |

Failure panels use `st.warning`, empty panels `st.info`.

### 4. `src/readmission_risk/dashboard/heartbeat.py` (stdlib + `monitoring.freshness` + `dashboard.present`, which imports pandas)

```python
def late_after(anchor: datetime) -> datetime:
    """S13: Friday 00:00 UTC of the ISO week after anchor's ISO week (anchor converted to UTC first):
    monday = (anchor_utc - timedelta(days=anchor_utc.weekday())).replace(hour=0, minute=0, second=0, microsecond=0);
    return monday + timedelta(days=11)."""
HeartbeatState = Literal["ok", "late", "stale", "not_started", "no_ticks", "unknown"]

@dataclass(frozen=True)
class Heartbeat:
    state: HeartbeatState
    sim_date: date | None                  # simulated_clock()[0]
    monitored_bundle: str | None           # simulated_clock()[1]
    last_tick_started_at: datetime | None  # newest monitoring row of ANY status/cadence, by (started_at, monitoring_run_id)
    last_tick_status: str | None
    history_tests_passed: bool | None      # of the NEWEST tick row (any status, the same row as last_tick_*): a succeeded
                                           # tick always passed (monitoring/tick.py:218-220 fails the tick otherwise), so only
                                           # the newest attempt can show a failure (round-3 critic 1)
    predictions_tests_passed: bool | None  # of the scoring run whose scoring_run_id equals the NEWEST tick's, if present in
                                           # scoring_rows (recent_scoring_runs, LIMIT 50); None if absent, if scoring_rows is
                                           # None, or if that tick's scoring_run_id is None
    anchor: datetime | None                # freshness.newest_scheduled_success(rows)["started_at"] in UTC
    late_at: datetime | None               # late_after(anchor) (S13)
    stale_at: datetime | None              # freshness.stale_after(rows, cron=cron)

    @classmethod
    def unavailable(cls) -> Heartbeat: ...  # state "unknown", every other field None (E7 fallback; also heartbeat(None, ...))

def wall_now() -> datetime: return datetime.now(UTC)   # the ONLY wall-clock read in the dashboard (S6); tests patch it

def simulated_clock(rows: Iterable[Mapping]) -> tuple[date, str, Mapping] | None:
    """D5 in Python (queries.py:83-91's ticks/monitored_bundle/clock CTEs): candidates = rows with status == "succeeded" and
    sim_date and model_bundle_id not None; bundle = that of the candidate with the max (started_at, monitoring_run_id); clock =
    max sim_date among the bundle's candidates; returns (clock, bundle, the bundle's candidate with that sim_date and the max
    (started_at, monitoring_run_id)), or None."""

def heartbeat(monitoring_rows: Sequence[Mapping] | None, scoring_rows: Sequence[Mapping] | None, *, now: datetime,
              cron: str = WEEKLY_CRON) -> Heartbeat:
    """now must be tz-aware (ValueError). state: unknown if monitoring_rows is None (the query failed; every other field None);
    no_ticks if empty; else not_started if anchor is None; else stale if now >= stale_at; else late if now >= late_at; else ok."""

def heartbeat_line(hb: Heartbeat) -> str:
    """Markdown, status as icon + bold word (never colour alone), then " · "-separated facts:
       head:  ok "✅ **Fresh**" | late "⚠️ **Late**" | stale f"⛔ **Stale since {stale_at:%Y-%m-%d}**" |
              not_started "⏸️ **Schedule not started**" | no_ticks "⏸️ **No monitoring tick yet**" |
              unknown "❔ **Heartbeat unavailable**"
       facts (all states except no_ticks/unknown, in this order):
              f"simulated date {sim_date:%Y-%m-%d}" (or "simulated date unknown" if None)
              f"last tick ran {last_tick_started_at:%Y-%m-%d %H:%M} UTC ({status_word(last_tick_status)})"
              "data tests passed" if history and predictions flags are both True; "data tests FAILED" if either is False;
              else "data tests unknown" (these describe the newest tick, whose status is shown in the previous fact; the
              freshness state describes the schedule, so "✅ Fresh · … (failed) · data tests FAILED" is a valid line)
              f"bundle `{short_id(monitored_bundle)}`" (short_id: the first 12 characters + "…" iff the value fullmatches
              [0-9a-f]{64}, else "unknown")
       tail (appended after the facts with " · "):
              late  f"no scheduled weekly tick since {anchor:%Y-%m-%d %H:%M} UTC; stale from {stale_at:%Y-%m-%d}"
              stale f"no scheduled weekly tick since {anchor:%Y-%m-%d %H:%M} UTC"
    Example ok: "✅ **Fresh** · simulated date 2023-10-30 · last tick ran 2026-10-26 04:44 UTC (succeeded) · data tests passed ·
    bundle `569fdab6c0de…`". Datetimes are converted to UTC before formatting."""
```
The cron is `WEEKLY_CRON` (the app has no `monitor.yml`; if the schedule is removed the heartbeat turns stale, which is true).

**One clock, made visible (round-1 critic 10, round-2 critic 9):** the heartbeat's simulated date is `simulated_clock(monitoring
rows)`; the call sheet's "Simulated today" caption comes from SQL (`PublishedDate.clock_sim_date`). They implement the same rule over
the same view, with two differences: separate cache entries (filled at different times), and `monitoring_history`'s `LIMIT 1000`
(the SQL clock CTE has no limit). The limit only matters if more than 1,000 monitoring rows were written since `OPS_EPOCH` AFTER
the monitored bundle's newest succeeded tick, which cannot happen while ticks keep succeeding (about 1-3 rows per week today).
If both values are available and `dates[0].clock_sim_date != hb.sim_date`, the call-sheet tab shows
`st.caption(f"The date list and the heartbeat disagree on the simulated date ({a:%Y-%m-%d} vs {b:%Y-%m-%d}); they were read at
different times and should agree within 3 hours.")`. Offline, the Python rule is tested on literal hand-written cases that
mirror the SQL's semantics (V-H), never against itself; a live comparison of `simulated_clock(monitoring_history rows)` with
`published_dates()[0].clock_sim_date` is a named slice-4 follow-up (extend `make dashboard-query-check`).

### 5. `src/readmission_risk/dashboard/present.py` (pandas; no Streamlit)

```python
GITHUB_RUN_PREFIX = "https://github.com/fma00/synthea-readmission-risk/actions/runs/"
_RUN_URL_RE = re.compile(re.escape(GITHUB_RUN_PREFIX) + r"\d+(/attempts/\d+)?")
STATUS_TEXT = {"succeeded": "✅ succeeded", "failed": "❌ failed", "data_tests_failed": "⚠️ data tests failed", "refused": "⛔ refused"}
VERDICT_TEXT = {"ok": "✅ ok", "alert": "🚨 alert", "insufficient_data": "⚪ insufficient data", "incomplete_cohort": "⚪ incomplete cohort"}
def freeze_rows(rows: Iterable[Mapping]) -> tuple[MappingProxyType, ...]   # S5: tuple(MappingProxyType(dict(r)) for r in rows)
def short_id(value) -> str           # value[:12] + "…" iff isinstance(value, str) and it fullmatches [0-9a-f]{64}, else "unknown"
def status_word(status) -> str       # the key itself if status in STATUS_TEXT, "other" otherwise, "-" for None (safe in markdown)
def status_text(status) -> str       # STATUS_TEXT.get(s, f"❔ {s}"); None -> "-"   (tables only, never markdown)
def verdict_text(verdict) -> str     # VERDICT_TEXT.get(v, f"❔ {v}"); None -> "-"  (tables only)
def run_link(url) -> str | None      # url iff isinstance str and _RUN_URL_RE.fullmatch(url), else None
def rate_text(n, rate) -> str:
    """In this order, whatever the other argument: n None -> "no history"; n < REASON_RATE_MIN_N -> f"withheld (n = {n} <
    {REASON_RATE_MIN_N})" (the app withholds too, not only the view); rate None -> f"rate unavailable (n = {n:,})" (AVG over a
    NULL-only is_readmitted, which gold.json allows); else f"{rate:.1%} of {n:,}". A withheld or missing rate is never rendered
    as a number."""
def reason_text(code, description) -> str:
    """code and description -> f"{description} ({code})"; code only -> f"(no description) ({code})"; description only ->
    f"{description} (no code)"; neither -> "(no reason recorded)"."""

CALL_SHEET_DISPLAY = ("Rank", "Top-N", "Discharged (UTC)", "Admission reason", "Risk score (%)", "Age (years)", "Length of stay (days)",
    "Prior inpatient stays", "Prior emergency visits", "Active conditions", "Reason's historical readmission rate",
    "Patient ID (synthetic)")
def call_sheet_frame(rows: Sequence[Mapping]) -> pd.DataFrame:
    """Columns exactly CALL_SHEET_DISPLAY, sorted by rank, index 0..n-1. Rank int64. Discharged = discharge_time (datetime64[ns,
    UTC]). Sources: Rank <- rank; Top-N <- is_top_n ("✅ call this week" if True, else "-"; V5's Top-N flag, besides the
    table split); Discharged <- discharge_time; Age (years) <- age_at_index_years; Length of stay (days) <-
    length_of_stay_days; Prior inpatient stays <- prior_inpatient_count; Prior emergency visits <- prior_emergency_count;
    Active conditions <- active_condition_count; Patient ID (synthetic) <- patient_id.
    Admission reason = reason_text(admission_reason_code, admission_reason_description). Risk score (%) = risk_score * 100 (float64). Age, LOS: float64 with NaN for NULL. The three counts: pandas "Int64"
    with <NA> for NULL. Rate column: rate_text(reason_history_n, reason_history_rate) (object/str). Patient ID: patient_id (str).
    NULL cells render as Streamlit's empty cell. Never a FORBIDDEN_OUTPUT_COLUMNS name, never the raw flag/score columns. Builds
    a new frame; rows are not mutated. Empty input -> an empty frame with these columns AND these dtypes (one construction
    path: the frame is built column by column with explicit dtypes, whatever the row count)."""
def split_top_n(rows) -> tuple[list[Mapping], list[Mapping]]   # (rows with is_top_n is True, the rest), each sorted by rank
def sheet_warnings(rows, published: PublishedDate) -> list[str]:
    """In this order (ints only; no BigQuery string enters the text), each present iff its trigger holds:
    len(rows) != published.n_predictions -> f"{count_text(len(rows), 'row is', 'rows are')} shown, but the run recorded
        {published.n_predictions}."
    k > 0, k = rows with scoring_run_id != published.scoring_run_id -> f"{count_text(k, 'row comes', 'rows come')} from another
        scoring run than the listed one."
    t != m, t = rows with is_top_n True, m = min(published.top_n, len(rows)) -> f"{count_text(t, 'row is', 'rows are')} flagged
        Top-N; the run's Top-N is {m}." """

def monitoring_points(rows: Sequence[Mapping], *, bundle: str | None) -> pd.DataFrame:
    """Rows with status "succeeded", model_bundle_id == bundle, verdict and sim_date not None; per sim_date the one with the max
    (started_at, monitoring_run_id); sorted by sim_date ascending; index 0..n-1. Columns, in order: sim_date (datetime64[ns, UTC],
    midnight UTC: a naive column serialises without an offset and the browser reads it as LOCAL time, so at UTC+1 the UTC time
    unit would show the previous day; measured by the round-8 critic), sim_week (str, f"{sim_date:%Y-%m-%d}"), verdict, verdict_label (verdict_text), oe_ratio, oe_ci_low, oe_ci_high, band_low, band_high (float64, NaN for
    NULL), observed (Int64), expected (float64), min_positives_floor (Int64), cadence_label (freshness.cadence_label(cadence,
    trigger)), plotted (bool: oe_ratio not NaN). bundle None or no row -> an empty frame with these columns."""
def monitoring_table(points: pd.DataFrame) -> pd.DataFrame
    # columns in this order: "Simulated week" (YYYY-MM-DD), "Verdict" (verdict_label), "O/E [95% CI]", "Observed / expected",
    # "Positives floor" (int or "-"), "Cadence"; newest first; all
    # str. The two value columns follow ledger.py:84-88 exactly: "O/E [95% CI]" is "-" iff oe_ratio is NULL, else
    # f"{num(r, 3)} [{num(lo, 3)}, {num(hi, 3)}]" where num(x, d) is "-" for NULL (so a NULL bound alone shows "-" in place);
    # "Observed / expected" is "-" iff observed is NULL, else f"{observed} / {num(expected, 1)}"
def count_text(n: int, singular: str, plural: str) -> str   # f"{n} {singular}" if n == 1 else f"{n} {plural}"
def visible_scoring_runs(rows, *, clock: date | None) -> tuple[list[Mapping], int]:
    """S12 (as refined): (kept rows, hidden count). Hidden: published is True and run_date not None and (clock is None or
    run_date > clock); or gold_fingerprint not None and != SERVING_GOLD_FINGERPRINT. Unpublished runs (a failed attempt is
    dated clock + 7) always show. Order preserved."""
def visible_monitoring_runs(rows, *, clock: date | None, bundle: str | None) -> tuple[list[Mapping], int]:
    """S12 (as refined): hidden: status == "succeeded" and sim_date not None and (clock is None or sim_date > clock); or
    model_bundle_id not None and (bundle is None or model_bundle_id != bundle). Failed ticks (dated clock + 7) always show.
    Order preserved."""
def scoring_runs_table(rows) -> pd.DataFrame
    # "Ran at (wall clock, UTC)" (started_at in UTC as "YYYY-MM-DD HH:MM"), "Run date" ("YYYY-MM-DD" or "-"), "Status"
    # (status_text), "Trigger" (str or "-"), "History tests", "Prediction tests" ("passed"/"FAILED"/"-"), "Published"
    # ("yes"/"no"/"-"), "Data-test failure" (str or "-"), "Error type" (str or "-"), "Run" (run_link or None); every column
    # str except "Run" (object, None allowed); sorted by (started_at, scoring_run_id) descending; no cap of its own (the query
    # has LIMIT SCORING_RUNS_LIMIT = 50)
def monitoring_runs_table(rows) -> pd.DataFrame
    # "Ran at (wall clock, UTC)", "Simulated date" ("YYYY-MM-DD" or "-"), "Status" (status_text), "Verdict" (verdict_text),
    # "Cadence" (cadence_label), "Trigger" (str or "-"), "History tests", "Error type" (str or "-"), "Run"; same types; sorted by
    # (started_at, monitoring_run_id) descending; the first TICKS_SHOWN = 50 rows only (display length: the query fetches up to
    # MONITORING_LIMIT = 1000 for the chart and the heartbeat)
SCORING_RUNS_LIMIT = 50; MONITORING_LIMIT = 1000; TICKS_SHOWN = 50
    # app.py passes the two limits explicitly: queries.recent_scoring_runs(limit=SCORING_RUNS_LIMIT),
    # queries.monitoring_history(limit=MONITORING_LIMIT) (the same values as the queries' defaults; a test pins both)
```

### 6. `src/readmission_risk/dashboard/charts.py` (altair)

```python
LAYERS = ("ci", "band_low", "band_high", "points_ok", "points_alert", "points_muted", "rug", "labels")
MUTED_VERDICTS = ("insufficient_data", "incomplete_cohort")
LEGEND_TEXT = "● ok · ◆ alert (labelled) · ○ insufficient data or incomplete cohort (hollow, grey) · △ on the bottom edge: a week without an O/E value · shaded bar: 95% CI, in the point's colour · dashed: alert band"
KNOWN_VERDICTS = ("ok", "alert", *MUTED_VERDICTS)
def oe_chart(points: pd.DataFrame, colors: dict[str, str]) -> alt.LayerChart:
    """alt.layer of exactly len(LAYERS) single-mark charts, in LAYERS order. The band lines use ALL rows of `points` (band_low/
    band_high exist even when O/E is NULL, so the lines never bridge a missing value they do not have); the rug uses only
    points[~points.plotted]; the other five layers use only points[points.plotted]. Every point/label layer selects rows with transform_filter(alt.FieldOneOfPredicate(
    field="verdict", oneOf=[...])), with the lists below; the ci layer filters on KNOWN_VERDICTS too, so a verdict outside
    VERDICT_TEXT is excluded by every point, ci and label layer's oneOf list (it is still in those layers' data, and in the
    band lines and the table). The CI is drawn per week, not as a connected area, so weeks without an O/E value never get a band:
      ci            mark_rule(strokeWidth=6, opacity=0.25); oneOf list(KNOWN_VERDICTS); x sim_date:T, y oe_ci_low:Q,
                    y2 oe_ci_high; color = alt.Color("verdict:N", scale=alt.Scale(domain=list(KNOWN_VERDICTS),
                    range=[colors["ok"], colors["alert"], colors["muted"], colors["muted"]]), legend=None) (an
                    incomplete-cohort week HAS an O/E and CI, cohort.py:149-153, and must not look like a valid estimate)
      band_low      mark_line(strokeDash=[4, 3], color=colors["muted"]); y band_low:Q
      band_high     same on band_high
      points_ok     mark_point(shape="circle", filled=True, size=60, color=colors["ok"]); oneOf ["ok"]; y oe_ratio:Q
      points_alert  mark_point(shape="diamond", filled=True, size=110, color=colors["alert"]); oneOf ["alert"]
      points_muted  mark_point(shape="circle", filled=False, size=60, color=colors["muted"]); oneOf list(MUTED_VERDICTS)
      rug           mark_point(shape="triangle-up", filled=False, size=50, color=colors["muted"]); NO verdict filter (every
                    unplotted week); y = alt.value(312) (pixels from the top of the 320-px plot: the bottom edge, S14); tooltip
                    as the point layers
      labels        mark_text(dy=-10, color=colors["ink"]); oneOf ["alert"]; text = alt.value("alert") (only alerts are
                    labelled: a new bundle's first ~17 ticks are incomplete cohorts, and a label on each would overlap; their
                    hollow grey marks and the table say it)
    Every layer encodes x as alt.X("sim_date:T", timeUnit="utcyearmonthdate", title="Simulated week",
    axis=alt.Axis(format="%Y-%m-%d")) (UTC: Vega-Lite otherwise shows dates in the viewer's local zone, so a viewer west of
    UTC would see the previous day, against V7), and the tooltip shows the preformatted string `sim_week` (never a temporal
    field); the three point layers and labels encode
    alt.Y("oe_ratio:Q", title="Observed / expected"); ci, band_low and band_high use their own y field with the SAME title
    "Observed / expected" (otherwise Vega-Lite merges the field names into the axis title).
    Tooltip on the three point layers and the rug, with these titles: sim_week "Simulated week", verdict_label "Verdict",
    oe_ratio "O/E", oe_ci_low "95% CI low", oe_ci_high "95% CI high", observed "Observed", expected "Expected",
    min_positives_floor "Positives floor", cadence_label "Cadence". No Vega legend (the encodings are constants); the app writes
    LEGEND_TEXT as a caption. .properties(height=320); .configure(background=colors["background"]) (OPAQUE: Streamlit's
    st.context.theme may misreport the theme on a session's first load, so the chart carries its own readable background);
    configure_axis(labelColor=ink, titleColor=ink, gridColor=muted, gridOpacity=0.3, domainColor=ink); y title "Observed /
    expected", x title "Simulated week". Rendered with st.altair_chart(chart, theme=None, width="stretch") (Streamlit 1.66.1
    deprecates use_container_width; measured: it logs a removal warning on every run)."""
```

### 7. `src/readmission_risk/dashboard/chrome.py` (stdlib + `scoring.constants`, `warehouse.bigquery`, `dashboard.present.short_id`)

```python
SYNTHETIC_NOTICE = "**Synthetic data · demo only · not for clinical use.**"
INTENDED_USE = "**Intended use:** a simulated weekly triage call sheet for a fictional clinical team; a portfolio demo."
MODEL_CARD_URL: str | None = None   # E5: slice 5 sets "https://github.com/fma00/synthea-readmission-risk/blob/main/docs/model-card.md"
SYNTHEA_URL = "https://github.com/synthetichealth/synthea"; SYNTHEA_LICENSE_URL = SYNTHEA_URL + "/blob/master/LICENSE"
GIT_COMMIT_ENV = "READMISSION_RISK_GIT_COMMIT"; _COMMIT_RE = re.compile(r"[0-9a-f]{7,40}")
def honesty_strip() -> str:
    """"  \n".join([INTENDED_USE, DEMO_NOTICE (imported from scoring.constants, verbatim),
    f"**Model card:** [docs/model-card.md]({MODEL_CARD_URL})" if MODEL_CARD_URL else "**Model card:** in preparation
    (published with v3.0)."])"""
def git_commit(environ: Mapping[str, str]) -> str | None   # environ.get(GIT_COMMIT_ENV) iff it fullmatches _COMMIT_RE, else None
def footer(*, commit: str | None, bundles: Iterable) -> str:
    """" · ".join of: "Model bundle " + ", ".join(f"`{s}`" for s in R), where R is built in three steps: (1) sort the distinct
    non-None raw ids; (2) map each through short_id; (3) drop repeats, keeping the order of step 2 (so two different non-hex ids
    give one "`unknown`", placed where the first of them sorted); or "Model bundle: none shown" if there is no non-None id; f"Gold `{SERVING_GOLD_FINGERPRINT[:12]}…`"; f"Image commit `{commit[:12]}`" or "Image commit unknown (not a deployed
    image)"; f"Synthetic patients generated with [Synthea]({SYNTHEA_URL}) ([Apache License 2.0]({SYNTHEA_LICENSE_URL}))"."""
```
**Markdown safety (critic 7):** every value interpolated into `st.markdown`/`st.caption`/`st.info`/`st.warning` is one of: a fixed
string; a date/datetime formatted by `strftime`; an int; `short_id` (64-hex or "unknown"); `status_word` (a known key, "other" or
"-"); a validated commit; `rate_text`/`panel_text` of those. Free text from BigQuery (reason descriptions, error types, failure
labels, raw status values) appears only inside `st.dataframe` cells.

### 8. `.streamlit/config.toml` (repo root; Streamlit reads `./.streamlit/config.toml` from the working directory; slice 4's image runs from its `WORKDIR` holding this file)

```toml
[global]
developmentMode = false

[client]
showErrorDetails = "none"
toolbarMode = "viewer"
showSidebarNavigation = false

[browser]
gatherUsageStats = false

[server]
headless = true
fileWatcherType = "none"
runOnSave = false
enableXsrfProtection = true
enableCORS = true
enableStaticServing = false
maxUploadSize = 1
maxMessageSize = 16
allowedHosts = ["dashboard-271458030145.us-central1.run.app", "localhost", "127.0.0.1"]
```
Measured in a scratch venv (Streamlit 1.66.1, confirmed by the critic): every key exists; under `showErrorDetails = "none"` an
uncaught exception shows only "This app has encountered an error. The original error message is redacted…". Streamlit parses
`./.streamlit/config.toml` ONCE per process, from the working directory at first parse; a later `chdir` is not seen unless
`streamlit.config.get_config_options(force_reparse=True)` is called (measured by the readiness pass), so the AppTest fixture does
exactly that (V-A). The dev hosts in `allowedHosts` are S9's recorded deviation.

### 9. `src/readmission_risk/dashboard/app.py` and `scripts/dashboard_app.py`

`scripts/dashboard_app.py` (the whole file apart from its docstring, which names `streamlit run scripts/dashboard_app.py` and S11):
```python
from readmission_risk.dashboard.app import main

main()
```
`app.py` (module-level definitions only; importing it renders nothing and calls no client). It imports the helper modules,
never their functions: `from readmission_risk import gcp`, `from readmission_risk.dashboard import charts, chrome, errors,
present, queries` and `from readmission_risk.dashboard import heartbeat as heartbeat_mod`, and calls every helper as a module
attribute (`present.call_sheet_frame(...)`, `charts.oe_chart(...)`, `heartbeat_mod.heartbeat(...)`, …), so a test's
`monkeypatch.setattr("readmission_risk.dashboard.present.call_sheet_frame", …)` reaches the app (the module is imported once and
AppTest reruns reuse it). `PublishedDate`, `Heartbeat`, `THEMES`/`chart_colors` (`reporting.palette`), `SERVING_GOLD_FINGERPRINT`
and the D2 constants (`warehouse.dashboard_views.REASON_RATE_CUTOFF`/`REASON_RATE_MIN_N`) may be imported by name (never
patched). The bare names in the sketch below stand for those module-attribute calls.
```python
CACHE_TTL = timedelta(hours=3)       # S4
CACHE_MAX_ENTRIES = 64
logger = logging.getLogger("readmission_risk.dashboard")
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
def _ensure_log_handler() -> None:
    """Called once at import: if `logger` has no handler whose `_readmission_risk_dashboard` attribute is True, add a
    logging.StreamHandler(sys.stderr) with logging.Formatter(_LOG_FORMAT), level WARNING, that attribute set True. Propagation stays
    on (pytest's caplog captures through the root). Nothing else configures logging (Streamlit configures only `streamlit.*`), so
    without this handler the records would rely on logging.lastResort. Cloud Run sends stderr to Cloud Logging; the default
    Formatter appends the traceback of exc_info records."""
_ensure_log_handler()
_cache = st.cache_resource(ttl=CACHE_TTL, max_entries=CACHE_MAX_ENTRIES, show_spinner="Reading BigQuery…")   # S5: no pickling; the spinner shows only on a miss (S16 bounds it)

@_cache
def load_published_dates() -> tuple[PublishedDate, ...]:
    return tuple(PublishedDate.from_row(r) for r in queries.run_bounded(gcp.bigquery_client(), queries.published_dates()))
@_cache
def load_call_sheet(published: PublishedDate) -> tuple[MappingProxyType, ...]:
    return present.freeze_rows(queries.run_bounded(gcp.bigquery_client(), queries.call_sheet(published)))
@_cache
def load_monitoring() -> tuple[MappingProxyType, ...]:   # present.freeze_rows(queries.run_bounded(client, queries.monitoring_history(limit=MONITORING_LIMIT)))
@_cache
def load_scoring_runs() -> tuple[MappingProxyType, ...]: # present.freeze_rows(queries.run_bounded(client, queries.recent_scoring_runs(limit=SCORING_RUNS_LIMIT)))

def fetch(loader, *args, breaker: dict) -> tuple[object | None, FailureClass | None]:
    """S18 first: if breaker["open"], logger.warning("dashboard query %s skipped (BigQuery unreachable earlier in this run)",
    loader.__name__) and return (None, "unreachable") without calling the loader. Else (loader(*args), None) on success. Any Exception (# noqa: BLE001 -- FR16), AssertionError included (production must
    degrade per view) -> logger.warning("dashboard query %s failed (%s)", loader.__name__, type(exc).__name__, exc_info=True)
    and (None, cls) with cls = errors.classify(exc); if cls == "unreachable", breaker["open"] = True. Exceptions are never
    cached. `main()` creates `breaker = {"open": False}` once per script run and passes it to every fetch (also inside the tab
    helpers). Tests detect a swallowed trap/fake assertion through that
    log record (V-A's log guard), never through the panel alone."""

def guarded(name: str, body: Callable[[], T], fallback: T = None) -> T:
    """body() on success. Any Exception (# noqa: BLE001 -- E7) -> logger.warning("dashboard part %s failed (%s)", name,
    type(exc).__name__, exc_info=True), st.warning(RENDER_FAILED.format(view=name)) and `fallback`."""

def main() -> None:
    st.set_page_config(page_title="Readmission call sheet · synthetic demo", layout="wide")
    try:
        theme = THEMES.get(st.context.theme.type or "light", THEMES["light"])
    except Exception:  # noqa: BLE001 -- st.context can be unavailable; light is always readable (opaque chart background)
        theme = THEMES["light"]
    guarded("The notice", _notice)                         # st.markdown(SYNTHETIC_NOTICE); st.info(chrome.honesty_strip())
    monitoring, m_fail = fetch(load_monitoring)
    runs, r_fail = fetch(load_scoring_runs)
    hb = guarded("The heartbeat", lambda: heartbeat_mod.heartbeat(monitoring, runs, now=heartbeat_mod.wall_now()),
                 fallback=Heartbeat.unavailable())       # monitoring None on failure -> state unknown
    guarded("The heartbeat line", lambda: _heartbeat_line(hb, m_fail))   # st.markdown(line); if m_fail: st.warning(panel_text(...))
    call_tab, monitoring_tab, history_tab = st.tabs(["Call sheet", "Monitoring", "Run history"])
    shown_bundles: list = []
    with call_tab: guarded("The call sheet", lambda: _call_sheet(hb, shown_bundles))
    with monitoring_tab: guarded("The monitoring view", lambda: _monitoring(monitoring, m_fail, hb, theme))
    with history_tab: guarded("The run history", lambda: _history(runs, r_fail, monitoring, m_fail, hb))
    st.divider()
    guarded("The footer", lambda: st.caption(chrome.footer(commit=chrome.git_commit(os.environ), bundles=shown_bundles)))
```
- `_call_sheet(hb, shown)`: `dates, fail = fetch(load_published_dates)`; fail → warning panel (table row 2), empty → info panel;
  else `picked = st.selectbox("Run date (simulated)", options=tuple(p.run_date for p in dates), format_func=lambda d:
  f"{d:%Y-%m-%d}")` with NO `key` and a position-independent label (measured in a scratch app: without a key Streamlit derives
  the widget identity from its options, so a changed date list resets the choice to the first option = the newest; a plain
  rerun keeps the user's pick; AppTest breaks if a stored value's label changes, so the label never depends on the position);
  `chosen = next(p for p in dates if p.run_date == picked)` (the CURRENT list's `PublishedDate`, so a republish of the same date
  never feeds an outdated object to the call-sheet query); `st.caption(f"Simulated today: {dates[0].clock_sim_date:%Y-%m-%d}")`;
  the clock-mismatch caption (§4) only if `hb.sim_date` is not None; `rows, fail = fetch(load_call_sheet, chosen)`; fail →
  warning, empty → info (row 3); else each `sheet_warnings` line as `st.caption`; `top, rest = split_top_n(rows)`;
  `st.subheader(f"Call this week: top {len(top)}")`; if `top`: `st.dataframe(call_sheet_frame(top), hide_index=True,
  column_config=_SHEET_COLUMNS)`, else `st.caption("No discharge is flagged Top-N for this run date.")`; if `rest`:
  `with st.expander(f"Rest of the ranked list ({len(rest)})"):` the same table for `rest` (no expander when `rest` is empty);
  `st.caption(f"Reason's historical readmission rate: the share of settled discharges before {REASON_RATE_CUTOFF:%Y-%m-%d}
  with the same admission reason that had an unplanned readmission within 30 days (planned chemotherapy/radiation stays
  excluded); withheld under {REASON_RATE_MIN_N} discharges.")`; `shown.extend(r["model_bundle_id"] for r in rows)`.
  `_SHEET_COLUMNS = {"Risk score (%)": NumberColumn(format="%.1f"), "Age (years)": NumberColumn(format="%.0f"),
  "Length of stay (days)": NumberColumn(format="%.1f"), "Discharged (UTC)": DatetimeColumn(format="YYYY-MM-DD HH:mm")}`.
- `_monitoring(monitoring, fail, hb, theme)`, branches in order, each the only content of the tab: (1) fail → warning (row 4);
  (2) `hb.state == "unknown"` → `st.info(MON_NO_BUNDLE_UNKNOWN)`; (3) `hb.monitored_bundle is None` (no succeeded tick yet) →
  `st.info(MON_NO_BUNDLE_YET)`; (4) `points = monitoring_points(monitoring, bundle=hb.monitored_bundle)` empty → info (row 4,
  "empty"); (5) else
  `st.altair_chart(oe_chart(points, chart_colors(theme)), theme=None, width="stretch")`; `st.caption(LEGEND_TEXT)`; if any
  muted week WITH an O/E (k1 = rows with verdict in MUTED_VERDICTS and `plotted` True) or any unplotted week (k2 = rows with
  `plotted` False, whatever the verdict): `st.caption(" · ".join(parts))` with parts, in this order, each only if its count > 0:
  f"{count_text(k1, 'week has', 'weeks have')} insufficient data or an incomplete cohort (hollow grey; the alert band does not
  apply)" and f"{count_text(k2, 'week has', 'weeks have')} no O/E value (△ on the bottom edge; see the table)"; texts (app.py
  constants): `MON_NO_BUNDLE_UNKNOWN = "The simulated clock could not be read, so the monitored model bundle is unknown and the
  chart is not shown."`, `MON_NO_BUNDLE_YET = "No monitoring tick has succeeded yet, so there is no monitored model bundle to
  chart."`; `st.dataframe(monitoring_table(points),
  hide_index=True)`.
- `_history(runs, r_fail, monitoring, m_fail, hb)`: two tables, each with the same branch order. For the scoring runs:
  `st.subheader("Scoring runs")`; (1) its query failed (`r_fail`) → warning (row 5), nothing else; (2) else `hb.state ==
  "unknown"` (the heartbeat query or computation failed, so no clock) → `st.info(NO_CLOCK_UNKNOWN.format(what="scoring runs"))`;
  (3) else `hb.sim_date is None` (no succeeded tick yet: states no_ticks / not_started without a clock) →
  `st.info(NO_CLOCK_YET.format(what="scoring runs"))`; (4) else `kept, hidden = visible_scoring_runs(runs, clock=hb.sim_date)`:
  `kept` empty → info (row 5, "empty"), else the dataframe; then, if `hidden > 0`, the hidden caption (shown in both sub-cases).
  The monitoring-ticks table follows the same four branches with `m_fail`, `visible_monitoring_runs(monitoring,
  clock=hb.sim_date, bundle=hb.monitored_bundle)`, `what="monitoring ticks"` and row 6. Texts (app.py constants):
  `NO_CLOCK_UNKNOWN = "The simulated date could not be read, so the {what} are not shown (some may be dated after it)."`,
  `NO_CLOCK_YET = "No monitoring tick has succeeded yet, so there is no simulated date and no {what} are shown."`; hidden
  captions: f"{count_text(hidden, 'run is', 'runs are')} not shown: dated after the simulated date or scored on another gold
  table." and f"{count_text(hidden, 'tick is', 'ticks are')} not shown: dated after the simulated date or of another model
  bundle.". Truncation captions (critic round 6, 7): scoring runs, if `len(runs) == SCORING_RUNS_LIMIT`: f"Only the
  {SCORING_RUNS_LIMIT} most recent scoring runs are loaded."; monitoring ticks, if `len(kept) > TICKS_SHOWN`: f"Showing the
  {TICKS_SHOWN} most recent of {len(kept)} ticks.", and if `len(monitoring) == MONITORING_LIMIT`: f"Only the {MONITORING_LIMIT}
  most recent ticks are loaded." (the S12 hidden counts are over the loaded rows). Dataframes: `st.dataframe(scoring_runs_table(kept), hide_index=True, column_config={"Run":
  LinkColumn(display_text="run")})`, and `monitoring_runs_table(kept)` with the same `column_config`.
- **Rules** (each enforced by a V-B source ban; the banned words may not appear ANYWHERE in the scanned files, comments and
  docstrings included: write "Streamlit's data cache", not its API name): the only widget is that `st.selectbox` (its options
  are the listed run dates, and the query/cache argument is the current list's `PublishedDate` for the picked date, so the only
  user-chosen value in SQL or a cache key is a listed date). Not used anywhere in `dashboard/` or `scripts/dashboard_app.py`:
  every other Streamlit input widget, `query_params`, `st.html`, `unsafe_allow_html`, `st.exception`, `import traceback`,
  the data-cache decorator, `to_dataframe`/`to_arrow` (E1), and any wall-clock read other than `heartbeat.wall_now` (S6).
  `gcp.bigquery_client` is called as a module attribute (patchable). Rows reach pandas only inside `present.py`:
  `call_sheet_frame` builds its frame column by column with explicit dtypes (§5); every other function there may use
  `pd.DataFrame([dict(r) for r in rows])`. `st.set_page_config`, `st.tabs` (constant labels) and `st.divider()` are
  deliberately outside E7: Streamlit primitives with constant arguments, whose failure would be a Streamlit bug.

## UX flow (pipeline)

Cold visit → `main()` → monitoring + scoring-runs queries (cached 3 h) → chrome (notice, honesty strip, heartbeat) → tabs:
call sheet (published dates → newest → call-sheet rows → Top-N table + the rest), monitoring (points → chart + table), run history
(two tables) → footer. Changing the date reruns the script: only `load_call_sheet(new date)` can miss the cache. Streamlit renders
all three tabs' content on each run (a cold visit runs 4 queries).

## Data handling

No modelling, split or label change: the app reads the three authorized views only, through slice 2's queries (bytes cap,
`component=dashboard`, parameters only, no `CURRENT_*`). No label column reaches the app (the views exclude them; a test asserts
the rendered tables contain none of `FORBIDDEN_OUTPUT_COLUMNS` and no demographic column). The "why" rate is slice 2's D2
aggregate, rendered as text with its n. The simulated clock drives every view; `heartbeat.wall_now()` is the only wall-clock read
(S6). No randomness. Cached values are immutable (S5), so one session cannot change another's data. Tests never reach GCP.

## Failure modes

| Case | Behaviour |
|---|---|
| A query raises | that view's `st.warning(panel_text(class, …))` (§3 table); other views render; logged to stderr with class + traceback (§9 handler); not cached; `at.exception` empty |
| Monitoring query fails | heartbeat "❔ Heartbeat unavailable" + its warning; monitoring tab and the monitoring-run table show their warnings; the scoring-run table shows the "simulated date could not be read" info (S12 cannot be applied without a clock) |
| Scoring-runs query fails | heartbeat "data tests unknown"; run-history scoring table shows its warning |
| `PublishedDate.from_row` raises | inside `load_published_dates`: the dates panel, class `unreachable` (S4) |
| A presentation bug in any guarded part (notice, heartbeat, heartbeat line, a tab, footer) | E7 text for that part; the other parts render; a failed heartbeat computation hands `Heartbeat.unavailable()` to the tabs |
| An AssertionError (a library's assert, or in tests the trap/fake) | handled like any exception (panel or E7 text); in tests the log guard fails the test |
| `st.context.theme` raises | light tokens |
| The date list changes during a session (new tick, republish) | the picker resets to the newest date; the query uses the current list's `PublishedDate` |
| No published date / empty call sheet / no points / no runs | the matching `empty` info panel |
| No Top-N row / no rest | caption "No discharge is flagged Top-N for this run date." / no expander |
| Runs after the clock, on another gold, ticks of another bundle | hidden, counted in a caption (S12) |
| Row count / run id / Top-N mismatch | `sheet_warnings` captions, rows still shown |
| Clock mismatch between cache entries | caption (§4) |
| NULL gold feature / description / reason | empty cell / `reason_text` (§5) |
| No history / n < 30 / NULL rate with n ≥ 30 | "no history" / "withheld (n = k < 30)" / "rate unavailable (n = k)", never a number |
| Monitoring rows, no succeeded scheduled weekly tick | "Schedule not started" |
| Newest tick's scoring run older than the 50 recent runs | "data tests unknown" (documented) |
| NULL O/E | table only; caption counts them |
| Incomplete-cohort week with an O/E | hollow grey point, grey CI bar, no text label |
| Bundle id not 64-hex / unknown status | "unknown" / "other" in markdown; raw value only in table cells |
| `github_run_url` not this repo's run URL | no link |
| `READMISSION_RISK_GIT_COMMIT` unset/malformed | "Image commit unknown (not a deployed image)" |

## Verification

All offline; `ruff check .` + `pytest` green (from 2,031 passed / 2 skipped at `b46a940`).

- **V-F (`tests/monitoring/test_freshness.py`):** `stale_after` = newest succeeded schedule+weekly `started_at` + 14 days; burn-in
  (both triggers), dispatch, catch_up and failed rows ignored; a tie on `started_at` broken by `monitoring_run_id`; naive
  `started_at` → ValueError, and an ISO-string `started_at` → ValueError (E10); none → None. `config.WEEKLY_CRON is freshness.WEEKLY_CRON`, `config.period_of is freshness.period_of`,
  `ledger.cadence_label is freshness.cadence_label`. A subprocess import of `readmission_risk.monitoring.freshness` loads no
  `readmission_risk.bundles`, `pandas`, `numpy` or `google`. Existing `tests/monitoring/test_ledger.py` passes unchanged; plus a
  parity test: for 6 scripted frames (empty; burn-in only; one weekly scheduled success; laptop catch-up only; a failed scheduled
  weekly attempt newer than a success; two weekly successes with equal `started_at`), `last_tick_badge(frame, cron=WEEKLY_CRON)`
  and `render_ledger(frame, <a fixed scoring-runs frame>, config=tests.monitoring.world.CONFIG)` equal literal values captured by
  running the PRE-refactor code at `b46a940` (the capture script and its output are recorded in this note's Implementation
  section).
- **V-P (`tests/reporting/test_palette.py`):** the 10 `THEMES` hex values pinned literally; `chart_colors` for both themes (5 keys
  each, as listed in §2); a subprocess import of `readmission_risk.reporting.palette` loads no `matplotlib`. **README figures:**
  after the refactor, both `make_readme_assets` recipes in CLAUDE.md, run with `--out-dir <scratchpad>/v2` and
  `--out-dir <scratchpad>/v1`, reproduce `docs/img/v2/*` and `docs/img/{reliability_light.svg,reliability_dark.svg,sample_topn.md}`
  byte for byte (`cmp`; owner's machine; recorded in this note).
- **V-E (`tests/dashboard/test_errors.py`):** BadRequest with `errors=[{"reason": "bytesBilledLimitExceeded"}]` → bytes_cap;
  Forbidden with `quotaExceeded`, Forbidden with `rateLimitExceeded`, `TooManyRequests("x")` → quota; Forbidden with
  `accessDenied`, `ServiceUnavailable`, `RetryError("x", cause=ServiceUnavailable("y"))`, `requests.ConnectionError`,
  `google.auth.exceptions.RefreshError`, `KeyError`, `concurrent.futures.TimeoutError` (what `job.result(timeout=20)` raises) → unreachable;
  `RetryError("x", cause=Forbidden("y", errors=[{"reason": "rateLimitExceeded"}]))` → quota (bigquery's `DEFAULT_RETRY` retries
  rate limits, so a real one arrives wrapped after S16's window); a quota error as `__cause__` of a RuntimeError → quota; an
  exception whose `__context__` points back to itself terminates. `QUOTA_REASONS == warehouse.gold._QUOTA_REASONS`. `panel_text`
  for all four kinds contains `what` and `still`; bytes_cap contains "100 MiB"; no panel contains "{".
- **V-H (`tests/dashboard/test_heartbeat.py`):** `simulated_clock` on literal, hand-written cases whose expected outputs follow the
  SQL CTEs' semantics (`queries.py:83-91`) as read by the test author, never computed by the function itself: the newest succeeded
  bundle wins although an older bundle has a later sim_date; within the bundle the max sim_date wins even if an older tick has it;
  failed rows and NULL sim_date/bundle ignored; a `started_at` tie broken by `monitoring_run_id`; None without candidates.
  `late_after` (S13) on literals: Monday 2026-10-26 00:17Z → Friday 2026-11-06 00:00Z; Wednesday 2026-10-28 23:17Z → the same
  Friday; Thursday 2026-10-29 02:27Z (a delayed slot) → the same Friday; a non-UTC tz-aware anchor is converted first. The
  delayed-slot case: anchor Monday 2026-10-19 00:17Z, `now` = Thursday 2026-10-29 02:27Z (10 d 2 h later, the next week's
  delayed Wednesday slot not yet recorded) → ok; `now` = Friday 2026-10-30 00:00Z → late. The newest tick failed with `history_tests_passed = False` 2 days after a scheduled
  success → state ok, "(failed)" and "data tests FAILED" in the line. States at the boundaries (`now = late_at - 1 µs` ok,
  `= late_at` late, `= stale_at` stale; no anchor not_started; `[]` no_ticks; `None` unknown); `Heartbeat.unavailable()` equals
  `heartbeat(None, None, now=…)`; naive `now` → ValueError; tests flags (both True "passed", one False "FAILED", run absent /
  runs None / tick run id None "unknown"); `heartbeat_line` for each of the six states equals a literal expected string (pins
  the §4 format, incl. the 12-character bundle).
- **V-R (`tests/dashboard/test_present.py`):** `rate_text` six cases (no history; n None with a rate → "no history"; withheld n = 9;
  n = 9 WITH a rate 0.25 → still "withheld (n = 9 < 30)"; rate unavailable n = 40; shown),
  the withheld and unavailable texts match no `\d%`; `reason_text` four literal cases; `call_sheet_frame` columns ==
  `CALL_SHEET_DISPLAY`, dtypes as specified (also for empty input), NULL counts → `<NA>`; **label/demographic exclusion with
  sentinels:** input rows carrying extra keys `is_readmitted` = 777777, `label_horizon` = 1999-09-09, `gender` =
  "SENTINEL-GENDER", `race` = "SENTINEL-RACE" → no output column derives from them and no cell equals or contains a sentinel;
  input rows unmutated (`MappingProxyType` input works); `split_top_n` incl. no Top-N row and no rest; each `sheet_warnings`
  sentence as a literal string (and none when consistent); `monitoring_points` (newest per sim_date, bundle filter, `plotted`,
  columns/dtypes, empty); `monitoring_table` formatting incl. a NULL CI bound and a NULL `expected`; `visible_scoring_runs` /
  `visible_monitoring_runs` (each hide rule, NULL date/fingerprint/bundle kept, clock None hides every dated published run / succeeded tick and keeps dated failed ticks / unpublished runs, order kept,
  hidden counts); both runs tables' columns and sort; `run_link` accepts `…/runs/123` and `…/runs/123/attempts/2`, rejects
  `javascript:…`, another repo, a trailing path, None, a non-str; `short_id` (64-hex → 12 + "…", 63 chars / uppercase /
  backtick / an int / None → "unknown"); `status_word`; `freeze_rows` returns read-only mappings.
- **V-C (`tests/dashboard/test_charts.py`):** `oe_chart(points, chart_colors(theme)).to_dict()` for both themes: exactly 8 layers
  (`LAYERS`) whose marks are, in order, rule / line / line / point / point / point / point (rug) / text; the point layers' `shape` circle / diamond /
  circle and `filled` True / True / False; each layer's filter `oneOf` list as in §6 (ci: KNOWN_VERDICTS; labels: ["alert"]);
  the ci layer's colour scale domain == KNOWN_VERDICTS and range == [ok, alert, muted, muted]; every colour in the spec ∈
  `chart_colors(theme).values()`; `config.background` == `colors["background"]`; the tooltip lists `min_positives_floor`; with a
  fixture of 6 weeks (1 ok; 2 alert; 3 NULL O/E; 4 incomplete_cohort WITH O/E and CI; 5 insufficient_data WITH O/E and CI;
  6 ok): the rule and point layers' data hold weeks 1, 2, 4, 5, 6 only, the band-line layers' data hold all 6 (each layer's data
  via the spec's `datasets`); weeks 4 and 5 are in `points_muted` and the ci layer, and in no label; week 3 is the rug's only
  row; in a SEPARATE fixture (the 6 weeks above plus a seventh week with verdict `"other"` and an O/E), that row is in the plotted data of the ci/point/label layers but excluded by each of their `oneOf` lists (asserted on the lists
  and on that row's verdict value), and present in the band lines' data. The rug's mark is `point` with `filled` False and its
  `y` a constant `value` 312. **Dates:** every `sim_date` value in the spec's `datasets` is the ISO string of midnight with
  offset `+00:00` (e.g. "2023-10-30T00:00:00+00:00"), and `sim_week` equals its first 10 characters.
- **V-K (`tests/dashboard/test_chrome.py`):** the E5 consistency test (`MODEL_CARD_URL is None` iff `docs/model-card.md` does not
  exist; while None the strip contains "in preparation (published with v3.0)" and no link); `DEMO_NOTICE in honesty_strip()`, `chrome.DEMO_NOTICE is scoring.constants.DEMO_NOTICE`;
  `git_commit` accepts 7-40 lower-hex only; `footer` literal strings for (no commit, no bundles) and (commit, ids
  ["f"*64, "a"*64, None, "NOT-HEX", "zz"]) → "Model bundle `unknown`, `aaaaaaaaaaaa…`, `ffffffffffff…` · …" (sorted raw ids, uppercase first:
  "NOT-HEX", "a…", "f…", "zz" → `unknown`, `aaaa…`, `ffff…`, `unknown` → de-duplicated: `unknown`, `aaaa…`, `ffff…`; the test
  pins exactly that order).
- **V-S (`tests/dashboard/test_streamlit_config.py`):** `tomllib.load(.streamlit/config.toml)` == the literal dict of §8 (exact
  equality); every key exists in `streamlit.config._config_options_template`.
- **V-A (`tests/dashboard/test_app.py`, AppTest):** `AppTest.from_file(str(REPO / "scripts/dashboard_app.py"), default_timeout=30)`.
  **Config isolation:** the autouse fixture does `monkeypatch.chdir(REPO)`, `monkeypatch.setenv("HOME", str(tmp_path))` (so no
  `~/.streamlit/config.toml` is read; measured: the global path is re-resolved through `HOME` on every reparse, and `STREAMLIT_*`
  environment variables are not applied by a reparse), then `streamlit.config.get_config_options(force_reparse=True)`, and
  asserts that `streamlit.config.get_option(k)` equals the §8 value for EVERY §8 key (a reparse at teardown restores nothing
  else, since tests run from the repo root). `tests/dashboard/fake_world.py` builds a `FakeBigQuery`
  (`tests/warehouse/fake_bigquery.py`) whose `on_query` routes by the exact SQL of `queries.published_dates()`, `call_sheet(<any>)`
  (one SQL; rows chosen by the `as_of_date` parameter), `monitoring_history()`, `recent_scoring_runs()`; an unknown SQL raises
  AssertionError. Every `FakeJob` the world returns has `allow_timeout=True` (S16's `run_bounded` passes `timeout=` to
  `result`). `world.fail[name] = (exc, site)` with `site` "query" (raised by `on_query`, as a submission failure) or "result"
  (the job's `raise_on_result`, as BigQuery reports bytes-cap and quota failures) raises on that query only; `world.rows[name] = []` empties that query only. The four
  answers are independent literal fixtures (no world answer is computed from another, and none with `simulated_clock`): the
  published rows' `clock_sim_date` is a literal field `world.sql_clock` (default the same date the hand-written monitoring rows
  give). Tests that model the SQL's coupling set both sides explicitly (case 4 empties `monitoring_history` AND
  `published_dates`, as the real clock CROSS JOIN would). Fixture rows: 3 published dates (all ≤ the clock), each with `n_predictions` 5, `top_n` 2, `window_days` 7,
  `model_bundle_id` B and `scoring_run_id` the date's run (r3, r4, r5; see the monitoring table); every call-sheet row has
  `as_of_date` = its date, `scoring_run_id` = that run, `model_bundle_id` B and `discharge_time` = the date minus (rank) days at
  12:00Z (inside the 7-day window); per date a call
  sheet of 5 rows, Top-N 2, in rank order: rank 1 shown rate; rank 2 the withheld reason (n = 9, so it is in the Top-N table);
  rank 3 "no history"; rank 4 NULL `prior_emergency_count`; rank 5 shown rate; every call-sheet row also carries the V-R
  sentinel keys. Monitoring rows (all `status` "succeeded", oldest first; B = the monitored bundle = "b" * 64; O = "c" * 64; every row's `history_tests_passed` True and `scoring_run_id` = the run named):

  | id | started_at (UTC) | cadence / trigger | sim_date | verdict | O/E [CI] | bundle | run |
  |---|---|---|---|---|---|---|---|
  | m0 | 2026-10-15 10:00 | dispatch / workflow_dispatch | 2023-09-18 | incomplete_cohort | 0.80 [0.40, 1.40] | O | r0 |
  | m1 | 2026-10-20 09:00 | burn_in / workflow_dispatch | 2023-10-02 | incomplete_cohort | 0.90 [0.50, 1.50] | B | r1 |
  | m2 | 2026-10-20 10:00 | burn_in / workflow_dispatch | 2023-10-09 | insufficient_data | 1.20 [0.40, 2.80] | B | r2 |
  | m3 | 2026-10-20 11:00 | burn_in / workflow_dispatch | 2023-10-16 | incomplete_cohort | NULL | B | r3 |
  | m4 | 2026-10-21 09:00 | burn_in / workflow_dispatch | 2023-10-23 | alert | 1.90 [1.30, 2.60] | B | r4 |
  | m5 | 2026-10-26 00:17 | weekly / schedule | 2023-10-30 | ok | 1.02 [0.80, 1.30] | B | r5 |

  m5 is the newest row, the anchor (a Monday) and the clock tick: clock 2023-10-30, monitored bundle B. Published dates:
  2023-10-30, 2023-10-23, 2023-10-16 (runs r5, r4, r3; `world.sql_clock` 2023-10-30). Scoring runs: r0-r5 (succeeded,
  published, serving fingerprint, `history_tests_passed` and `predictions_tests_passed` True, `run_date` = the tick's sim_date),
  plus r-future (`run_date` 2025-06-30, after the clock) and r-v1 (`run_date` 2025-07-31, another `gold_fingerprint`): the S12
  filter hides exactly those two; m0 is hidden from the monitoring-ticks table (bundle O). `FIXED_NOW` = 2026-10-27 12:00Z
  (state ok). The chart's points: m1, m2, m4, m5 plotted, m3 on the rug; the muted caption: "2 weeks have insufficient data or
  an incomplete cohort (hollow grey; the alert band does not apply) · 1 week has no O/E value (△ on the bottom edge; see the
  table)". Autouse: `st.cache_resource.clear()` before AND after each test;
  `monkeypatch.setattr("readmission_risk.gcp.bigquery_client", lambda: world.client)`;
  `monkeypatch.setattr("readmission_risk.dashboard.heartbeat.wall_now", lambda: FIXED_NOW)`; the conftest's E2 trap stays armed.
  **Log guard** (autouse; `caplog.set_level(logging.WARNING)`): at teardown it takes the records with
  `record.name.startswith("readmission_risk.dashboard")` and builds `Counter((r.getMessage(), r.exc_info[0] if r.exc_info else
  None) for r in records)`; it fails the test if any exception class is `AssertionError` or a subclass, or if the Counter differs
  from the test's declared `expected_log` (default empty), e.g. `{("dashboard query load_monitoring failed (ServiceUnavailable)",
  ServiceUnavailable): 1}`. Records of other loggers (`streamlit.*`) are ignored. The check is a plain function
  (`check_log(records, expected)`) so a meta-test can call it. Cases:
  1. happy path: `SYNTHETIC_NOTICE`, `DEMO_NOTICE` and "**Model card:** in preparation (published with v3.0)." in the rendered
     markdown/info; the heartbeat markdown equals a LITERAL expected string written in the test from the fixture's values
     exactly "✅ **Fresh** · simulated date 2023-10-30 · last tick ran 2026-10-26 00:17 UTC (succeeded) · data tests passed ·
     bundle `bbbbbbbbbbbb…`", never computed by `heartbeat_line`; tab labels `["Call sheet", "Monitoring", "Run history"]`;
     selectbox options newest first (`"YYYY-MM-DD"` labels), value the first; a caption "Simulated today: <clock>"; first
     dataframe = the 2 Top-N rows, its columns == `CALL_SHEET_DISPLAY`; its rank-2 cell of "Reason's historical readmission
     rate" == "withheld (n = 9 < 30)"; no rendered dataframe cell contains a sentinel; `len(at.get("vega_lite_chart")) == 1`
     (AppTest exposes the Altair chart only as an element of type "vega_lite_chart"); the scoring-runs dataframe holds the
     six runs r0-r5 only (newest first) and the caption "2 runs are not shown: dated after the simulated date or scored on another gold
     table." appears; the monitoring-ticks dataframe excludes the other bundle's tick (caption "1 tick is not shown: dated after
     the simulated date or of another model bundle."); the footer contains "Model bundle `bbbbbbbbbbbb…`", f"Gold `{SERVING_GOLD_FINGERPRINT[:12]}…`" and exactly "Image commit
     unknown (not a deployed image)" (the autouse fixture does `monkeypatch.delenv("READMISSION_RISK_GIT_COMMIT",
     raising=False)`, so the developer's environment cannot change it); no clock-mismatch caption; no `sheet_warnings`
     caption; `at.exception` empty; **nothing fell back:** `at.warning` is
     empty, `at.info` holds exactly one element (the honesty strip), and no element's text contains "could not be displayed",
     "could not be read", "was not loaded" or "nothing to show yet".
  2. jobs: every recorded query's `job_config.maximum_bytes_billed == MAX_BYTES_BILLED`, `labels == {"component": "dashboard"}`,
     `dry_run` false, parameters equal the query's; `world.client.kinds()` contains only `query`; exactly 4 queries on the cold
     run; 0 more on `at.run()` (cache hit, trap armed: proves no unpickling); exactly 1 more after selecting the second date, with
     `as_of_date` = that date; 0 more after re-selecting the first.
  3. for each of the 4 queries × {bytes_cap BadRequest at site "result", quota Forbidden at site "result", ServiceUnavailable at
     site "query", ServiceUnavailable at site "result"}, with "SECRET-123" in the exception message: the panel text for that
     query (§3 table) is present; the other views render (e.g. the call sheet still has its
     dataframe when monitoring fails); "SECRET-123" and the exception class name appear nowhere in any element's value/body;
     `at.exception` empty. Expected `at.warning` texts, exactly: bytes_cap / quota (no breaker): dates → 1; call sheet → 1;
     scoring runs → 1 (the scoring-run table); monitoring → 3 (heartbeat, monitoring tab, monitoring-run table) plus the
     scoring-run info "The simulated date could not be read…". Unreachable (S18 breaker, fetch order monitoring, scoring
     runs, dates, call sheet): monitoring → 5 (heartbeat, monitoring tab, monitoring-run table, scoring-run table, dates),
     and the fake records exactly 1 query; scoring runs → 2 (scoring-run table, dates), 2 queries recorded; dates → 1, 3
     queries; call sheet → 1, 4 queries. For monitoring failures the heartbeat reads "❔ **Heartbeat unavailable**"; for
     scoring-run failures it contains "data tests unknown". `expected_log` = the failing query's record plus, for
     unreachable, one "dashboard query <loader> skipped (BigQuery unreachable earlier in this run)" record (exc None) per
     skipped loader.
  4. empty: no published dates → the dates info panel; no monitoring rows (and, as the SQL would, no published dates) → "No
     monitoring tick yet", the monitoring tab's `MON_NO_BUNDLE_YET`, the dates info panel, and in the run history `NO_CLOCK_YET` for both
     tables (scoring runs exist in the world but are not shown); no scoring runs (clock present) → its "empty" info panel.
     Branch (4) of `_monitoring` (a monitored bundle but no point) is defensive (a succeeded tick always has a verdict,
     MON-02/`monitoring.sql:13`, and a sim_date): tested by patching `present.monitoring_points` to return an empty frame with
     its columns → "The monitoring record: nothing to show yet. The call sheet may still work."
  5. not cached: the dates query raises once, then succeeds: after the second `at.run()` the selectbox exists.
  6. freshness (fixture anchor: a Monday 00:17Z scheduled success): `wall_now` = anchor + 15 days → heading "Stale since <anchor
     date + 14 d>"; `late_after(anchor)` → "Late"; `late_after(anchor) - 1 µs` (11 d 23 h later) → "Fresh".
  7. `monkeypatch.setenv("READMISSION_RISK_GIT_COMMIT", "0123456789abcdef")` → "Image commit `0123456789ab`" in the footer.
  8. clock mismatch: the world's published `clock_sim_date` one week ahead of the monitoring clock → the caption appears.
  9. E7: (a) `present.call_sheet_frame` patched to raise `ValueError` → "The call sheet could not be displayed. The other views
     still work."; the monitoring chart still renders; `expected_log` = {("dashboard part The call sheet failed (ValueError)",
     ValueError): 1}. (b) `heartbeat_mod.heartbeat` patched to raise `ValueError` → "The heartbeat could not be displayed…", the
     call sheet still renders its dataframe, the monitoring tab shows `MON_NO_BUNDLE_UNKNOWN`, both run-history tables show
     `NO_CLOCK_UNKNOWN` (monitoring rows exist; state unknown); `expected_log` = {("dashboard part The heartbeat failed
     (ValueError)", ValueError): 1}. (c) `chrome.footer` patched to raise `ValueError` → "The footer could not be displayed…"
     and every tab renders; `expected_log` = {("dashboard part The footer failed (ValueError)", ValueError): 1}. Meta-test: `check_log` on records containing an
     `AssertionError` record reports a failure.
  10. the date list changes mid-session: after the first run, the world gains a newer published date 2023-11-06 (and its call
      sheet), a new monitoring row m6 (succeeded, weekly / schedule, sim_date 2023-11-06, bundle B, started_at 2026-11-02 00:17Z,
      run r6) and `world.sql_clock` = 2023-11-06, `wall_now` moves to 2026-11-03 12:00Z, and `st.cache_resource.clear()` is
      called (so no clock-mismatch caption: asserted absent); the next `at.run()` shows the new date as the picker's value and its rows. Then the
      user picks the second date; the world republishes that date: its published row gets a new `scoring_run_id` and
      `n_predictions` = 4, AND its call-sheet rows are replaced by 4 rows carrying that new `scoring_run_id` (as a real republish
      does); the cache is cleared again. The run dates are unchanged, so the options tuple is unchanged and Streamlit KEEPS the
      user's pick: the test asserts the picker's value is still the second date, no `sheet_warnings` caption appears (an
      outdated `PublishedDate` would produce both the row-count and the other-run warning) and the Top-N plus rest dataframes
      hold 4 rows together. (The query's parameters alone cannot
      tell the objects apart: they derive from `run_date` and `window_days` only.)
  11. `app.CACHE_TTL == timedelta(hours=3)`, `app.CACHE_MAX_ENTRIES == 64`; `importlib.reload(readmission_risk.dashboard.app)`
      issues no client call (this test opts out of the autouse client patch with `@pytest.mark.no_fake_world`, which the
      fixture checks via `request.node.get_closest_marker`, registered in `pyproject.toml`'s pytest markers; the conftest's
      refusing `gcp` factories stay in place); after the reload the logger
      has exactly one handler marked `_readmission_risk_dashboard` (no duplicate on re-import).
  12. empty Top-N: a date published with `top_n` = 0 whose rows all have `is_top_n` False (so no `sheet_warnings` caption:
      the case asserts there is none) → "Call this week: top 0", the caption "No discharge is flagged
      Top-N for this run date.", the rest expander with 5 rows; a date whose rows are all Top-N → no expander.
- **V-B (`tests/dashboard/test_import_boundary.py`, extended):** `_SOURCES` = the dashboard package files + `scripts/dashboard_app.py`,
  `assert len(_SOURCES) >= 8`; `_BANNED_MODULES` gains `matplotlib`, `readmission_risk.reporting.style`,
  `readmission_risk.monitoring.config`, `readmission_risk.monitoring.ledger`; the subprocess imports every dashboard module
  (incl. `app`, side-effect free) and `streamlit`, `altair`. Source bans (all compiled with `re.MULTILINE`, matched against the
  WHOLE file text, comments and docstrings included), next to the existing pickle, `to_dataframe/to_arrow` and `file_uploader`
  bans:
  - widgets: `r"\.(text_input|text_area|chat_input|number_input|date_input|datetime_input|time_input|file_uploader|camera_input|audio_input|color_picker|slider|select_slider|checkbox|toggle|radio|multiselect|pills|segmented_control|button|download_button|link_button|form|form_submit_button|feedback|data_editor)\("`
    (every Streamlit 1.66.1 input widget except `selectbox`, as a call on any container), plus `menu_button`, `pagination` and
    `popover` in the same alternation;
  - widget-making keywords and HTML/markdown routes: `r"\bon_(select|change|click)\s*="` (so `tabs`/`expander`, which accept
    `on_change`, stay inert layout), `r"\bbind\s*="` (URL-bound state would bypass the
    `query_params` ban), `r"\bcomponents\b"`, `r"\.iframe\("`, `r"\bst\.write(_stream)?\("` (renders arbitrary strings as
    markdown, bypassing §7's markdown safety);
  - a completeness check, two parts. (i) Every public callable of `streamlit` (`dir(streamlit)`) whose signature has an
    `on_change`, `on_click` or `on_submit` parameter, except `selectbox`, `tabs` and `expander`, is in the widget alternation
    (measured on 1.66.1: `on_submit` only on `chat_input`, already banned). (ii) The callables with an `on_select` parameter are
    exactly {`altair_chart`, `dataframe`, `plotly_chart`, `pydeck_chart`, `vega_lite_chart`} (measured on 1.66.1; display
    elements that `on_select=`'s ban keeps inert): a new one fails the test until classified. Part (i)'s measured list for
    `on_change`/`on_click`, except `selectbox`, `tabs` and `expander` (measured on 1.66.1: those plus `audio_input`, `button`,
    `camera_input`, `checkbox`, `color_picker`, `data_editor`, `date_input`, `datetime_input`, `download_button`, `feedback`,
    `file_uploader`, `form_submit_button`, `link_button`, `menu_button`, `multiselect`, `number_input`, `pagination`, `pills`,
    `popover`, `radio`, `segmented_control`, `select_slider`, `slider`, `text_area`, `text_input`, `time_input`, `toggle`), is in
    the widget alternation (so a widget added by a Streamlit bump fails the test
    until it is classified);
  - `r"\bquery_params\b"`, `r"\bcache_data\b"`, `r"\bst\.html\("`, `r"unsafe_allow_html"`, `r"\bst\.exception\("`,
    `r"^\s*(import|from)\s+traceback\b"`;
  - wall clock: `r"\b(datetime\.(now|utcnow|today|fromtimestamp|utcfromtimestamp)|date\.(today|fromtimestamp)|Timestamp\.(now|utcnow|today))\b"`
    must match exactly once over all sources, in `heartbeat.py`, on the line of `wall_now`'s body (S6); plus, with zero matches
    allowed: `r"^\s*(import\s+time\b|from\s+time\s+import)"` (no use of the `time` module at all: `time.time`, `time_ns`,
    `gmtime`, `localtime`, `monotonic`, …), `r"to_datetime\(\s*[\"'](now|today)"`, `r"\bnp\.datetime64\(\s*[\"'](now|today)"`, `r"Timestamp\(\s*[\"'](now|today)"`, and
    aliased imports that would hide a read: `r"^\s*import\s+datetime\s+as\b"`, `r"^\s*from\s+datetime\s+import\s+[^\n]*\bas\b"`,
    `r"^\s*import\s+pandas\s+as\s+(?!pd\b)"` (the alias rules make the name-based patterns sufficient for code written in
    the house style; a deliberate evasion is out of a source test's reach, recorded).
  A test that `scripts/dashboard_app.py` is exactly the two code lines.
- **Lockfiles (S17):** `tests/test_lockfiles.py` (extended as in Surfaces) passes for all four pairs, D8 included; the main
  pair is byte-unchanged (`git diff --exit-code requirements.in requirements.txt requirements-linux-extras.txt`); CI's
  `lint-and-test` (ubuntu-24.04, 3.14) on the PR proves the Linux `--require-hashes` install of the dashboard lockfile with
  watchdog from its extras; `pipeline-dataproc-runtime` and `audit-workflows` are unchanged.
- **V-Q (`tests/dashboard/test_queries.py`, extended; S16):** `run_bounded` passes `retry` with timeout 10 (its `_timeout`
  attribute), `timeout=10` and `job_retry=None` to `client.query`, and `retry` with timeout 20 and `timeout=20` to `job.result` (read from the fake's recorded kwargs);
  `job_config` is the same as `run`'s (bytes cap, labels, parameters); the app's four loaders call `run_bounded` (a source test:
  `app.py` contains no `queries.run(`).
- **Sanity check (offline):** the happy-path AppTest element tree dumped once and read by the elephant (recorded); no live run.

## Risks

| Risk | Mitigation |
|---|---|
| AppTest differs from the browser (client-side rendering) | S2 accepted; slice 4's smoke + manual pass |
| `allowedHosts` pins slice 4's service name/region; legacy host missing | E4; slice-4 test |
| Streamlit adds a Linux-only dependency we missed | CI's hash-checked Linux install fails loudly |
| A cached empty result hides a new tick for up to 3 h | documented; M4 allows it |
| The heartbeat compares the live wall clock with monitoring rows cached up to 3 h, so it can say Late/Stale for up to 3 h after a newer scheduled success | documented; M4 allows the lag; the Late threshold (S13) leaves days of margin in normal operation |
| One `bigquery.Client` (`gcp.bigquery_client()`'s module cache, lazy unlocked init) is shared by every Streamlit session thread | accepted: Cloud Run max-instances 1, demo traffic; a duplicate client from a racing init is harmless; a lock in `gcp.py` is a named follow-up if traffic grows |
| `import streamlit` sets `os.environ["MPLBACKEND"] = "Agg"` (`streamlit/__init__.py:56`) and loads the `google` namespace (protobuf) for the rest of the pytest process | harmless here: `reporting/` draws through `FigureCanvasAgg` explicitly; the README byte-identity check (V-P) runs the CLI recipes in their own processes; no test bans `google` for dashboard imports |
| `cache_resource` shares objects across sessions | values are immutable (tuples, MappingProxyType, frozen dataclasses); present.py never mutates (tested) |
| Production jobs pull dashboard dependencies | avoided by S17: only `make setup` and CI's `lint-and-test` install them |
| A `-c` constrained lockfile drifts from `requirements.txt` | D8 in `tests/test_lockfiles.py`; `make lock-dashboard` after every `make lock-main` (`lock-main`'s closing echo, exact text in the Makefile row) |

## Out-of-scope follow-ups

Slice 4: Dockerfile + FR20's image lockfile decision (S17: recompile this slice's `requirements-dashboard.*` inside
`python:3.14-slim`, plus google-cloud-bigquery, or a separate file; D8 already pins the shared versions), Cloud Run named `dashboard`
with both hosts in `allowedHosts` and the S9 test (E4), a live clock-parity check in `make dashboard-query-check` (§4),
`READMISSION_RISK_GIT_COMMIT` from the build, the live smoke, the manual accessibility
pass, the Synthea NOTICE check (PRD Open question 6), and S15's server-level verification items: `allowedHosts` (a request
with another Host header refused, the service hosts accepted), XSRF/CORS on, the redacted error page (no type/message/traceback),
`/_stcore/health` 200, the kept starlette/uvicorn pins under the real server, one look at the page and chart in light and dark
mode. Slice 5: the model card (E5: set `MODEL_CARD_URL`). Later: Playwright; a `make` target to run the
app against live BigQuery that impersonates `dashboard-runtime` (E9); materializing the reason rate; one shared ops epoch
constant.

## Implementation (2026-11-01)

Built as designed (revision 10), on branch `v3-slice3-dashboard-app` off `b46a940`. Implementation-level notes:
- **V-F capture.** The pre-refactor ledger outputs were captured at `b46a940` (before any edit) by the script below, run with
  `PYTHONPATH=. .venv/bin/python capture_ledger.py`; the six badge dicts and ledger sha256s are the literals in
  `tests/monitoring/test_freshness.py::CAPTURED`, and the same script re-run after the refactor gave byte-identical output.
  ```python
  """Captures last_tick_badge / render_ledger outputs of the PRE-refactor ledger (b46a940) for V-F's parity test."""
  import hashlib, json
  import pandas as pd
  from readmission_risk.monitoring.config import WEEKLY_CRON
  from readmission_risk.monitoring.ledger import last_tick_badge, render_ledger
  from tests.monitoring.test_ledger import RUNS, _tick
  from tests.monitoring.world import CONFIG
  
  FRAMES = {
      "empty": pd.DataFrame(),
      "burn_in_only": pd.DataFrame([_tick(1), _tick(2, started="2026-10-20T14:07:30Z")]),
      "weekly_success": pd.DataFrame([_tick(1, cadence="weekly", started="2026-10-26T04:44:00Z")]),
      "catch_up_only": pd.DataFrame([_tick(1, cadence="catch_up", trigger="laptop", github_run_url=None)]),
      "failed_newer": pd.DataFrame([_tick(1, cadence="weekly", started="2026-10-26T04:44:00Z"),
                                    _tick(2, cadence="weekly", status="failed", started="2026-10-27T05:17:00Z")]),
      "tie": pd.DataFrame([_tick(1, cadence="weekly", started="2026-10-26T04:44:00Z"),
                           _tick(2, cadence="weekly", started="2026-10-26T04:44:00Z")]),
  }
  out = {}
  for name, frame in FRAMES.items():
      badge = last_tick_badge(frame, cron=WEEKLY_CRON)
      text = render_ledger(frame, RUNS, config=CONFIG) if not frame.empty else None
      out[name] = {"badge": badge, "ledger_sha256": hashlib.sha256(text.encode()).hexdigest() if text else None}
  print(json.dumps(out, indent=1, ensure_ascii=False))
  ```
- **V-P.** After the palette move, both `make_readme_assets` recipes (v2 into `<scratchpad>/vp/v2`, v1 into `<scratchpad>/vp/v1`)
  reproduced `docs/img/v2/{reliability_light.svg,reliability_dark.svg,sample_topn.md}` and
  `docs/img/{reliability_light.svg,reliability_dark.svg,sample_topn.md}` byte for byte (`cmp`, owner's Mac).
- **S17 measured.** `make lock-dashboard` compiled 35 pins; every pin shared with `requirements.txt` has its version
  (D8 passes); watchdog absent on macOS. `requirements-dashboard-linux-extras.txt` holds the sha256 of all 30 files PyPI lists
  for watchdog 6.0.0. `git diff --exit-code requirements.in requirements.txt requirements-linux-extras.txt` is clean; only
  `ci.yml`'s `lint-and-test` gained the install line (zizmor offline: no findings).
- **Choices left to the implementer:** `CADENCE_LABELS` is not re-exported from `ledger.py` (nothing imports it from there);
  `reporting/style.py` no longer imports `dataclass`; the reporting package has 64 tests at `b46a940` too (CLAUDE.md's 66 was
  stale). AppTest's log guard reads `caplog.get_records("setup") + caplog.get_records("call")` (at teardown `caplog.records`
  holds only the teardown phase).
- **Offline sanity check:** the happy-path render against the fake world, read by the elephant: heartbeat
  "✅ **Fresh** · simulated date 2023-10-30 · last tick ran 2026-10-26 00:17 UTC (succeeded) · data tests passed · bundle
  `bbbbbbbbbbbb…`"; picker 2023-10-30 / 10-23 / 10-16; Top-N table of 2 with the withheld cell "withheld (n = 9 < 30)"; the
  muted-week caption "2 weeks have … · 1 week has no O/E value …"; "2 runs are not shown …", "1 tick is not shown …"; footer
  "Model bundle `bbbbbbbbbbbb…` · Gold `80edd13dcc96…` · Image commit unknown …"; 4 queries in the designed order, 0 on a rerun.
- **Gate:** `ruff check .` clean; `pytest` 2,170 passed, 2 skipped (after the pre-commit review) (from 2,031 / 2 at `b46a940`; the skips: the Dataproc-runtime
  environment check and the off-macOS lock-main guard).

## Pre-commit review (2026-11-01)

`/eg-precommit-review`: 4 rounds, 4 findings fixed, 0 rebutted; round 4 returned `no findings`.
- Round 1: (1) the run history hid every failed weekly attempt (dated clock + 7, since the clock moves only on success): the
  owner refined S12 (the clock bound applies only to published runs and succeeded ticks), with
  `tests/dashboard/test_present.py::test_failed_weekly_attempts_at_the_next_week_stay_visible`; (2) CLAUDE.md's install line
  lacked the dashboard set.
- Round 2: this note's Interfaces §5 still stated the old S12 rule.
- Round 3: the S12 row and V-R still described the old `clock=None` rule; this hit the early-stop rule (next to a fixed line)
  and the owner chose "keep working"; the `clock=None` keep assertions were added.
