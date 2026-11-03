# v3 slice 4: container, Cloud Run deploy and cost guard for the dashboard (PRD FR20-FR24)

**Status:** design revision 6 (2026-11-03, `/eg-new-feature`), the binding text. Design-check log: round 1: comprehension passed, critic 20 gaps,
readiness 14 questions (→ revision 2; no new owner decision; F10-F14 added). Round 2: critic 14 gaps, readiness 6 questions (→
revision 3; F15-F18 added; no new owner decision). Round 3: critic 9 gaps, readiness 6 questions (→ revision 4; F17 corrected to
US Pacific months, F19-F20 added, a separate trigger SA; no new owner decision). Round 4: readiness CLOSED, critic 4 gaps; at the
3-revision cap the owner chose "fix all, re-run B/C once" (→ revision 5: the `error` outcome + P13, the import-check rule,
`_grants` keys, `main.py` import block). Round 5: readiness CLOSED again, critic 5 gaps; owner: "fix all, then implement"
(→ revision 6: lazy zone + P13 at L9, IC6 imports the lazy google modules, P14, `_grants` key order, the `error` outcome's code
shape). **Gate note:** readiness closed in rounds 4 and 5; the critic never closed; revision 6 was not re-reviewed (owner override,
slice 2/3 precedent); the pre-commit review checks the code. Branch `v3-slice4-cloud-run-deploy` off `v3`
(`32b16b5`); PR into `v3` (ruleset 24876876: PR required, 0 approvals; merge commit, not squash). **A LIVE slice:** the offline
build (code, Terraform files, workflows, tests) comes first; Terraform applies, GCP calls and workflow dispatches happen only in the
owner-confirmed live runbook (section "Live runbook"), after this design check closes, the pre-commit review passes, and the owner
confirms each step. PRD: `notes/prds/v3-cloud-run-call-sheet-bigquery-gold-2026-10-28.md` (V1, V12, V14, V15, V18; FR20-FR24; NFR
cost, performance, degraded modes; M1, M4-M7; Risks; Implementation hints slice 4; Open questions 3, 4, 6). Binding: slice 2's
D1-D5 (`v3-slice2-authorized-views-2026-10-31.md`), slice 3's S1-S18 and E3-E10 (`v3-slice3-dashboard-app-2026-11-01.md`). The PRD
wins over CLAUDE.md; this note records owner decisions where it differs from the PRD.

## Owner decisions (2026-11-02/03, scope confirm)

| # | Decision | PRD effect |
|---|---|---|
| T1 | **`v3` merges into `main` during this slice, and `v3` is kept.** After this slice's PR merges into `v3` and CI is green there, PR #40 (`v3` → `main`) is merged with a merge commit, before the first `deploy.yml` dispatch (the WIF provider accepts only `refs/heads/main`, and a workflow on `main` builds `main`'s code, so "only the workflow on `main`" cannot deploy this slice's Dockerfile and app). Terraform applies may run from the slice branch before that merge (v2 precedent). Slice 5 continues on `v3`; a second `v3` → `main` PR follows at the v3.0 release. The merge moves L16 to (merge date + 50 days): CLAUDE.md and the two notes that carry it (`v2-slice7-monitoring-tick-ledger-2026-10-18.md`, `monitoring-burn-in-timer-and-weekly-retry-2026-10-20.md`) are updated in the L-step that merges (L3). | executes the PRD's "decide when to merge" hint |
| T2 | **FR20 = the existing dashboard pair, recompiled on Linux.** `make lock-dashboard` runs pip-compile inside the Dockerfile's own base image (`linux/amd64`, by digest); `requirements-dashboard.in` gains `google-cloud-bigquery==3.46.1` (kept equal to `requirements.txt` by `-c` + D8); pandas stays 2.3.3 through the constraint (PRD Open question 3: parity, answered); watchdog is resolved into the lockfile itself (with the marker of F10), so `requirements-dashboard-linux-extras.txt` is deleted. The image installs only this lockfile; the package runs from source (F2). A new offline CI job proves the image holds only this set. | answers Open question 3 |
| T3 | **Kill switch = a Cloud Run function (gen2) with standard-library code**, triggered by the dashboard budget's Pub/Sub topic through Eventarc; it sets the service to manual scaling with 0 instances (F1). The function's `requirements.txt` holds no requirement: the buildpack's own `functions-framework` is the only third-party package, accepted as a platform component. Rejected: a Pub/Sub push to an own-image Cloud Run service (a second image and deploy path), and Google's native Cloud Run spend cap (not in Terraform provider 8.8.1, no Pub/Sub, so M6's synthetic-message test is impossible; deviates from V18). | answers Open question 4 (mechanism) |
| T4 | **A terminology credit in the footer.** Synthea's NOTICE (read 2026-11-02: "Synthea / Copyright 2017-2025 The MITRE Corporation", then RxNorm, LOINC, SNOMED CT ("constitutes a fair use … for the purposes of research and nonprofit educational purposes …"), SBSCL) covers the terminologies inside the generated data; Apache-2.0 §4(d) attaches to redistributing the Work, not to its output. The app shows SNOMED CT admission-reason descriptions, so the footer gains one line crediting SNOMED CT as distributed with Synthea, linking the NOTICE. Not legal advice; it records the check. | closes the "Synthea NOTICE" item of Open question 6 |

## Elephant resolutions (not owner decisions; flagged)

- **F1 (FR24's "max-instances 0" is impossible).** Cloud Run accepts max instances "from 1 to the maximum limit"
  (docs.cloud.google.com/run/docs/configuring/max-instances). The documented way to disable a service is manual scaling: "Specify a
  value of `0` to disable the service"; in-flight requests finish, new ones get "Service unavailable", and changing the manual
  instance count "does *not* create a new revision" (…/configuring/services/manual-scaling). The kill PATCHes the service's whole
  top-level `scaling` object (update mask `scaling`) to `{"scalingMode": "MANUAL", "manualInstanceCount": 0}`, which also clears
  `minInstanceCount`/`maxInstanceCount` (so the API never sees MANUAL combined with an automatic max); re-enabling restores them
  through Terraform (§9). FR24's intent (only the dashboard goes offline) is kept.
- **F2 (`pip install .` is incompatible with `--require-hashes`).** pip refuses to hash-check a local directory, and building the
  package needs setuptools from an unhashed build-isolation download (`python:3.14-slim` ships no setuptools). The image copies
  `src/readmission_risk/` and sets `PYTHONPATH=/app/src` (the tree `pip install -e .` exposes; package data such as
  `warehouse/sql/views/*.sql` is read through `importlib.resources`, which works from source). Deviation from FR21's "installs the
  package", same effect, nothing unhashed enters the image.
- **F3 (every open tab bills at the instance-based rate).** "A Cloud Run instance that has any open WebSocket connection is
  considered active, so CPU is allocated and the service is billed as instance-based billing"; connections are cut at the request
  timeout and Streamlit's client reconnects (…/triggering/websockets). So one forgotten tab keeps the single instance billed around
  the clock: 1 vCPU × $0.000018/s + 0.5 GiB × $0.000002/s ≈ **$0.0000190/s ≈ $1.64/day**; the instance-based free tier (240,000
  vCPU-s, 450,000 GiB-s per billing account and month) absorbs ≈ 2.8 days. The request timeout does not bound this (reconnects);
  the kill switch (CHF 3) does. Normal use (owner + a few reviewers, ≪ 50 tab-hours a month) stays in the free tier.
- **F4 (billing sees only revision-template labels).** "Cloud Billing and Cloud Logging only receive labels that are specified in"
  the revision template (…/configuring/labels). The service carries `component = dashboard` in BOTH `labels` and `template.labels`;
  the budget filters on it. Whether internet-egress line items carry the label is undocumented: measured in L11; if egress is not
  covered, that is recorded (egress ≤ 1 GiB/month is free within North America) and not fixed in this slice.
- **F5 (`allowedHosts` checks WebSockets only; a foreign Host cannot reach the container through Cloud Run).** In Streamlit 1.66.1
  `_is_host_allowed` is called only from `web/server/starlette/starlette_websocket.py` (`_is_origin_allowed`, line 148), which closes
  with code 1008 before accepting; HTTP routes are not host-checked. Google's frontend routes a request by its Host header, so a
  request with a foreign Host never reaches our service. Hence the "foreign Host refused" check (S15) runs against the image
  locally (CI job `dashboard-image`, SC5), and the live check proves both service hosts ARE accepted (SC3, L5).
- **F6 (the legacy host).** The non-deterministic `dashboard-<hash>-uc.a.run.app` URL exists for every service and cannot be
  disabled alone (`default_uri_disabled` disables all run.app URLs). It becomes known at L1 from the new Terraform output
  `dashboard_urls`; L2 adds it to `allowedHosts`, `SERVICE_HOSTS`, `deploy.yml` and `ci.yml` in this PR, before the PR merges (the
  image is built only after the merge, T1).
- **F7 (Python patch).** `python:3.14-slim` is now 3.14.10 (Debian trixie, 2026-10-25); the laptop and CI run 3.14.7 / setup-python
  "3.14". The image pins `python:3.14.10-slim-trixie@sha256:<index digest>`; guard 14 and the lockfile header compare major.minor
  only. Bumping `.python-version` is a follow-up.
- **F8 (re-enabling within the month is futile without a budget change).** Budget messages arrive "multiple times per day" and the
  function kills whenever `costAmount >= budgetAmount`, so a same-month re-enable is undone within hours. The runbook (§9)
  re-enables in the next calendar month or after raising `var.dashboard_budget_chf`.
- **F9 (Terraform shows the kill as drift).** Terraform owns `scaling` (AUTOMATIC, min 0, max 1). While killed, `terraform plan`
  shows exactly that diff; applying it IS the re-enable. Every apply is owner-confirmed after reading the plan, so an accidental
  re-enable needs the owner to accept that diff. `scaling` is NOT in `ignore_changes` (M5).
- **F10 (watchdog has no macOS wheel for Python 3.14).** PyPI's watchdog 6.0.0 files (checked 2026-11-03): macOS wheels for
  cp39-cp313 and PyPy only, plus `py3-none-manylinux*` wheels. A Linux compile writes `watchdog==6.0.0` with no marker, so `make
  setup` on macOS 3.14 would build it from the sdist (unhashed setuptools, a C toolchain). `make lock-dashboard` therefore appends
  `; platform_system == "Linux"` to the watchdog pin line after pip-compile (one GNU `sed` inside the container; Streamlit requires
  watchdog only off Darwin, so macOS needs it not at all). `tests/test_lockfiles.py` asserts that watchdog is the only marked pin of
  the dashboard lockfile and carries exactly that marker. And `make setup` / CI's `lint-and-test` install the dashboard lockfile with
  `--only-binary=:all:`, so any future pin without a wheel for the installing platform fails loudly instead of building.
- **F11 (Streamlit's external-IP lookup).** With `server.headless = true` and a wildcard `server.address`, Streamlit's startup
  banner calls `net_util.get_external_ip()` (an HTTP request to checkip.amazonaws.com, 1 s timeout, then HTTPS; `web/bootstrap.py`
  lines 325-326) on every server start: an outbound call on every cold start and CI start. `logger.hideWelcomeMessage = true` makes
  `_print_url` return first (`web/bootstrap.py:286`), so it is added to `.streamlit/config.toml` (and to the exact-config test).
  Residual, accepted and recorded: a WebSocket from a FOREIGN Origin still runs `is_url_from_allowed_origins`
  (`web/server/server_util.py:72-89`), which may call `get_external_ip()` before refusing; it caches a success, and the request is
  refused either way.
- **F12 (Artifact Registry keeps the 5 newest, not 2).** With immutable tags and pushes that precede a possibly failing deploy, two
  failed deploys would make the served image the third newest, and "keep 2" would let the daily cleanup delete it (whether a cold
  start can pull a deleted digest is undocumented). `keep_count = 5`: ≤ ~1 GB, at most about $0.05/month above the 0.5 GiB free
  tier. Deviation from FR22's "keeping the 2 most recent images". The `functions` repo has MUTABLE tags (the buildpack re-pushes
  fixed tags on each rebuild; unverified, so the safe setting) and the same cleanup pair.
- **F13 (rollback is a redeploy, not a traffic change).** `gcloud run services update-traffic` would change the Terraform-owned
  `traffic` block (M5 drift, silently reverted by the next apply). A rollback is `gcloud run deploy dashboard --region us-central1
  --image <previous digest> --quiet` by the operator (or a future `deploy.yml` input), which keeps traffic on the latest revision.
- **F14 (`main`'s scheduled jobs after T1).** Merging `v3` puts slices 1-3's code under `main`'s weekly `monitor.yml` (which now
  imports `monitoring/freshness.py`, E3) and the ledger job. Pre-merge checks in L3: PR #40's CI green on the merge commit, and
  `make monitor-simulate` (offline) on the merged tree. Rollback if a scheduled tick breaks after the merge: a PR reverting the merge
  commit (`git revert -m 1 <merge>`), owner-confirmed. L12 is the observation.
- **F15 (`gcloud run deploy` writes template metadata; measured in the local gcloud SDK source).**
  `googlecloudsdk/command_lib/run/serverless_operations.py` (`_NewRevisionNonceChange`) sets the template label
  `client.knative.dev/nonce` on every deploy without `--revision-suffix`/`--tag`, and `command_lib/run/config_changes.py:668-680`
  sets the annotations `run.googleapis.com/client-name` and `run.googleapis.com/client-version` on the service and the template.
  Provider 8.8.1 treats `template.labels`/`template.annotations` as Optional, not computed, so a plan would propose removing them.
  `ignore_changes` therefore names exactly those map keys (never the whole `template[0].labels`, which carries F4's billing label):
  `template[0].labels["client.knative.dev/nonce"]`, `template[0].annotations["run.googleapis.com/client-name"]`,
  `template[0].annotations["run.googleapis.com/client-version"]`, in addition to the image and `client`/`client_version`. P6 stays
  for anything else L6 shows.
- **F16 (no repository facts beyond what is already public).** `infra/variables.tf` keeps the billing account id out of the repo.
  The budget's resource name (`billingAccounts/<account>/budgets/<id>`), which the `dashboard_budget_name` output and every kill
  log line carry, is recorded in Results and CLAUDE.md only as `billingAccounts/<redacted>/budgets/…<last 4 of the id>`; log lines
  quoted there are redacted the same way.
- **F17 (only the current budget month's cost kills).** Cloud Billing budget months start at midnight US Pacific, so a real
  `costIntervalStart` reads e.g. `2026-11-01T07:00:00Z` (PDT) or `2026-12-01T08:00:00Z` (PST). A message whose `costIntervalStart`,
  converted to `America/Los_Angeles`, is not in the same year-month as `now` converted to `America/Los_Angeles` (a late message for
  the previous month, arriving after a re-enable on the 1st) yields `stale-period`, never a kill. "The 1st" in §9 means the 1st
  in US Pacific time.
- **F19 (the deploy smoke proves the server, not a render).** SC1-SC4 never run the app script (by design: no BigQuery from CI or
  smoke). A future deploy whose app fails on its first render (a lazily imported module missing from the lockfile, a view
  permission regression) passes Smoke; IC6 imports the app AND the modules it imports lazily (the google
  tree) at build time, so a module missing from the lockfile fails the build; L7 renders this deploy by hand. Recorded as a
  known limit of `deploy.yml`. Streamlit's `server.scriptHealthCheckEnabled` route was considered and not adopted: the app
  catches its own failures (FR16 panels, E7), so the route would answer 200 regardless while running real queries.
- **F20 (two identities for the function, as FR24 reads).** FR24 gives `dashboard-kill` "`run.developer` on the service only". So
  the Eventarc trigger runs as a separate SA `dashboard-kill-trigger` holding only `run.invoker` on the function's own Cloud Run
  service; `dashboard-kill` holds only `run.developer` on the dashboard.
- **F18 (outbound lookups in the checks).** A foreign-Origin WebSocket makes Streamlit call `net_util.get_external_ip()`
  (checkip.amazonaws.com, 1 s HTTP + 1 s HTTPS, a failure not cached) before refusing (F11's residual). So SC4/SC5 cause one such
  call per request in V-SC (pytest), the `dashboard-image` job and the live checks: not Google Cloud, but internet; in a sandbox
  without network each costs ≈ 2 s, inside the 30 s/60 s timeouts. Recorded, accepted: the refusal is the property under test.

## Why

The app exists and is tested offline (slice 3), but nothing serves it. The PRD's V1 replaces Community Cloud with keyless Cloud
Run; V15 makes Terraform own the service and CI only swap the image; V18 needs a guard that takes only the dashboard offline if
traffic (or a forgotten tab, F3) runs up the bill. This slice makes M1 (a live URL serving three views) and M4-M7 measurable.

## Scope

**In:** FR20 (T2), FR21 (Dockerfile, `.dockerignore`), FR22 (Artifact Registry `dashboard` + `functions`, Cloud Run v2 `dashboard`),
FR23 (`deploy.yml`, SA `dashboard-deploy`), FR24 (budget, topic, function `dashboard-kill`, SAs `dashboard-kill`,
`dashboard-kill-trigger` and `function-builder`, re-enable runbook); `READMISSION_RISK_GIT_COMMIT` from the build; the offline CI job `dashboard-image`; the
stdlib server check `scripts/dashboard_server_check.py` (local, CI and live); slice 3's inherited items (S15 server checks, the
legacy host + S9 test, the live clock-parity check Q5 in `make dashboard-query-check`, the manual accessibility pass, the Synthea
NOTICE check, T4); the docs needed to operate it (`docs/setup.md` > "Dashboard on Cloud Run (v3 slice 4)", plus the stale install
lines); CLAUDE.md lines; the live runbook L0-L12 with Results.

**Out:** model card, README tour, docs pass, guard-8, pip-audit / Trivy / Dependabot `docker` (slice 5, FR25-FR28); Playwright (S2);
label v2; as-of scoring; a custom domain or load balancer; Cloud Armor; bumping `.python-version` to 3.14.10 (F7); a `make` target
that runs the app locally impersonating `dashboard-runtime` (slice 3's E9 follow-up); a rollback input on `deploy.yml` (F13).

## Surfaces touched

| File | Change |
|---|---|
| `requirements-dashboard.in` | + `google-cloud-bigquery==3.46.1`; header comment rewritten (Linux compile, T2, F10) |
| `requirements-dashboard.txt` | recompiled by the new `make lock-dashboard` (Linux) |
| `requirements-dashboard-linux-extras.txt` | **deleted** |
| `Makefile` | `lock-dashboard` rewritten (Docker, §1); `setup`'s third install line (§1); new variables `DASHBOARD_BASE_IMAGE`, `DASHBOARD_IMAGE`, `DASHBOARD_URLS`, `KILL_COST`; new targets `dashboard-image`, `dashboard-image-check`, `dashboard-server-check`, `dashboard-kill-test` (§5); `.PHONY` |
| `Dockerfile`, `.dockerignore` | new (repo root, §2) |
| `.gitignore` | + `infra/.build/` |
| `scripts/dashboard_server_check.py` | new, stdlib only, Python ≥ 3.12 (§4) |
| `scripts/dashboard_image_check.py` | new, stdlib only, runs INSIDE the image (§3) |
| `scripts/publish_budget_message.py` | new, stdlib + `gcloud` subprocess, Python ≥ 3.12 (§8); not named `*_test.py`, which pytest would collect |
| `infra/functions/dashboard_kill/{main.py,kill.py,requirements.txt}` | new: the function (§8) |
| `infra/apis.tf` | + 7 services |
| `infra/versions.tf`, `infra/.terraform.lock.hcl` | + `hashicorp/archive` 2.8.1 (hashes linux_amd64 + darwin_arm64, `terraform providers lock -platform=linux_amd64 -platform=darwin_arm64`) |
| `infra/dashboard.tf` | new: repos `dashboard` + `functions`, Cloud Run service, topic, budget, source archive + object, function (§6) |
| `infra/iam.tf` | + locals `deploy_workflow_ref`/`deploy_principal_set` (next to the existing `*_workflow_ref` locals); + SAs `dashboard-deploy`, `dashboard-kill`, `dashboard-kill-trigger`, `function-builder` and 9 `_member` bindings (§6). The `dashboard_runtime` resource is NOT edited (its description already names "the Cloud Run service identity from slice 4"), but it gains a binding ON it (`dashboard-deploy`'s `serviceAccountUser`) |
| `infra/dashboard.tf` (also) | + `google_pubsub_topic_iam_member` publisher for Billing's budget-alert account (§6; the 10th new member) |
| `infra/variables.tf`, `infra/outputs.tf` | + `dashboard_budget_chf` (number, default 3, validated: whole number 1-100); + outputs `dashboard_urls`, `dashboard_image_repository`, `dashboard_budget_name`, `dashboard_budget_chf` |
| `infra/budgets.tf` | header comment: "the Pub/Sub billing hard stop was declined in PRD OQ4" becomes "… in the v2 PRD's OQ4; v3's V18 dashboard-only kill switch lives in dashboard.tf" |
| `.github/workflows/deploy.yml` | new (§7) |
| `.github/workflows/ci.yml` | `lint-and-test`: the dashboard install line drops the extras file and gains `--only-binary=:all:`; its pip `cache-dependency-path` drops the extras file; new job `dashboard-image` (§5) |
| `.streamlit/config.toml` | + `[logger] hideWelcomeMessage = true` (F11); L2 adds the legacy host |
| `src/readmission_risk/dashboard/chrome.py` | + `SYNTHEA_NOTICE_URL`, `TERMINOLOGY_CREDIT`, a fifth footer part (§10) |
| `src/readmission_risk/warehouse/dashboard_query_check.py` | + check Q5 (§11) |
| `tests/scripts/stdlib_loader.py` | `load_isolated(name, tmp_path, *, directory=ROOT / "scripts")`, which now copies into a per-name directory `tmp_path / "isolated" / name` (so one test can load two modules, as V-KT does; existing callers unaffected); `imported_modules(name, *, directory=ROOT / "scripts")` (existing callers unchanged) |
| tests (changed) | `tests/test_lockfiles.py` (§1), `tests/test_tooling.py` (§1, §5: `EXPECTED_TARGETS` +4, `MAKEFILE_VARIABLES` +4, the `lock-dashboard` dry-run assertions, a new `make setup` dry-run assertion), `tests/test_infra.py` (V-T; `test_iam_is_additive_only` 50 → 60 members; `test_dashboard_runtime_grants_are_exactly` (`tests/test_infra.py:568-579`) gains `dashboard-deploy`'s `serviceAccountUser` binding ON the runtime SA, and P1 would add one more), `tests/dashboard/test_streamlit_config.py` (§12, F11), `tests/dashboard/test_chrome.py` (§10), `tests/warehouse/test_dashboard_query_check.py` (§11) |
| tests (new) | `tests/service_hosts.py`, `tests/test_container.py`, `tests/scripts/test_dashboard_server_check.py`, `tests/scripts/test_dashboard_image_check.py`, `tests/scripts/test_publish_budget_message.py`, `tests/functions/__init__.py`, `tests/functions/test_dashboard_kill.py` |
| docs | `docs/setup.md`: new section "Dashboard on Cloud Run (v3 slice 4)" before "Teardown checklist" (which gains: function, budget, topic, service with `deletion_protection` set false first, the two repos, the four SAs); install block at line 18 and the `lock-dashboard` row at line 54 updated; `README.md:180` install line updated; CLAUDE.md (v3 architecture App/deploy bullet, Build & test commands, install line, test count) |

## Interfaces

### 1. Lockfile (T2, F10)

`requirements-dashboard.in` (after the comment header):
```
-c requirements.txt
streamlit==1.66.1
altair==6.3.0
google-cloud-bigquery==3.46.1
```
`Makefile`:
```make
# The dashboard image's base, read from the Dockerfile's FROM line, so the lockfile is compiled in exactly that image.
DASHBOARD_BASE_IMAGE := $(shell sed -n 's/^FROM \(python:[^ ]*@sha256:[0-9a-f]\{64\}\)$$/\1/p' Dockerfile)

lock-dashboard: ## Recompile requirements-dashboard.txt inside the image's base (linux/amd64, Python 3.14), keeping existing pins; needs Docker
	@test -n "$(DASHBOARD_BASE_IMAGE)" || { echo "ERROR: no digest-pinned FROM line in Dockerfile" >&2; exit 1; }
	docker run --rm --platform linux/amd64 --user "$$(id -u):$$(id -g)" -e HOME=/tmp -v "$(CURDIR)":/src -w /src \
	  $(DASHBOARD_BASE_IMAGE) sh -euc '\
	    python -m venv /tmp/lock && /tmp/lock/bin/pip install --require-hashes -r requirements-dev.txt; \
	    /tmp/lock/bin/python -c "import sys; print(\"pip-compile interpreter:\", sys.version.split()[0], sys.platform)"; \
	    /tmp/lock/bin/pip-compile --generate-hashes --output-file=requirements-dashboard.txt requirements-dashboard.in; \
	    sed -i "s/^watchdog==\([^ ]*\) \\\\$$/watchdog==\1 ; platform_system == \"Linux\" \\\\/" requirements-dashboard.txt'
	@grep -q '^watchdog==.* ; platform_system == "Linux" \\$$' requirements-dashboard.txt || { echo "ERROR: watchdog marker not applied (F10)" >&2; exit 1; }
```
(The exact shell quoting is the implementer's; the contract is: pip-compile under the base image's Python 3.14 on linux/amd64,
then exactly the watchdog pin line gains the Linux marker, and the recipe fails if it did not.) pip-compile keeps existing pins of
the output file, so shared versions stay; pip-tools records every file hash PyPI lists, so the file installs on macOS. `make
setup`'s third line becomes `$(BIN)/pip install --require-hashes --only-binary=:all: -r requirements-dashboard.txt`; CI's
`lint-and-test` install line: `pip install --require-hashes --only-binary=:all: -r requirements-dashboard.txt`.
`tests/test_lockfiles.py`: the dashboard `LockPair` is unchanged (header Python 3.14, the same command string);
`LINUX_EXTRAS_PAIRS` drops the dashboard pair; new tests: (a) `test_dashboard_lockfile_is_linux_compiled`: it pins `watchdog`, the
watchdog line ends with `; platform_system == "Linux" \`, no other pin line has a marker, and
`requirements-dashboard-linux-extras.txt` does not exist; (b) `test_dashboard_lockfile_meets_fr20`: `uvicorn >= 0.52` and
`pyarrow != 25.0.0` (packaging `Version`), and it pins `google-cloud-bigquery`. D8 then covers google-cloud-bigquery's tree.
`tests/test_tooling.py`'s `lock-dashboard` dry-run asserts the new recipe: `docker run --rm --platform linux/amd64`, the
`DASHBOARD_BASE_IMAGE` value (equal to the Dockerfile's FROM reference), `pip-compile --generate-hashes
--output-file=requirements-dashboard.txt requirements-dashboard.in`, the watchdog `sed`, no `--upgrade`, no `uname`/Darwin guard.
A new `test_setup_installs_the_dashboard_set_binary_only` dry-runs `make setup` and asserts the line `pip install
--require-hashes --only-binary=:all: -r requirements-dashboard.txt` and that `requirements-dashboard-linux-extras.txt` appears
nowhere in the recipe. `test_co_installed_lockfiles_agree_on_shared_pins` is unchanged and must stay green (pip-compile keeps pins; if a shared pin with
`requirements-dev.txt` diverged, that is a defect fixed before commit by recompiling, never by editing the test).

### 2. `Dockerfile` and `.dockerignore` (repo root)

```dockerfile
# The dashboard image (v3 slice 4, PRD FR21; notes/eg-new-feature/v3-slice4-cloud-run-deploy-2026-11-03.md). Base pinned by tag AND
# index digest; `make lock-dashboard` compiles requirements-dashboard.txt inside this same image.
FROM python:3.14.10-slim-trixie@sha256:<index digest, fetched at implementation with `docker buildx imagetools inspect python:3.14.10-slim-trixie`>

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
RUN useradd --system --uid 10001 --create-home --home-dir /home/app app
WORKDIR /app
COPY requirements-dashboard.txt ./
RUN pip install --require-hashes --only-binary=:all: --no-deps -r requirements-dashboard.txt
COPY src/readmission_risk ./src/readmission_risk
COPY scripts/dashboard_app.py scripts/dashboard_image_check.py ./scripts/
COPY .streamlit/config.toml ./.streamlit/config.toml
ARG GIT_COMMIT
ENV PYTHONPATH=/app/src READMISSION_RISK_GIT_COMMIT=${GIT_COMMIT}
USER 10001
EXPOSE 8080
CMD ["sh", "-c", "exec streamlit run scripts/dashboard_app.py --server.port=${PORT:-8080} --server.address=0.0.0.0"]
```
No `# syntax=` directive (it would pull an unpinned frontend image). `--only-binary=:all:` forbids sdist builds; if a pin has no
linux/amd64 wheel the CI build fails loudly and the design is revisited, never loosened silently. `--no-deps` makes an incomplete
lock fail. The copied files are root-owned and read-only to uid 10001. `GIT_COMMIT` is the full 40-hex SHA; `chrome.git_commit`
accepts 7-40 hex, so a missing or malformed value shows "Image commit unknown".

`.dockerignore`, exactly:
```
*
!requirements-dashboard.txt
!src/readmission_risk/**
!scripts/dashboard_app.py
!scripts/dashboard_image_check.py
!.streamlit/config.toml
**/__pycache__
**/*.pyc
```

**Single-manifest images (both builds).** Every `docker build` of this image (CI's `dashboard-image`, `deploy.yml`, `make
dashboard-image`) passes `--provenance=false --sbom=false`, so a push is ONE platform manifest (no attestation index that
Artifact Registry would list as extra versions and the cleanup policy could split), `RepoDigests[0]` is that manifest's digest, and
it is what Cloud Run resolves. V-C and V-T assert the flags.

### 3. `scripts/dashboard_image_check.py` (stdlib: `__future__`, `importlib.metadata`, `json`, `os`, `pathlib`, `re`, `subprocess`, `sys`; inside the image: `docker run --rm IMAGE python -I scripts/dashboard_image_check.py`)

`main() -> int`: one line per check `IC<n> pass|fail <detail>`; returns 0 iff all pass, else 1. Pure helpers (unit-tested):
`lock_pins(text) -> dict[str, str]` (canonical name → version, from lines matching `^([A-Za-z0-9_.-]+)==([^ ;\\]+)`) and
`compare(installed: dict, pins: dict, *, ignore=("pip",)) -> list[str]` (missing, extra, version mismatches). The pin regex is
`^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^\s;\\]+)` (extras stripped, as `tests/test_lockfiles.py`'s `PIN_RE`): google-cloud-bigquery
brings `google-api-core[grpc]` and `google-auth[pyopenssl]` pin lines into the dashboard lockfile.
- **IC1** `sys.version_info[:2] == (3, 14)`.
- **IC2** `os.getuid() != 0`.
- **IC3** `compare({canonical(d.metadata["Name"]): d.version for d in importlib.metadata.distributions()}, lock_pins(...)) == []`
  (canonical = lowercase, runs of `-_.` → `-`).
- **IC4** none of `pyspark, mlflow, scikit-learn, xgboost, cloudpickle, scipy, matplotlib, typer, google-cloud-storage` is installed.
- **IC5** `[sys.executable, "-m", "pip", "check"]` exits 0.
- **IC6** a child `[sys.executable, "-c", <code>]` (environment inherited, so `PYTHONPATH` applies) that imports
  `readmission_risk.dashboard.app` AND every module the app imports lazily at run time: `google.auth`,
  `google.auth.impersonated_credentials`, `google.auth.exceptions`, `google.oauth2.credentials` (`gcp.py:43-46`),
  `google.cloud.bigquery` (`gcp.py:83`, `queries.py:192`), `google.cloud.bigquery.retry` (`queries.py:225`),
  `google.api_core.exceptions` (`errors.py:49`), then prints `json.dumps(sorted(m for m in
  ('pyspark','mlflow','sklearn','xgboost','cloudpickle') if m in sys.modules))`; it must exit 0 and print exactly `[]`. The list is
  a constant `LAZY_IMPORTS` in the script; `tests/scripts/test_dashboard_image_check.py` greps `src/readmission_risk/dashboard/`
  and `gcp.py` for function-level `from google…`/`import google…` statements and asserts each module is in `LAZY_IMPORTS`.
- **IC7** `.streamlit/config.toml` exists in the working directory and `READMISSION_RISK_GIT_COMMIT` matches `[0-9a-f]{40}`.

### 4. `scripts/dashboard_server_check.py` (stdlib: `__future__`, `argparse`, `base64`, `http.client`, `os`, `socket`, `ssl`, `sys`, `urllib.parse`; Python ≥ 3.12)

CLI: `python scripts/dashboard_server_check.py --url URL [--url URL ...] [--connect HOST:PORT] [--foreign-host-check]`. Each `--url`
is a base URL (`https://<host>` or `http://<host>`); its netloc is the Host header. `--connect` sends every request to that address
(plain HTTP) while keeping each URL's Host header. Pure helpers (unit-tested), each returning `(passed: bool, detail: str)`:
`upgrade_headers(host, origin, key) -> dict`; `check_health(status, headers, body: bytes)` (SC1); `check_cors(headers)` (SC2);
`check_upgrade(status, *, expect_101: bool)` (SC3 with True; SC4/SC5 with False). `headers` is a list of `(name, value)` pairs
(case-insensitive lookups; several `Set-Cookie` headers allowed).
- Per URL: **SC1** `GET /_stcore/health` → 200, body `ok`, header `Cache-Control: no-cache`, a `Set-Cookie` header starting
  `_streamlit_xsrf=`. **SC2** `GET /_stcore/health` with `Origin: https://evil.example` → no `Access-Control-Allow-Origin` header.
  **SC3** WebSocket upgrade `GET /_stcore/stream` with `Upgrade: websocket`, `Connection: Upgrade`, `Sec-WebSocket-Version: 13`,
  `Sec-WebSocket-Key: base64(os.urandom(16))`, `Sec-WebSocket-Protocol: streamlit`, `Origin: <the URL's scheme://netloc>` → status
  101; the socket is then closed without sending a frame (no session runs the app script, so no BigQuery query). **SC4** SC3 with
  `Origin: https://evil.example` → status ≠ 101.
- With `--foreign-host-check` (local only, F5): **SC5** SC3 with `Host: evil.example` and `Origin: http://evil.example` → ≠ 101.
- Output: one line per check and URL, `SC<n> <url> pass|fail|error <detail>` (detail includes the status seen; `error` = the
  request raised `OSError`/`http.client.HTTPException`, detail `<class>: <message>`). Every check runs for every URL whatever
  earlier results were. Exit 3 if any fail (wins), else 5 if any error, else 0. HTTPS uses `ssl.create_default_context()`; timeouts 30 s (a cold start on Cloud Run). No third-party
  imports (an AST test), so it runs under the runners' system `python3` (3.12 on ubuntu-24.04).

### 5. CI job `dashboard-image` and the Make targets

`ci.yml` (offline, no credentials, no cache, no `uses:` besides checkout):
```yaml
  dashboard-image:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
        with:
          persist-credentials: false

      - name: Build
        run: docker build --platform linux/amd64 --provenance=false --sbom=false --build-arg GIT_COMMIT="$GITHUB_SHA" -t dashboard:ci .

      - name: Image contents
        run: docker run --rm dashboard:ci python -I scripts/dashboard_image_check.py

      # The container's logs are printed on exit whatever the outcome (a trap, not an `if:` step:
      # tests/test_docs.py's zizmor test forbids `if:` lines in ci.yml).
      - name: Server behaviour
        run: |
          trap 'docker logs dash 2>&1 | tail -n 100 || true' EXIT
          docker run -d --name dash -p 127.0.0.1:8080:8080 -e PORT=8080 dashboard:ci
          for i in $(seq 1 60); do curl -fsS http://127.0.0.1:8080/_stcore/health && break; sleep 1; done
          python3 scripts/dashboard_server_check.py --connect 127.0.0.1:8080 --foreign-host-check \
            --url http://dashboard-271458030145.us-central1.run.app \
            --url http://127.0.0.1:8080
```
(L2 adds `--url http://<legacy host>`.) The container gets no Google credentials and the app is never rendered (SC3 opens no
session), so no Google call is attempted; F11 removes the startup IP lookup.

`Makefile` (each a `## `-documented target; variables overridable):
- `DASHBOARD_IMAGE ?= readmission-risk-dashboard:local`
- `dashboard-image`: `docker build --platform linux/amd64 --provenance=false --sbom=false --build-arg GIT_COMMIT=$$(git rev-parse
  HEAD) -t $(DASHBOARD_IMAGE) .`
- `dashboard-image-check` (depends on `dashboard-image`): `docker run --rm $(DASHBOARD_IMAGE) python -I
  scripts/dashboard_image_check.py`, then starts the container as `readmission-risk-dashboard-check` on `127.0.0.1:8080`, waits for
  health (60 × 1 s), runs `$(PYTHON) scripts/dashboard_server_check.py --connect 127.0.0.1:8080 --foreign-host-check` with one
  `--url` per word of `$(patsubst https://%,http://%,$(DASHBOARD_URLS))` (scheme → http, host kept) plus `--url
  http://127.0.0.1:8080`, prints the container's last 100 log lines,
  and removes the container whatever the outcome (exit status = the check's).
- `DASHBOARD_URLS ?= https://dashboard-271458030145.us-central1.run.app` (L2 appends the legacy URL); `dashboard-server-check` (LIVE,
  read-only, no credentials): `$(PYTHON) scripts/dashboard_server_check.py` with one `--url` per `DASHBOARD_URLS` entry.
- `KILL_COST ?= 0.01`; `dashboard-kill-test` (LIVE, publishes one message): `$(PYTHON) scripts/publish_budget_message.py
  --budget-name "$$($(TERRAFORM) -chdir=infra output -raw dashboard_budget_name)" --budget-amount "$$($(TERRAFORM) -chdir=infra
  output -raw dashboard_budget_chf)" --cost $(KILL_COST)` (the amount always follows the Terraform variable).
`tests/test_tooling.py`: `EXPECTED_TARGETS` + `dashboard-image`, `dashboard-image-check`, `dashboard-server-check`,
`dashboard-kill-test`; `MAKEFILE_VARIABLES` + `DASHBOARD_BASE_IMAGE`, `DASHBOARD_IMAGE`, `DASHBOARD_URLS`, `KILL_COST`; a dry-run
test per new target asserting its command line(s) as above.

### 6. Terraform

APIs added to `local.services`: `run.googleapis.com`, `artifactregistry.googleapis.com`, `pubsub.googleapis.com`,
`cloudfunctions.googleapis.com`, `cloudbuild.googleapis.com`, `eventarc.googleapis.com`, `logging.googleapis.com` (already on in
every project; adopted). Every new resource has `depends_on = [google_project_service.enabled]`. Snippets below are compressed
(attributes on one line); the files use normal HCL layout and `terraform fmt`.

`infra/dashboard.tf`:
```hcl
locals {
  dashboard_label       = { component = "dashboard" }
  dashboard_placeholder = "us-docker.pkg.dev/cloudrun/container/hello" # replaced by deploy.yml; ignored after create
}

resource "google_artifact_registry_repository" "dashboard" {
  location = var.region, repository_id = "dashboard", format = "DOCKER", labels = local.dashboard_label
  docker_config { immutable_tags = true }
  cleanup_policy_dry_run = false
  cleanup_policies { id = "delete-all", action = "DELETE", condition { tag_state = "ANY" } }
  cleanup_policies { id = "keep-5-newest", action = "KEEP", most_recent_versions { keep_count = 5 } }   # F12
}
resource "google_artifact_registry_repository" "functions" {   # the function's build output (F12: mutable tags)
  location = var.region, repository_id = "functions", format = "DOCKER", labels = local.dashboard_label
  docker_config { immutable_tags = false }
  cleanup_policy_dry_run = false
  cleanup_policies { id = "delete-all", ... } cleanup_policies { id = "keep-5-newest", ... }   # same pair
}

resource "google_cloud_run_v2_service" "dashboard" {
  name = "dashboard", location = var.region, deletion_protection = true
  ingress = "INGRESS_TRAFFIC_ALL", invoker_iam_disabled = true, labels = local.dashboard_label
  scaling { scaling_mode = "AUTOMATIC", min_instance_count = 0, max_instance_count = 1 }
  template {
    labels = local.dashboard_label                                  # F4: billing reads template labels
    service_account = google_service_account.dashboard_runtime.email
    execution_environment = "EXECUTION_ENVIRONMENT_GEN1"
    timeout = "900s"                                               # PRD 15-30 min; reconnects regardless (F3)
    max_instance_request_concurrency = 80
    session_affinity = true
    containers {
      image = local.dashboard_placeholder
      ports { container_port = 8080 }
      resources { limits = { cpu = "1", memory = "512Mi" }, cpu_idle = true, startup_cpu_boost = true }
      # no startup_probe/liveness_probe block: the default TCP startup probe (PRD risk "HTTP probe")
    }
  }
  traffic { type = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", percent = 100 }
  lifecycle {
    # deploy.yml swaps only the image (V15); gcloud writes client/client_version and the F15 template keys. NEVER
    # template[0].revision (provider issue #14569: later applies reuse the revision name and fail with 409), never the whole
    # template[0].labels (F4's billing label).
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
      template[0].labels["client.knative.dev/nonce"],
      template[0].annotations["run.googleapis.com/client-name"],
      template[0].annotations["run.googleapis.com/client-version"],
    ]
  }
}

resource "google_pubsub_topic" "dashboard_budget" { name = "dashboard-budget", labels = local.dashboard_label }

# Billing publishes budget notifications as this Google-managed account; the console grants it when a topic is connected there,
# Terraform must grant it here (budgets-programmatic-notifications). P10 if the apply rejects the member.
resource "google_pubsub_topic_iam_member" "billing_publishes_dashboard_budget" {
  topic = google_pubsub_topic.dashboard_budget.id, role = "roles/pubsub.publisher"
  member = "serviceAccount:billing-budget-alert@system.gserviceaccount.com"
}

resource "google_billing_budget" "dashboard" {
  billing_account = var.billing_account, display_name = "dashboard kill switch"
  budget_filter {
    projects = ["projects/${var.project_number}"], labels = { component = "dashboard" }
    calendar_period = "MONTH", credit_types_treatment = "INCLUDE_ALL_CREDITS"
  }
  amount { specified_amount { currency_code = "CHF", units = tostring(var.dashboard_budget_chf) } }
  threshold_rules { threshold_percent = 0.5, spend_basis = "CURRENT_SPEND" }
  threshold_rules { threshold_percent = 1.0, spend_basis = "CURRENT_SPEND" }
  all_updates_rule { pubsub_topic = google_pubsub_topic.dashboard_budget.id, schema_version = "1.0" }   # default email recipients kept
}

data "archive_file" "dashboard_kill" {
  type = "zip", source_dir = "${path.module}/functions/dashboard_kill", output_path = "${path.module}/.build/dashboard_kill.zip"
  excludes = ["__pycache__", ".pytest_cache", ".DS_Store"]   # and V-T: the folder's git-tracked files are exactly the three
  output_file_mode = "0644"                                   # the same zip (and hash) on every machine
}
resource "google_storage_bucket_object" "dashboard_kill_source" {   # the artifacts bucket's sha256/<hex>/<file> convention
  bucket = google_storage_bucket.artifacts.name
  name   = "sha256/${data.archive_file.dashboard_kill.output_sha256}/dashboard_kill.zip"
  source = data.archive_file.dashboard_kill.output_path
}

resource "google_cloudfunctions2_function" "dashboard_kill" {
  name = "dashboard-kill", location = var.region, labels = local.dashboard_label
  build_config {
    runtime = "python314", entry_point = "on_budget_message"
    service_account = google_service_account.function_builder.id
    docker_repository = google_artifact_registry_repository.functions.id
    source { storage_source { bucket = google_storage_bucket.artifacts.name, object = google_storage_bucket_object.dashboard_kill_source.name } }
  }
  service_config {
    service_account_email = google_service_account.dashboard_kill.email
    ingress_settings = "ALLOW_INTERNAL_ONLY", max_instance_count = 1, available_memory = "256Mi", timeout_seconds = 60
    all_traffic_on_latest_revision = true
    environment_variables = {
      TARGET_SERVICE  = google_cloud_run_v2_service.dashboard.id   # projects/<p>/locations/<r>/services/dashboard
      EXPECTED_BUDGET = google_billing_budget.dashboard.name       # billingAccounts/<a>/budgets/<id>
    }
  }
  event_trigger {
    trigger_region = var.region, event_type = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic = google_pubsub_topic.dashboard_budget.id, retry_policy = "RETRY_POLICY_DO_NOT_RETRY"
    service_account_email = google_service_account.dashboard_kill_trigger.email   # F20
  }
}
```
`python314` is GA on Cloud Run functions (google-24 stack, deprecation 2030-10-10; docs.cloud.google.com/functions/docs/runtime-support,
read 2026-11-02).

**Service accounts and bindings (`iam.tf`; all `_member`, additive; 9 new members here + the topic publisher above = 10,
`test_iam_is_additive_only` 50 → 60):**

| SA | Binding | Why |
|---|---|---|
| `dashboard-deploy` | `roles/run.developer` on the service (`google_cloud_run_v2_service_iam_member`) | deploy a revision; no `run.admin`, so no IAM-policy change |
| | `roles/iam.serviceAccountUser` on `dashboard-runtime` (`google_service_account_iam_member`) | a revision runs as that SA |
| | `roles/artifactregistry.writer` on repo `dashboard` (`google_artifact_registry_repository_iam_member`) | push (includes read) |
| | `roles/iam.workloadIdentityUser` for `local.deploy_principal_set` on itself | WIF, `deploy.yml` on `main` only |
| `dashboard-kill` | `roles/run.developer` on the dashboard service | the PATCH (F1); its only grant (FR24) |
| `dashboard-kill-trigger` | `roles/run.invoker` on the function's Cloud Run service (`google_cloud_run_v2_service_iam_member`, name = `google_cloudfunctions2_function.dashboard_kill.name`) | Eventarc delivery identity (F20); its only grant |
| `function-builder` | `roles/logging.logWriter` (project, `google_project_iam_member`), `roles/artifactregistry.writer` on repo `functions`, `roles/storage.objectViewer` on the artifacts bucket | the documented custom build-SA set (functions/docs/securing/build-custom-sa); the default compute SA has no `roles/editor` since 2026-10-14 |

The operator (project Owner) already holds actAs on every SA; no operator binding is added.

**Pre-registered responses to unverified behaviour** (each an owner-confirmed edit + apply, recorded in Results with the error text,
the IAM member count updated in the test; never a broader predefined role):
- **P1** the kill's PATCH refused for `iam.serviceAccounts.actAs` on `dashboard-runtime` → `roles/iam.serviceAccountUser` on
  `dashboard-runtime` for `dashboard-kill`. **Trade-off, recorded:** with it, the function's identity could deploy any image as
  `dashboard-runtime` (whose own reach is only the `dashboard` views); no narrower grant exists, because actAs cannot be scoped to a
  field of the update.
- **P2** `gcloud run deploy` as `dashboard-deploy` refused a project-level permission (e.g. `run.operations.get`,
  `serviceusage.services.get`) → a project custom role holding exactly the refused permission(s).
- **P3** Eventarc delivery refused → `roles/eventarc.eventReceiver` (project) for `dashboard-kill-trigger`.
- **P4** `ALLOW_INTERNAL_ONLY` blocks the Eventarc Pub/Sub delivery → `ALLOW_ALL` (invocation still requires `run.invoker`).
- **P5** the budget API rejects the label filter (the console offers only labels "with usage") → the apply fails on the budget and
  on the function (it reads the budget's name); everything else is created. After first billed usage (L8, ≈ 1 day later)
  `terraform apply` is re-run without `-target` and creates both. If it still fails, the filter becomes `services =
  ["services/<Cloud Run service id from the Cloud Billing catalog>"]` (it then also counts the function, pennies). L9/L10 wait.
- **P6** `terraform plan` after a CI deploy shows changes on gcloud-owned metadata only (`run.googleapis.com/*` annotations or
  labels, `client*`) → exactly those fields are added to `ignore_changes`; a settings diff is a defect fixed in the workflow, never
  ignored.
- **P7** the function build fails because `python314` is refused → `python313` (kill.py is stdlib-only and 3.12-compatible).
- **P8** the function build fails on a permission (the build SA reading `storage_source`, writing the `functions` repo, or the
  Cloud Functions service agent) → exactly the refused permission on the named resource for the named principal.
- **P9** the kill's PATCH body or mask rejected (400) → the error text recorded; the body/mask changed to the minimal form the API
  accepts (still MANUAL / 0) and the V-K expectation updated.
- **P10** the apply rejects the publisher member (account unknown) → the resource is removed (count back to 59) and the console
  is used once to connect the topic to the budget, which grants Billing's publisher itself; recorded; L10 proves delivery either way.
  If L10 finds no message within 24 h with the member present, the topic's IAM policy and the budget's notification settings are
  read (`gcloud pubsub topics get-iam-policy dashboard-budget`; the budget in the console) and the finding is recorded before any
  change.
- **P11** peak memory at L7/L8 ≥ 90 % of 512 MiB, or an out-of-memory restart → `memory = "1Gi"` (gen1 allows it; ≈ 2× the GiB-s
  price, still inside F3's estimate's order), owner-confirmed.
- **P13** L9 shows `dashboard-kill: error exception=ZoneInfoNotFoundError…` → replace `pacific()` by a
  stdlib rule for US Pacific time (UTC−8, UTC−7 from the second Sunday of March 10:00 UTC to the first Sunday of November 09:00
  UTC), with V-K cases on both transitions; owner-confirmed, recorded.
- **P14** `gcloud run deploy` at L4 is refused any `run.services.*` permission on the service itself (e.g.
  `run.services.setIamPolicy`, which the kept `run.googleapis.com/invoker-iam-disabled` setting might require) → the runbook
  STOPS for an owner decision; never a broader grant (FR23: no `run.admin`, no IAM-policy change by the deployer).
- **P12** the first apply fails because a just-enabled API's Google-managed service agent does not exist yet (typical for Eventarc,
  Cloud Run, Cloud Build) → wait 5 minutes and re-run `apply` (plan re-read first); recorded. Any other apply error stops the runbook.

`variables.tf`:
```hcl
variable "dashboard_budget_chf" {
  description = "Monthly CHF amount at which dashboard-kill disables the dashboard (v3 slice 4, FR24)."
  type        = number
  default     = 3
  validation {
    condition     = var.dashboard_budget_chf == floor(var.dashboard_budget_chf) && var.dashboard_budget_chf >= 1 && var.dashboard_budget_chf <= 100
    error_message = "dashboard_budget_chf must be a whole number of CHF between 1 and 100 (it becomes the budget's units)."
  }
}
```
`outputs.tf`: `dashboard_urls = google_cloud_run_v2_service.dashboard.urls`; `dashboard_image_repository =
"${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.dashboard.repository_id}"`;
`dashboard_budget_name = google_billing_budget.dashboard.name` (contains the billing account id: F16); `dashboard_budget_chf =
var.dashboard_budget_chf`. `versions.tf`: `archive = { source = "hashicorp/archive", version =
"2.8.1" }` (2026-09-11, > 7 days); `test_terraform_versions_agree` (`tests/test_infra.py:336-347`, which today asserts the lock file holds exactly
`[hashicorp/google]` and reads the FIRST `version =`) is rewritten per provider: the lock file's providers are exactly
`{hashicorp/google, hashicorp/archive}`, and each `versions.tf` pin equals the `version` inside that provider's own lock block.

### 7. `.github/workflows/deploy.yml`

```yaml
name: Deploy dashboard

# Keyless deploy of the dashboard image (v3 slice 4, PRD FR23): builds this commit's image in the runner, pushes it under an
# immutable tag, deploys THAT digest to the Terraform-owned Cloud Run service (only the image changes, V15) and smoke-checks
# the served revision. dashboard-deploy is bound to this file's workflow_ref on main (infra/iam.tf); the values below must
# match infra/ (tests/test_infra.py checks them). Rollback: redeploy an earlier digest (F13), never update-traffic.
on:
  workflow_dispatch:

permissions: {}

concurrency:
  group: deploy
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-24.04
    timeout-minutes: 30
    permissions:
      contents: read
      id-token: write
    env:
      REGION: us-central1
      SERVICE: dashboard
      REPOSITORY: us-central1-docker.pkg.dev/synthea-readmission-risk/dashboard
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
        with:
          persist-credentials: false

      # No token_format: no access token is minted as a step output; configure-docker uses gcloud's credential helper.
      - uses: google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093  # v3.0.0
        with:
          workload_identity_provider: projects/271458030145/locations/global/workloadIdentityPools/github/providers/github-actions
          service_account: dashboard-deploy@synthea-readmission-risk.iam.gserviceaccount.com

      - name: Build
        run: docker build --platform linux/amd64 --provenance=false --sbom=false --build-arg GIT_COMMIT="$GITHUB_SHA" -t "$REPOSITORY/dashboard:$GITHUB_SHA-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT" .

      - name: Image contents
        run: docker run --rm "$REPOSITORY/dashboard:$GITHUB_SHA-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT" python -I scripts/dashboard_image_check.py

      - name: Push
        id: push
        run: |
          tag="$REPOSITORY/dashboard:$GITHUB_SHA-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
          gcloud auth configure-docker us-central1-docker.pkg.dev --quiet
          docker push "$tag"
          ref=$(docker inspect --format '{{index .RepoDigests 0}}' "$tag")
          case "$ref" in "$REPOSITORY/dashboard@sha256:"*) ;; *) echo "unexpected digest reference $ref" >&2; exit 1;; esac
          echo "image=$ref" >> "$GITHUB_OUTPUT"

      - name: Deploy
        env:
          IMAGE: ${{ steps.push.outputs.image }}
        run: gcloud run deploy "$SERVICE" --region "$REGION" --image "$IMAGE" --quiet

      - name: Smoke
        env:
          IMAGE: ${{ steps.push.outputs.image }}
        run: |
          rev=$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.latestReadyRevisionName)')
          served=$(gcloud run revisions describe "$rev" --region "$REGION" --format 'value(status.imageDigest)')
          test "$served" = "$IMAGE" || test "$served" = "${IMAGE##*@}" || { echo "served $served, pushed $IMAGE" >&2; exit 1; }
          python3 scripts/dashboard_server_check.py --url https://dashboard-271458030145.us-central1.run.app
          echo "Deployed $IMAGE as $rev" >> "$GITHUB_STEP_SUMMARY"
```
(L2 adds `--url https://<legacy host>`.) The comparison accepts both forms `status.imageDigest` may take (full reference or bare
`sha256:…`). No `--allow-unauthenticated` (public access is Terraform's `invoker_iam_disabled`); `gcloud run deploy` takes exactly
`--region`, `--image`, `--quiet`. The runner's preinstalled gcloud is used (as `smoke.yml`/`score.yml` do). A deploy while the kill
is active (manual 0 instances) fails at Smoke (health is "Service unavailable"); the runbook says re-enable first (§9).

### 8. The kill function (`infra/functions/dashboard_kill/`)

`requirements.txt`: one comment line: `# Intentionally no requirement: kill.py uses only the standard library and the buildpack
provides functions-framework (v3 slice 4, T3).`

`main.py` (whole file apart from its docstring):
```python
import logging

import functions_framework
import kill

# Without a handler Python's lastResort passes only WARNING and up, so the INFO outcome lines would never reach Cloud Logging
# (which collects the function's stderr).
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


@functions_framework.cloud_event
def on_budget_message(cloud_event):
    kill.handle(cloud_event.data)
```
(One third-party import block: ruff classifies `kill` as third-party, since the folder is neither `src/` nor a package, so a
blank line between the two imports would be I001.)

`kill.py` (stdlib only; its imports are `__future__`, `base64`, `binascii`, `collections.abc`, `dataclasses`, `datetime`, `json`,
`functools`, `logging`, `os`, `urllib.error`, `urllib.request`, `zoneinfo`; Python ≥ 3.12). **Time zone:** `pacific()` returns
`ZoneInfo("America/Los_Angeles")`, built lazily on first use and cached (`functools.cache`; `functools` joins the import list), and
`decide` calls it. Not a module-level constant: functions-framework imports `main.py` (and `kill`) at container start and a gen2
deploy waits for a healthy revision, so an import-time failure would break L1's apply instead of reaching L9. A runtime without
zone data therefore yields the `error` outcome line (`ZoneInfoNotFoundError`) on the first message, visible in L9; P13 is the
response. Whether the google-24 run image carries `/usr/share/zoneinfo` is not documented in what was read (unverified):
```python
LOG = logging.getLogger("dashboard-kill")
METADATA_TOKEN_URL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
RUN_API = "https://run.googleapis.com/v2/"
DISABLED = {"scalingMode": "MANUAL", "manualInstanceCount": 0}

class Malformed(ValueError): ...
class KillError(RuntimeError): ...   # an HTTP status outside 2xx or a network failure

@dataclass(frozen=True)
class BudgetMessage:
    budget: str         # "billingAccounts/{billingAccountId}/budgets/{budgetId}" from message.attributes
    cost: float         # data.costAmount
    budget_amount: float  # data.budgetAmount
    currency: str       # data.currencyCode
    period_start: datetime  # data.costIntervalStart (ISO 8601, tz-aware; a trailing "Z" accepted)

def parse(data: dict) -> BudgetMessage:
    """From an Eventarc Pub/Sub CloudEvent's data: data["message"]["attributes"]["billingAccountId"/"budgetId"] and
    data["message"]["data"] (base64 of the JSON payload). Raises Malformed on anything missing, undecodable, non-numeric or a
    naive/unparseable costIntervalStart."""

def decide(msg: BudgetMessage, *, expected_budget: str, now: datetime) -> str:
    """In this order: 'ignore-other-budget' if msg.budget != expected_budget; 'stale-period' if (year, month) of
    period_start.astimezone(ZoneInfo("America/Los_Angeles")) != (year, month) of now.astimezone(ZoneInfo("America/Los_Angeles"))
    (F17; US Pacific budget months); 'kill' if msg.budget_amount > 0 and msg.cost >=
    msg.budget_amount; else 'below-budget'."""

def is_disabled(scaling: dict | None) -> bool:
    """True iff scaling.get("scalingMode") == "MANUAL" and int(scaling.get("manualInstanceCount", 0)) == 0 (proto3 JSON omits
    zero; other keys are ignored)."""

def handle(data: dict, *, environ: Mapping[str, str] = os.environ, http: Callable | None = None,
           now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> str:
    """Order: (1) config: TARGET_SERVICE and EXPECTED_BUDGET must both be non-empty in `environ`, else 'malformed reason=config'
    (whatever the message); (2) parse, else 'malformed reason=<text>'; (3) decide; (4) act on 'kill'.
    Returns the outcome and logs EXACTLY ONE line per call, `dashboard-kill: <outcome> <key=value ...>`:
      malformed            ERROR  reason=<config | Malformed text>              (no exception)
      ignore-other-budget  INFO   budget=.. cost=.. budget_amount=.. currency=.. period_start=..
      stale-period         INFO   (same fields)
      below-budget         INFO   (same fields)
      already-disabled     INFO   (same fields)
      disabled             INFO   (same fields) operation=<long-running operation name from the PATCH response>
      http-error           ERROR  (same fields) method=.. status=<int|None> body=<json.dumps(payload)[:500], or `-` after a network failure>   then raises KillError
      error                ERROR  exception=<class>: <message[:300]>   then re-raises: any exception other than Malformed and
                                  KillError raised in steps (2)-(4) (e.g. a GET response that is not a dict, ZoneInfoNotFoundError)
    Code shape (ruff TRY rules): steps (2)-(4) live in a helper `_process(data, environ, http, now) -> tuple[str, dict]` that
    raises Malformed/KillError itself (raises inside a called function, not in the `try` body: no TRY301); `handle` is
        try: outcome, fields = _process(...)
        except Malformed as exc: <log malformed>; return "malformed"
        except KillError: <log http-error from the fields KillError carries>; raise
        except Exception as exc: LOG.error("dashboard-kill: error exception=%s: %s", ...)  # noqa: TRY400 -- one line, no
            traceback text in it; the re-raise lets the platform record the traceback
            raise
    KillError carries `method`, `status`, `body` and the message fields as attributes for the http-error line.
    `handle` returns the bare outcome word (e.g. 'malformed'); the reason appears only in the log line.
    On 'kill': GET {RUN_API}{TARGET_SERVICE}; is_disabled(resp.get("scaling")) -> already-disabled; else PATCH
    {RUN_API}{TARGET_SERVICE}?updateMask=scaling with body {"scaling": DISABLED} -> disabled (the operation is not awaited).
    `http(method, url, body: dict | None) -> (status: int, payload: dict)` returns HTTP error statuses instead of raising (a
    non-JSON response body becomes `{"raw": <text[:500]>}`) and
    raises KillError only for network failures; the default implementation fetches a metadata-server token (header
    Metadata-Flavor: Google) and calls urllib with a 10 s timeout (urlopen on fixed https/http URLs: `# noqa: S310`, justified in
    a comment)."""
```
Eventarc does not retry (`RETRY_POLICY_DO_NOT_RETRY`); the next budget message, hours later, is the natural retry. Idempotent by
the GET.

`scripts/publish_budget_message.py` (L9; stdlib only: `__future__`, `argparse`, `datetime`, `json`, `subprocess`, `sys`, `zoneinfo`; `gcloud`
via `subprocess`; never in CI; Python ≥ 3.12):
`build_message(*, budget_name, cost, budget_amount, now) -> tuple[dict[str, str], str]` returns (attributes, data JSON text):
attributes `billingAccountId` and `budgetId` (parsed from `budget_name` = `billingAccounts/<a>/budgets/<b>`; ValueError otherwise),
`schemaVersion = "1.0"`; data `budgetDisplayName = "dashboard kill switch"`, `costAmount`, `costIntervalStart` = the first instant
of `now`'s US Pacific month, converted to UTC (`YYYY-MM-01T07:00:00Z` or `…T08:00:00Z`, as Billing sends; F17), `budgetAmount`, `budgetAmountType = "SPECIFIED_AMOUNT"`, `currencyCode = "CHF"`, and
`alertThresholdExceeded = 1.0` only when `cost >= budget_amount` (omitted otherwise, as Billing does). `main()` calls
`subprocess.run(argv, check=False, capture_output=True, text=True)` with exactly `argv = ["gcloud", "pubsub", "topics", "publish",
"dashboard-budget", "--project", "synthea-readmission-risk", "--message", <data JSON>, "--attribute",
"billingAccountId=<a>,budgetId=<b>,schemaVersion=1.0", "--format=value(messageIds)"]`; on return code 0 it prints `published
message <stdout stripped>` and exits 0 (never printing the budget name: F16); otherwise it writes gcloud's stderr to stderr and
exits 1. CLI: `--budget-name` (required), `--cost`,
`--budget-amount` (floats, required). `now` defaults to the current UTC time, so the message is for the current month (F17).

### 9. Re-enable runbook (`docs/setup.md` > Dashboard on Cloud Run > Kill switch)

1. Confirm why it fired: the function's outcome line (`gcloud functions logs read dashboard-kill --region us-central1 --gen2
   --limit 20`) and the billing report filtered to label `component=dashboard`.
2. If in the same calendar month: raise `dashboard_budget_chf` in `infra/terraform.tfvars` first (F8), or wait for the 1st.
3. `terraform -chdir=infra plan`: it must show exactly the `scaling` change on `google_cloud_run_v2_service.dashboard` (F9; plus the
   budget amount if step 2 raised it). Owner confirms, then `apply`.
4. `make dashboard-server-check` → all pass.
5. Deploys and rollbacks while killed fail at Smoke; re-enable first. Rollback = redeploy an earlier digest (F13).

### 10. Footer credit (T4, `chrome.py`)

```python
SYNTHEA_NOTICE_URL = SYNTHEA_URL + "/blob/master/NOTICE"
TERMINOLOGY_CREDIT = (
    "Admission reasons are SNOMED CT descriptions as distributed with Synthea "
    f"([NOTICE]({SYNTHEA_NOTICE_URL})), shown for non-commercial education"
)
```
`footer()` appends it as the fifth part (after the Synthea/Apache part, same ` · ` separator). `tests/dashboard/test_chrome.py`'s
footer expectations gain it.

### 11. Q5 clock parity (`dashboard_query_check.py`)

```python
def _q5(client, report: QueryCheckReport, newest: q.PublishedDate) -> None:
    from readmission_risk.dashboard.heartbeat import simulated_clock  # pandas + stdlib only
```
Runs `q.monitoring_history()` through `_real(client, ..., report, "q5")` (the measurement key follows the existing lowercase
`q0`..`q4` keys); `clock = simulated_clock(rows)` returns `(date, bundle, row)` or None. Pass iff `clock is not None and clock[0] ==
newest.clock_sim_date`; detail `"clock <clock[0]> (python) = <newest.clock_sim_date> (sql); monitored bundle <short_id(clock[1])>;
newest published run's bundle <short_id(newest.model_bundle_id)>"`. Fail (`_Failed`) detail: `"python clock <clock[0] or None> !=
sql clock <newest.clock_sim_date>"`. **Bundles are reported, never asserted.** Known divergence, recorded in the docstring:
`monitoring_history()` has `LIMIT 1000` while the SQL clock CTE has none (slice 3 §4), so Q5 could fail after more than 1,000
monitoring rows since `OPS_EPOCH`; that is a true signal that the heartbeat would also be wrong. Called after `_q4` in
`run_query_checks`; when Q0 lists nothing, `"Q5"` joins the skipped names; the existing tests in
`tests/warehouse/test_dashboard_query_check.py` that assert exact result-name and skip-name sets gain `"Q5"`.

### 12. `.streamlit/config.toml` (F11) and `SERVICE_HOSTS` (the S9 test)

`config.toml` gains:
```toml
[logger]
hideWelcomeMessage = true
```
`SERVICE_HOSTS` lives in a new streamlit-free module `tests/service_hosts.py` (a tuple constant, nothing else), imported by
`tests/dashboard/test_streamlit_config.py`, `tests/scripts/test_dashboard_server_check.py` and `tests/test_tooling.py`; V-SC calls
`pytest.importorskip("streamlit")` only in its real-server tests, so under CI's Python 3.12 `pipeline-dataproc-runtime` job
(`pytest tests/pipeline tests/scripts`, no streamlit) the module still collects, its pure-helper tests run and the server tests
skip with a reason. `tests/dashboard/test_streamlit_config.py`: `EXPECTED` gains `"logger": {"hideWelcomeMessage": True}`; `tests/service_hosts.py`: `SERVICE_HOSTS =
("dashboard-271458030145.us-central1.run.app",)` (L2 appends the legacy host);
`test_allowed_hosts_are_the_service_hosts_plus_dev_hosts`: `set(allowedHosts) - {"localhost", "127.0.0.1"} == set(SERVICE_HOSTS)`;
`test_deploy_ci_and_make_check_every_service_host` (in `tests/test_tooling.py`): the set of `run.app` hosts named in `deploy.yml` (as `--url https://<host>`),
in `ci.yml`'s `dashboard-image` job (as `--url http://<host>`) and in the Makefile's `DASHBOARD_URLS` each equals `set(SERVICE_HOSTS)`.

## UX flow (pipeline)

Offline: `make lock-dashboard` (Docker) → `make dashboard-image-check` → pytest. CI: `lint-and-test` + `dashboard-image`. Live:
Terraform apply creates the service with the placeholder → hosts read → PR merged into `v3` → `v3` merged into `main` → `deploy.yml`
dispatched → a visitor opens the deterministic URL → Cloud Run starts an instance → Streamlit serves the page, the browser opens a
WebSocket, the app queries the `dashboard` views as `dashboard-runtime` → idle → instance count 0. Budget messages flow budget →
topic → Eventarc → `dashboard-kill`, which logs `below-budget` until cost reaches CHF 3, then disables the service.

## Data handling

No data, label, model or split changes; leakage, seeds and split integrity are untouched. The image holds no data and no
credentials; the runtime identity is `dashboard-runtime` (views only, D1-D5 unchanged). The server check opens no Streamlit
session, so CI and smoke runs never query BigQuery. Reproducibility: base image by digest, hash-locked dependencies, the commit in
the image's env and footer, the served digest recorded by the smoke step.

## Failure modes

| Case | Behaviour |
|---|---|
| A pin lacks a Linux wheel | `--only-binary` fails the CI build (`dashboard-image`); design revisited |
| A pin lacks a macOS cp314 wheel | `--only-binary` fails `make setup` loudly (F10 handles watchdog) |
| `make lock-dashboard` without Docker running | docker's own error (documented in setup.md) |
| watchdog marker not applied | the recipe's grep fails; the lockfile test fails |
| Image starts but BigQuery unreachable | FR16 panels (slice 3); health still 200 |
| Deploy refused a permission | the workflow fails at that step; P2 |
| Served digest ≠ pushed | Smoke fails; the new revision serves anyway; the owner redeploys a known digest (F13) |
| Deploy while killed | Smoke fails (Service unavailable); §9 step 5 |
| Kill fires | service unavailable; §9 |
| Kill function: malformed/foreign message | `malformed` / `ignore-other-budget`, one log line, no action |
| Kill function: unexpected exception (incl. missing zone data) | `error` ERROR line + re-raise (the zone too, built lazily: P13); visible in L9 |
| Kill function: HTTP error | `http-error` ERROR line, KillError; no retry; next message retries; budget emails still reach the owner at 50 %/100 % |
| Budget messages never arrive (publisher grant missing) | L10 detects it (no outcome line naming the dashboard budget within 24 h); the missing grant is identified from the budget's notification settings and fixed, recorded |
| Terraform drift after a deploy | L6 + P6 |
| Legacy host refused WebSocket | prevented by L2; SC3 on that URL proves it |
| `main`'s scheduled tick breaks after T1 | F14 rollback |
| Two failed deploys before a success | F12: keep 5 |

## Verification

**Offline (pytest; none reaches GCP or Docker):**
- **V-K** `tests/functions/test_dashboard_kill.py` loads `kill.py` with `load_isolated("kill", tmp_path,
  directory=ROOT / "infra" / "functions" / "dashboard_kill")` and asserts `imported_modules("kill", directory=...)` (TOP-LEVEL names, as the helper returns: `collections`,
  `urllib`, …) ⊆ `sys.stdlib_module_names | {"__future__"}`, the rule `tests/scripts/test_tick_issues.py:28` and
  `test_publish_ledger.py:43` use; the same rule applies to every "stdlib only" script of this slice (V-IC, V-SC, V-KT). The
  module lists in §3, §4 and §8 are the expected imports, documentation, not an equality test. Cases: `parse` of a real-shaped message; Malformed for: no `message`, no attributes, bad base64, non-JSON,
  missing/non-numeric `costAmount`/`budgetAmount`, missing/naive `costIntervalStart`; `decide` table (equal → kill; below →
  below-budget; other budget → ignore; `budget_amount` 0 → below-budget; cost ≥ amount but `costIntervalStart` in the previous
  Pacific month → stale-period (fixture `2026-10-01T07:00:00Z` with now `2026-11-01T09:00:00Z`); a current-month message in the
  UTC/Pacific gap (`costIntervalStart = 2026-11-01T07:00:00Z`, now `2026-11-01T07:30:00Z`) → kill; other budget AND previous month →
  ignore-other-budget); the "real-shaped message" fixture uses `costIntervalStart = 2026-11-01T07:00:00Z`; `is_disabled` on `{"scalingMode":"MANUAL"}` (True), `{"scalingMode":"MANUAL",
  "manualInstanceCount":0,"maxInstanceCount":1}` (True), `{"scalingMode":"AUTOMATIC","maxInstanceCount":1}` (False), `{}` and None
  (False), `{"scalingMode":"MANUAL","manualInstanceCount":2}` (False); `handle` with a fake `http`: already-disabled (GET only),
  disabled (GET then PATCH with exactly `…?updateMask=scaling` and body `{"scaling": DISABLED}`; operation name in the line), GET or
  PATCH status 403 → http-error line and KillError, network KillError → http-error line and re-raised; missing `TARGET_SERVICE`
  with a below-budget message → malformed reason=config (config is checked first), and with a malformed message → malformed
  reason=config; every case logs exactly one `dashboard-kill:` record (`caplog.set_level(logging.INFO, logger="dashboard-kill")`)
  at the level in the table; an `error` case: a fake `http` whose GET returns `(200, ["not", "a", "dict"])` → one ERROR
  `dashboard-kill: error exception=...` line and the exception re-raised; a `pacific()` that raises `ZoneInfoNotFoundError` (monkeypatched) → `error` line and re-raise; `kill`'s import makes no
  `ZoneInfo` call (it is lazy). `main.py` (AST): imports exactly `logging`, `functions_framework` and `kill`, calls
  `logging.basicConfig(level=logging.INFO, ...)` at module level, defines `on_budget_message` decorated with
  `functions_framework.cloud_event`. `requirements.txt` has no non-comment line. The folder's files as git sees them (`git ls-files --cached --others --exclude-standard
  infra/functions/dashboard_kill`, so it also holds before the files are staged) are exactly `kill.py`, `main.py`,
  `requirements.txt`.
- **V-SC** `tests/scripts/test_dashboard_server_check.py`: pure helpers; and a REAL Streamlit server in a subprocess (`python -m
  streamlit run app.py --server.port=<free port> --server.address=127.0.0.1`, cwd = a temp dir holding a one-line `app.py`
  (`import streamlit as st; st.write("ok")`) and a copy of the repo's `.streamlit/config.toml`): the check with `--connect
  127.0.0.1:<port> --foreign-host-check` and `--url http://<h>` for every `SERVICE_HOSTS` host plus `127.0.0.1` → exit 0, SC1-SC5
  pass. Negative control: the same with a temp config where `allowedHosts = []`, `enableXsrfProtection = false`, `enableCORS =
  false` → SC1 (no cookie), SC2 (ACAO `*`), SC4 and SC5 (101) fail, exit 3. Never runs the dashboard app. Skipped with a reason
  when `streamlit` is not importable (CI's `pipeline-dataproc-runtime` job runs `tests/scripts` under Python 3.12 without it);
  timeout 60 s per server. It never contacts Google Cloud; SC4/SC5 cause Streamlit's own checkip lookup (F18).
- **V-IC** `tests/scripts/test_dashboard_image_check.py`: `lock_pins` on a lockfile excerpt with markers and hashes; `compare`
  (missing, extra, mismatch, pip ignored); AST stdlib-only.
- **V-KT** `tests/scripts/test_publish_budget_message.py`: `build_message` → base64 + Eventarc wrapper → `kill.parse` round-trips
  (budget, cost, amount, currency, period_start); both modules loaded in one test through the per-name loader; `alertThresholdExceeded` present iff cost ≥ amount; `costIntervalStart` for a given `now`; a bad
  budget name raises; `main` with a fake `subprocess.run` gets the exact gcloud argv.
- **V-C** `tests/test_container.py`: the `FROM` line matches `^FROM python:3\.14\.\d+-slim-trixie@sha256:[0-9a-f]{64}$`; exactly
  one `pip` RUN with `--require-hashes --only-binary=:all: --no-deps -r requirements-dashboard.txt`; `USER 10001` after every
  `RUN`/`COPY`; `CMD` equals the JSON above; `ENV` sets `PYTHONPATH=/app/src` and `READMISSION_RISK_GIT_COMMIT=${GIT_COMMIT}` after
  `ARG GIT_COMMIT`; no `# syntax=` line; `.dockerignore` equals the exact text of §2; each COPY source string corresponds to one
  `!` line by literal rule (`src/readmission_risk` ↔ `!src/readmission_risk/**`, otherwise equal); the CI build is the
  pattern-matching proof. The Makefile's `DASHBOARD_BASE_IMAGE` equals the FROM reference (read with `make -pn`: a `$(shell …)` value is invisible to
  the Makefile-text regexes other tooling tests use).
- **V-L** §1's lockfile and tooling tests; D8 green.
- **V-T** `tests/test_infra.py` (parsing the HCL text as the existing tests do): Cloud Run attributes as §6 (each, including
  `cpu_idle`, `startup_cpu_boost`, gen1, `timeout = "900s"`, concurrency, session affinity, `deletion_protection`,
  `invoker_iam_disabled`, ingress, both label maps, runtime SA, traffic LATEST 100), `ignore_changes` exactly the six entries of
  §6 (F15) and neither `template[0].revision` nor a bare `template[0].labels`/`template[0].annotations`, no probe blocks; both
  `docker build` lines in `deploy.yml`/`ci.yml` carry `--provenance=false --sbom=false`; the topic publisher member is exactly
  `serviceAccount:billing-budget-alert@system.gserviceaccount.com` with `roles/pubsub.publisher`; the `dashboard_budget_chf`
  validation exists; `archive_file` excludes and `output_file_mode = "0644"`; both repos' cleanup pair (`keep_count = 5`), `dashboard` immutable, `functions` mutable;
  the four new SAs' grants exactly (the `_grants` helper, `tests/test_infra.py:749-754`, gains the target keys `name`,
  `repository`, `topic` and `service_account_id`, placed BEFORE `project` in its `bucket or table_id or dataset_id or ...`
  chain (Cloud Run and Artifact Registry `_iam_member` resources also carry `project`/`location`, which would otherwise win), so a
  binding on the Cloud Run service, an Artifact Registry repo, the topic or a service account is told apart from one on another
  resource of that kind; `dashboard-deploy`'s `serviceAccountUser` on `dashboard-runtime` is asserted through `_grants` (target
  `google_service_account.dashboard_runtime.name`) AND through `_on_account("dashboard_runtime")`) (`dashboard-kill` only `run.developer` on the service, F20) (§6 table), no `roles/run.admin`, `roles/owner`, `roles/editor` anywhere; `deploy_workflow_ref`
  and `deploy_principal_set` locals in `iam.tf` with the exact text pattern of the existing three; `deploy.yml` dispatch-only,
  `permissions: {}`, id-token at job level, SA email + provider equal infra values, no `--allow-unauthenticated`, the `gcloud run
  deploy` line has exactly `--region "$REGION" --image "$IMAGE" --quiet`, `IMAGE` comes from the push step's `@sha256:` check, no
  `update-traffic`; the dashboard budget (label filter, `units = tostring(var.dashboard_budget_chf)`, topic,
  `schema_version`), the function (`python314`, `ALLOW_INTERNAL_ONLY`, `RETRY_POLICY_DO_NOT_RETRY`, `max_instance_count = 1`, build
  SA = `function_builder`, runtime SA = `dashboard_kill`, both env vars), which extends `test_no_resource_uses_default_compute_sa`;
  the 7 new APIs; archive pinned in `versions.tf` and the lock file; IAM member count 60; `test_dashboard_runtime_grants_are_exactly`
  updated; `.gitignore` has `infra/.build/`.
- **V-F** `tests/dashboard/test_chrome.py`: the footer's fifth part equals `TERMINOLOGY_CREDIT`; the link target is
  `SYNTHEA_NOTICE_URL`.
- **V-Q5** `tests/warehouse/test_dashboard_query_check.py` (fake BigQuery): pass on agreeing rows (detail names both bundles);
  fail on a different date; pass when the monitored bundle differs from the newest published run's bundle (reported only); fail
  when `simulated_clock` returns None (no succeeded tick); `Q5` skipped with the others when Q0 is empty; Q5 is the last result.
- zizmor (offline) clean on `deploy.yml` and the changed `ci.yml`; `terraform fmt -check` + `validate` (`make tf-validate`, CI job).
- Gate: `ruff check .` and `pytest` green before (2,170 passed, 2 skipped at `32b16b5`) and after.

**Offline with Docker (implementation; owner's Mac + CI):** `make lock-dashboard` (record the interpreter line and the pin count),
`make dashboard-image-check` (IC1-IC7, SC1-SC5) → record; `make setup` in a fresh venv on macOS installs the Linux-compiled
dashboard lockfile with `--only-binary=:all:` (record); CI `dashboard-image` green on the PR.

## Live runbook (each step owner-confirmed; results in "Results" and CLAUDE.md)

`export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk` for every command. Results redact the billing account (F16).
- **L0** `terraform -chdir=infra init` (archive provider) + `plan`: **28 to add, 0 to change, 0 to destroy** (7 APIs, 2 repos, the
  service, topic, budget, source object, function, 4 SAs, 10 IAM members). Any other count stops the runbook.
- **L1** `apply`. Record `terraform output dashboard_urls` and the redacted budget name; the placeholder answers on the
  deterministic URL. P5/P7/P8/P10/P12 if refused.
- **L2** Add the legacy host (F6) to `.streamlit/config.toml`, `SERVICE_HOSTS`, `deploy.yml`, `ci.yml` and `DASHBOARD_URLS`; tests +
  `make dashboard-image-check` green; commit to this PR (with authorization). Merge the PR into `v3` when its CI is green.
- **L3** PR #40 (`v3` → `main`): CI green on it and `make monitor-simulate` on the merged tree (F14); merge (merge commit) (T1).
  Then a small PR into `main` (and the same into `v3`) moving L16 to (merge date + 50 days) in CLAUDE.md and the two notes.
- **L4** `gh workflow run deploy.yml --ref main`; green (P2 or P14 if refused). Record the run URL, digest and revision.
- **L5** `make dashboard-server-check` from the laptop (SC1-SC4 on both URLs).
- **L6 (M5)** `terraform plan` = "No changes" (P6 otherwise).
- **L7 (M1, S15 visual, a11y, reconnect)** The owner opens the URL: the three views render with data; the footer shows the image
  commit (= `main`'s HEAD at L4) and the T4 line; light and dark mode (OS setting) look right (chart and page). The manual
  accessibility pass: keyboard-only navigation through the tabs and the run-date picker with a visible focus; status as text +
  icon; the chart's text table; body text/link contrast in both themes via the browser's contrast checker. Reconnect (PRD risk):
  keep one tab open past the 900 s request timeout, then use the picker: the app still responds (a brief "Connecting" is fine).
  Redacted error page: run the image locally with a failing script mounted (`docker run --rm -p 127.0.0.1:8080:8080 -v
  "$PWD/<scratch>/fail.py:/app/fail.py:ro" $(DASHBOARD_IMAGE) sh -c 'exec streamlit run fail.py --server.port=8080
  --server.address=0.0.0.0'`, `fail.py` = `raise RuntimeError("secret-detail")`) and confirm in the browser that only "This app has
  encountered an error. The original error message is redacted…" shows and "secret-detail" does not. Screenshots kept locally.
  **Pass criteria (recorded):** `gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="dashboard"
  AND (severity>=ERROR OR textPayload:"Traceback")' --freshness=2h` returns no entry (plain-text stderr lines can arrive without
  a severity, so the content term is what catches a traceback) from these sessions (a `Traceback` in the server log counts as a fail, as
  does any FR16 panel shown while BigQuery is healthy); peak `run.googleapis.com/container/memory/utilizations` for the sessions is
  recorded against 512 MiB (P11 at ≥ 90 %).
- **L8 (M7, M6 part 1)** 5 cold starts: before each, the instance count is 0 (Cloud Run metrics `container/instance_count`, or ≥ 20
  min since the last tab closed), then open the URL in a fresh private window and time Enter → heartbeat line visible (stopwatch);
  also record `container/startup_latencies` for that start. Median ≤ 15 s. The count reaching 0 after the last tab closed is M6's
  scale-to-zero.
- **L9 (M6 part 2)** `make dashboard-kill-test` (cost 0.01) → outcome line `below-budget`, service unchanged; `make
  dashboard-kill-test KILL_COST=3` → `disabled`, the URL answers "Service unavailable"; again →
  `already-disabled`. Re-enable by §9 (plan shows exactly the scaling diff; apply); `make dashboard-server-check` passes. P1/P3/P4/P9
  if refused.
- **L10** Within 24 h of L1 (or of P5's second apply): at least one real budget message reached the function (a `below-budget`
  line naming the dashboard budget). P10's diagnosis if none.
- **L11** Billing for the slice's days (Cloud console > Billing > Reports, project, grouped by service and by label `component`):
  CHF per service; whether Cloud Run lines carry the label; whether egress appears and under which service (F4).
- **L12 (M4)** After the next scheduled Monday tick following L4: within 3 h, without a redeploy, the heartbeat and the call sheet
  show the new simulated date; `make dashboard-query-check` (with Q5) passes.

## Risks

| Risk | Mitigation |
|---|---|
| `ignore_changes` incomplete | L6 + P6 |
| An unverified permission blocks deploy, build or kill | P1-P4, P8, each live-tested at L1/L4/L9 |
| Label filter rejected before usage exists | P5 |
| Forgotten tab bills ~$1.64/day | F3: free tier ≈ 2.8 days, then the CHF 3 kill; billing lag ≈ 1 day → a worst month ≈ CHF 4-5 for the dashboard, inside the existing CHF 5/20 budgets |
| Merging `v3` into `main` publishes unreleased v3 work and changes `main`'s scheduled jobs' code | T1 accepted; F14 checks + rollback; README/docs describe v2 until slice 5; the model-card link stays withheld (E5) |
| gen1 + Python 3.14 + Streamlit incompatibility | L4 shows it; fallback gen2 (minimum 512 MiB, slower cold start), owner-confirmed |
| Cold start > 15 s | 1 vCPU + boost, lazy client, F11; L8 measures; if missed, record and propose (min instances stay 0 by V12) |
| Base-image digest goes stale | slice 5's Dependabot `docker` + Trivy (FR28) |
| Foreign-Origin WebSockets trigger an outbound IP lookup | F11 residual, refused regardless, recorded |

## Out-of-scope follow-ups

Slice 5: Dependabot `docker` for the Dockerfile (a digest bump must be followed by `make lock-dashboard`), Trivy in `deploy.yml`,
pip-audit, model card (E5's link), README tour with the live URL and screenshot. Later: bump `.python-version` to 3.14.10 (F7); a
local-run target impersonating `dashboard-runtime` (E9); Playwright (S2); a custom domain (would allow `default_uri_disabled`); a
rollback input on `deploy.yml` (F13); measuring egress labels if L11 cannot tell.

## Implementation (2026-11-03)

Built as designed (revision 6), on branch `v3-slice4-cloud-run-deploy` off `32b16b5`. Offline only: no Terraform apply, no GCP
call, no workflow dispatch. Implementation-level deviations and measurements:
- **Watchdog marker by a script, not `sed`.** The design's one GNU `sed` needed three quoting layers (make, `sh -c`, sed), so
  `lock-dashboard` runs a 30-line stdlib `scripts/mark_linux_only_pins.py` inside the same container (same contract: exactly the
  watchdog pin line gains `; platform_system == "Linux"`, idempotent, fails if the pin is missing; tested in
  `tests/scripts/test_mark_linux_only_pins.py`); the recipe's grep guard is unchanged.
- **`.dockerignore` re-includes `!src/readmission_risk`, not `!src/readmission_risk/**`.** BuildKit warned `CopyIgnoredFile`: `*`
  excludes the directory entry itself, so `COPY src/readmission_risk` copied an "ignored" directory. `**/__pycache__` and
  `**/*.pyc` still exclude caches. `tests/test_container.py` pins the exact file.
- **`KillError` carries the parsed message** (`.msg`, set in `_process`), so the `http-error` line has the message fields as the
  table says; a network failure before any message is parsed has none.
- **`tests/functions/`** loads `kill.py` through the per-name `load_isolated` (also used by `tests/scripts/test_publish_budget_message.py`
  to load both modules in one test).
- **Measured (owner's Mac, Docker Desktop 29.2.1, emulated linux/amd64):**
  - `make lock-dashboard`: pip-compile under Python 3.14.10 in the base image; 52 pins (35 before: + google-cloud-bigquery's tree,
    + watchdog, marked); the header and the `LockPair` command string unchanged; every shared pin equal to `requirements.txt`
    (D8) and `requirements-dev.txt`.
  - `make dashboard-image-check`: image 195 MB; IC1-IC7 pass (Python 3.14.10, uid 10001, 52 distributions = the lockfile, so the
    base ships no setuptools/wheel; `pip check` clean; the app + 7 lazy google modules import, no banned module loaded; IC7: in the run after the pre-commit review's dirty-tree fix, `commit ''` accepted through `--allow-missing-commit`, this tree being uncommitted; an earlier run, before that fix, had stamped HEAD `32b16b5`, which did not describe the image's contents);
    SC1-SC5 pass for the deterministic host and 127.0.0.1; refused WebSockets answer **HTTP 403**; no startup banner (F11). The
    server log names each refusal: `Rejecting WebSocket connection with disallowed Origin or Host header`.
  - A fresh macOS venv installs `requirements-dashboard.txt` with `--require-hashes --only-binary=:all:`: watchdog skipped by its
    marker, `pip check` clean, streamlit 1.66.1 and google-cloud-bigquery import.
  - `make tf-validate`: valid on provider 8.8.1, including the map-key `ignore_changes` entries. zizmor (offline): no findings.
- **Dirty-tree builds record no commit (pre-commit review round 1, deviation from §5 and IC7).** `make dashboard-image` passes
  `GIT_COMMIT=<HEAD>` only when `git status --porcelain` is empty; a build of uncommitted changes records none (its footer says
  "Image commit unknown"), and only `make dashboard-image-check` then passes `--allow-missing-commit`, which lets IC7 accept an
  empty commit (never a malformed one). CI's `dashboard-image` job and `deploy.yml` build clean checkouts with `$GITHUB_SHA` and run
  IC7 strictly. So a local IC7 "pass" with `commit ''` means "no commit recorded", not a provenance claim.
- **Gate:** `ruff check .` clean; `pytest` 2,281 passed, 2 skipped (after the pre-commit review's fixes) (from 2,170 / 2 at `32b16b5`; the skips: the Dataproc-runtime
  environment check and the off-macOS lock-main guard).

## Pre-commit review (2026-11-03)

`/eg-precommit-review`: 4 rounds, 11 findings fixed, 0 rebutted; round 4 returned `no findings`.
- Round 1 (7): `dashboard_budget_name` output made `sensitive` (F16); `function-builder`'s artifacts read conditioned to
  `sha256/*/dashboard_kill.zip` (static, so no IAM change per source hash); `docs/setup.md` no longer claims both service hosts are
  checked before L2; CLAUDE.md's CI job list (five jobs), pip-cache list and test count; Q5 reads with the app's
  `present.MONITORING_LIMIT`; the staged-only deletion unstaged (nothing staged); a dirty working tree builds with no commit and
  only `make dashboard-image-check` passes `--allow-missing-commit` (the deviation recorded under Implementation).
- Round 2 (3): CLAUDE.md's test count from a measured run; the dirty-tree deviation documented here and in `docs/setup.md`;
  `tests/warehouse/test_dashboard_query_check.py::test_q5_reads_the_rows_the_app_reads` (fails with the Q5 fix reverted).
- Round 3 (1): this note's IC7 measurement corrected (the pre-fix run had stamped HEAD on an uncommitted tree).

## Results

*(filled during the live runbook)*
