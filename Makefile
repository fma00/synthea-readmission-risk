# Common tasks. Recipes call each venv's binaries by path, so no activated venv is needed. Written for GNU Make 3.81
# (macOS /usr/bin/make) as well as 4.x (CI's ubuntu-24.04): no `define NAME =`, .ONESHELL, $(file ...) or `!=`
# assignments (tests/test_tooling.py checks). Every variable below can be overridden: `make score RUN_DATE=20240115`.
# In a linked git worktree with no .venv of its own, point VENV at the main checkout's:
# make lint VENV="$(git rev-parse --git-common-dir)/../.venv" (the pre-commit hooks find it themselves).

PYTHON           ?= python3.14
PYTHON312        ?= python3.12
VENV             ?= .venv
RUNTIME_VENV     ?= .venv-dataproc
LOCK_VENV        ?= .venv-lock312
LOCK314_VENV     ?= .venv-lock314
GOLD_DIR         ?= data/gold/full_run_2b
REFERENCE_DATE   ?= 20260916
TEST_START_DATE  ?= 20230101
EXPERIMENT       ?= readmission-risk-v1
RUN_DATE         ?= 20250630
M5_RUN_DATE      ?= 20230417
SCORE_GOLD_DIR   ?= data/gold/main_200k
TRAIN_EXPERIMENT ?=
TRAIN_ARGS       ?=
SCORE_ARGS       ?=
EXPORT_MODEL     ?= logistic_regression
EXPORT_ARGS      ?=
SOURCE_BUNDLE    ?=
PORTABLE_ARGS    ?=
TERRAFORM        ?= terraform
GOLD_URI         ?= gs://synthea-readmission-risk-gold/v2/main_200k
SCORER_SA        ?= scorer@synthea-readmission-risk.iam.gserviceaccount.com
MONITOR_SA       ?= monitor@synthea-readmission-risk.iam.gserviceaccount.com
LEDGER_SA        ?= ledger-reader@synthea-readmission-risk.iam.gserviceaccount.com
GOLD_SA          ?= gold-publisher@synthea-readmission-risk.iam.gserviceaccount.com
DASHBOARD_SA     ?= dashboard-runtime@synthea-readmission-risk.iam.gserviceaccount.com
PUBLISH_ARGS     ?=
WEEKS            ?= 1
BURN_IN_TICKS    ?= 5
BURN_IN_INTERVAL ?= 30
CHAMPION_BUNDLE_DIR ?= bundles/569fdab6c17d433542b2cd6b531ed8efc5677fb37b00a792d84f79803c6a0f72
LEDGER_OUT       ?= data/monitoring/ledger
RUN              ?=
JDK_TARBALL      ?=
GEN_ARGS         ?=
BATCH_ARGS       ?=
DASHBOARD_IMAGE  ?= readmission-risk-dashboard:local
DASHBOARD_URLS   ?= https://dashboard-271458030145.us-central1.run.app
KILL_COST        ?= 0.01
BIN := $(VENV)/bin
# The dashboard image's base, read from the Dockerfile's FROM line, so the lockfile is compiled in exactly that image (v3 slice 4).
DASHBOARD_BASE_IMAGE := $(shell sed -n 's/^FROM \(python:[^ ]*@sha256:[0-9a-f]\{64\}\)$$/\1/p' Dockerfile)
CAFFEINATE := $(shell command -v caffeinate >/dev/null 2>&1 && echo caffeinate -i)

# $(call check_python,<venv dir>,<major>,<minor>,<variable to override>)
define check_python
$(1)/bin/python -c 'import sys; sys.exit(sys.version_info[:2] != ($(2), $(3)))' || { echo "ERROR: $(1) is not Python $(2).$(3); delete it, or override $(4)" >&2; exit 1; }
endef

# $(call fresh_venv,<dir>,<python>,<variable>): the Makefile owns .venv-* directories and recreates them from scratch, so
# a stale venv never survives. The case guard admits only a plain .venv-* name in the repository root: the first arm
# rejects any '/' or '..' (a shell `*` also matches '/', so `.venv-x/../../y` would otherwise pass), keeping overrides
# such as RUNTIME_VENV=/ or RUNTIME_VENV=.venv-x/../.. away from rm -rf.
define fresh_venv
case "$(1)" in */*|*..*) echo "ERROR: $(3) must name a .venv-* directory in the repository root (it is deleted and recreated)" >&2; exit 1;; .venv-*) ;; *) echo "ERROR: $(3) must name a .venv-* directory in the repository root (it is deleted and recreated)" >&2; exit 1;; esac; rm -rf "$(1)" && $(2) -m venv "$(1)"
endef

.PHONY: help setup hooks lint test pre-commit train export export-portable score score-bq m5-check setup-runtime test-runtime lock-main lock-dev lock-dashboard lock-runtime tf-validate spark-zip generate-vm bigjoin-batch check-population gate prereg-lock prereg-phase-b m5-monitoring gold-publish gold-seeded-check gold-access-check dashboard-access-check dashboard-query-check dashboard-image dashboard-image-check dashboard-server-check dashboard-kill-test gold-200k monitor-preflight monitor-simulate monitor-catch-up ledger m3-check burn-in-timer
.DEFAULT_GOAL := help

help: ## List the targets
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

setup: ## Create .venv (Python 3.14) and install the four hash-locked dependency sets (docs/setup.md)
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	@$(call check_python,$(VENV),3,14,VENV)
	$(BIN)/pip install -e .
	$(BIN)/pip install --require-hashes -r requirements.txt -r requirements-linux-extras.txt
	$(BIN)/pip install --require-hashes --only-binary=:all: -r requirements-dashboard.txt
	$(BIN)/pip install --require-hashes -r requirements-dev.txt
	@echo "Done. Run 'make hooks' to install the git pre-commit hook."

# --allow-missing-config: the hook in .git/hooks/ is shared by every branch and worktree, and a branch without
# .pre-commit-config.yaml (main, until the v2 work merges) would otherwise refuse every commit.
hooks: ## Install the git pre-commit hook (ruff, offline zizmor, terraform fmt)
	$(BIN)/pre-commit install --allow-missing-config

lint: ## Run ruff and the offline zizmor workflow audit, like CI's lint step
	$(BIN)/ruff check .
	$(BIN)/zizmor --offline .

test: ## Run the full pytest suite
	$(BIN)/pytest

pre-commit: ## Run the pre-commit hooks over all tracked files
	$(BIN)/pre-commit run --all-files

train: ## Train both models into TRAIN_EXPERIMENT (required, must not be EXPERIMENT); extra flags via TRAIN_ARGS
	@test -n "$(TRAIN_EXPERIMENT)" || { echo "ERROR: set TRAIN_EXPERIMENT=<a new experiment name>; training into an experiment that already holds runs makes make export's --model ambiguous (docs/scoring.md)" >&2; exit 1; }
	@test "$(TRAIN_EXPERIMENT)" != "$(EXPERIMENT)" || { echo "ERROR: TRAIN_EXPERIMENT must differ from the export experiment $(EXPERIMENT)" >&2; exit 1; }
	$(BIN)/python scripts/train_models.py --gold-dir $(GOLD_DIR) --reference-date $(REFERENCE_DATE) --test-start-date $(TEST_START_DATE) --experiment-name $(TRAIN_EXPERIMENT) $(TRAIN_ARGS)

export: ## Export the EXPORT_MODEL run of EXPERIMENT as a model bundle under bundles/; extra flags via EXPORT_ARGS
	$(BIN)/python scripts/export_bundle.py --experiment-name $(EXPERIMENT) --model $(EXPORT_MODEL) $(EXPORT_ARGS)

export-portable: ## Export the local schema-1 bundle SOURCE_BUNDLE (required) once more with its portable form and parity reference (FR0.3); extra flags via PORTABLE_ARGS
	@test -n "$(SOURCE_BUNDLE)" || { echo "ERROR: set SOURCE_BUNDLE=<a local schema-1 bundle directory>" >&2; exit 1; }
	$(BIN)/python scripts/export_portable_bundle.py --source-bundle $(SOURCE_BUNDLE) --gold-dir $(GOLD_DIR) $(PORTABLE_ARGS)

score: ## Score the Top-N triage list for RUN_DATE with the champion bundle (deploy/champion.json) on SCORE_GOLD_DIR (make gold-200k); extra flags via SCORE_ARGS
	$(BIN)/python scripts/score_discharges.py --gold-dir $(SCORE_GOLD_DIR) --run-date $(RUN_DATE) $(SCORE_ARGS)

score-bq: ## Score RUN_DATE into BigQuery as the scorer SA (laptop; needs ADC); extra flags via SCORE_ARGS
	$(BIN)/python scripts/score_to_bigquery.py --gold-dir $(GOLD_URI) --run-date $(RUN_DATE) --impersonate-service-account $(SCORER_SA) $(SCORE_ARGS)

m5-check: ## Prove every predictions/scoring_runs data test fires on a seeded bad row (M5) for M5_RUN_DATE (published by slice-7 code), as the scorer SA
	$(BIN)/python scripts/seeded_data_test_check.py --run-date $(M5_RUN_DATE) --impersonate-service-account $(SCORER_SA)

m5-monitoring: ## Prove every serving.monitoring data test fires on a seeded synthetic row (M5), as the monitor SA
	$(BIN)/python scripts/seeded_data_test_check.py --monitoring --impersonate-service-account $(MONITOR_SA)

gold-publish: ## LIVE: publish GOLD_URI into serving.gold by write-audit-publish, as the gold-publisher SA (PUBLISH_ARGS=--dry-run audits only)
	$(BIN)/python scripts/publish_gold_to_bigquery.py --gold-uri $(GOLD_URI) --impersonate-service-account $(GOLD_SA) $(PUBLISH_ARGS)

gold-seeded-check: ## LIVE, scratch_gold only: prove every GOLD data test fires on a seeded bad row (M5 for GOLD), as the gold-publisher SA
	$(BIN)/python scripts/gold_seeded_check.py --impersonate-service-account $(GOLD_SA)

gold-access-check: ## LIVE, expected-denied probes: gold-publisher cannot create/change in serving; scorer and monitor cannot read serving.gold
	$(BIN)/python scripts/gold_access_check.py --gold-sa $(GOLD_SA) --scorer-sa $(SCORER_SA) --monitor-sa $(MONITOR_SA)

dashboard-access-check: ## LIVE, expected-denied probes: dashboard-runtime cannot read serving or scratch*, nor create a view; the views' columns are as designed
	$(BIN)/python scripts/dashboard_access_check.py --dashboard-sa $(DASHBOARD_SA)

dashboard-query-check: ## LIVE, read-only: the dashboard's fixed queries as dashboard-runtime (dates, pruning, filters, bytes) and parity with SCORE_GOLD_DIR (make gold-200k)
	$(BIN)/python scripts/dashboard_query_check.py --dashboard-sa $(DASHBOARD_SA) --gold-dir $(SCORE_GOLD_DIR)

# The image records HEAD only when the working tree is clean: a build of uncommitted changes records no commit (its footer then
# says "Image commit unknown"), and dashboard-image-check allows that one case. CI and deploy.yml always build clean checkouts.
dashboard-image: ## Build the dashboard image as DASHBOARD_IMAGE (linux/amd64, single manifest; needs Docker)
	commit=$$(test -z "$$(git status --porcelain)" && git rev-parse HEAD); docker build --platform linux/amd64 --provenance=false --sbom=false --build-arg GIT_COMMIT=$$commit -t $(DASHBOARD_IMAGE) .

dashboard-image-check: dashboard-image ## Offline: the image's contents (IC1-IC7) and server behaviour (SC1-SC5) against a local container
	docker run --rm $(DASHBOARD_IMAGE) python -I scripts/dashboard_image_check.py $$(test -z "$$(git status --porcelain)" || echo --allow-missing-commit)
	@docker rm -f readmission-risk-dashboard-check >/dev/null 2>&1 || true
	docker run -d --name readmission-risk-dashboard-check -p 127.0.0.1:8080:8080 -e PORT=8080 $(DASHBOARD_IMAGE) >/dev/null
	@for i in $$(seq 1 60); do curl -fsS http://127.0.0.1:8080/_stcore/health >/dev/null 2>&1 && break; sleep 1; done; \
	$(PYTHON) scripts/dashboard_server_check.py --connect 127.0.0.1:8080 --foreign-host-check $(foreach u,$(patsubst https://%,http://%,$(DASHBOARD_URLS)),--url $(u)) --url http://127.0.0.1:8080; status=$$?; \
	docker logs readmission-risk-dashboard-check 2>&1 | tail -n 100; docker rm -f readmission-risk-dashboard-check >/dev/null; exit $$status

dashboard-server-check: ## LIVE, read-only, no credentials: SC1-SC4 against every DASHBOARD_URLS entry
	$(PYTHON) scripts/dashboard_server_check.py $(foreach u,$(DASHBOARD_URLS),--url $(u))

dashboard-kill-test: ## LIVE: publish one synthetic budget message (cost KILL_COST, amount = the Terraform variable) to the kill switch's topic
	$(PYTHON) scripts/publish_budget_message.py --budget-name "$$($(TERRAFORM) -chdir=infra output -raw dashboard_budget_name)" --budget-amount "$$($(TERRAFORM) -chdir=infra output -raw dashboard_budget_chf)" --cost $(KILL_COST)

gold-200k: ## Download the monitored gold (GOLD_URI) to SCORE_GOLD_DIR (LIVE, read-only; needs gcloud)
	mkdir -p $(SCORE_GOLD_DIR) && gcloud storage cp "$(GOLD_URI)/*" $(SCORE_GOLD_DIR)/

monitor-preflight: ## Offline: check the completeness guard accepts every simulated week (needs make gold-200k and the local champion bundle)
	$(BIN)/python scripts/monitor_tick.py preflight --gold-dir $(SCORE_GOLD_DIR) --bundle $(CHAMPION_BUNDLE_DIR)

monitor-simulate: ## Offline: score every simulated week and evaluate each cohort (the sanity check; L10's oracle)
	$(BIN)/python scripts/monitor_tick.py simulate --gold-dir $(SCORE_GOLD_DIR) --bundle $(CHAMPION_BUNDLE_DIR) --out data/monitoring/simulation.csv

monitor-catch-up: ## LIVE: run WEEKS monitoring ticks from the laptop (clean main only; no Actions tick may run); refused Mon 00:00-Thu 06:00 UTC (weekly retry slots)
	$(BIN)/python scripts/monitor_tick.py catch-up --weeks $(WEEKS) --scorer-sa $(SCORER_SA) --monitor-sa $(MONITOR_SA)

ledger: ## Render the run ledger and badge from BigQuery into LEDGER_OUT, as the ledger-reader SA
	$(BIN)/python scripts/render_ledger.py --out-dir $(LEDGER_OUT) --impersonate-service-account $(LEDGER_SA)

m3-check: ## Check metric M3 (>= 5 consecutive green timer-dispatched burn-in ticks, then a scheduled weekly one), as the ledger-reader SA; needs gh
	$(BIN)/python scripts/m3_check.py --impersonate-service-account $(LEDGER_SA)

burn-in-timer: ## LIVE: dispatch BURN_IN_TICKS burn-in ticks of monitor.yml, one every BURN_IN_INTERVAL minutes (laptop; needs gh); refuses Mon-Wed UTC (weekly retry slots)
	$(CAFFEINATE) $(BIN)/python scripts/burn_in_timer.py --ticks $(BURN_IN_TICKS) --interval-minutes $(BURN_IN_INTERVAL)

setup-runtime: ## Recreate .venv-dataproc: Python 3.12 + the Dataproc runtime 3.0 mirror (requirements-dataproc-3.0.txt)
	@$(call fresh_venv,$(RUNTIME_VENV),$(PYTHON312),RUNTIME_VENV)
	@$(call check_python,$(RUNTIME_VENV),3,12,PYTHON312)
	$(RUNTIME_VENV)/bin/pip install -e .
	$(RUNTIME_VENV)/bin/pip install --require-hashes -r requirements-dataproc-3.0.txt
	$(RUNTIME_VENV)/bin/pip check

# Does not set READMISSION_RISK_EXPECT_DATAPROC_RUNTIME: a laptop's JDK need not be 21, so the exact stack is asserted in CI only.
test-runtime: ## Run tests/pipeline and tests/scripts in .venv-dataproc (needs JDK 17 or 21)
	@test -x $(RUNTIME_VENV)/bin/pytest || { echo "ERROR: run 'make setup-runtime' first" >&2; exit 1; }
	@echo "PySpark uses JAVA_HOME=$${JAVA_HOME:-<unset: the java on PATH>}; Spark 4.0 supports JDK 17 or 21 only."
	$(RUNTIME_VENV)/bin/pytest tests/pipeline tests/scripts

# requirements.txt and requirements-dev.txt are compiled under Python 3.14, the interpreter that installs them (since
# 2026-10-12), and on macOS: pip-compile resolves environment markers for the host only, and requirements-linux-extras.txt
# holds what a macOS compile cannot see (on Linux, nvidia-nccl-cu13 would land in requirements.txt unmarked).
# requirements-dataproc-3.0.txt is compiled under 3.12, the Dataproc runtime's interpreter. The header records how
# pip-compile was invoked, not this recipe: run these targets rather than the header's command. tests/test_lockfiles.py
# checks the result.
lock-main: ## Recompile requirements.txt under Python 3.14 on macOS, keeping existing pins
	@test "$$(uname -s)" = Darwin || { echo "ERROR: run lock-main on macOS (see the comment above lock-main in the Makefile)" >&2; exit 1; }
	@$(call fresh_venv,$(LOCK314_VENV),$(PYTHON),LOCK314_VENV)
	@$(call check_python,$(LOCK314_VENV),3,14,PYTHON)
	@$(LOCK314_VENV)/bin/python -c 'import sys; print("pip-compile interpreter:", sys.version.split()[0], sys.executable)'
	$(LOCK314_VENV)/bin/pip install --require-hashes -r requirements-dev.txt
	$(LOCK314_VENV)/bin/pip-compile --generate-hashes --output-file=requirements.txt requirements.in
	@echo "requirements-linux-extras.txt is hand-maintained: if a Linux-only dependency changed, update it (docs/setup.md)."
	@echo "Then run make lock-dashboard: requirements-dashboard.txt is constrained by requirements.txt."

lock-dev: ## Recompile requirements-dev.txt under Python 3.14 on macOS, keeping existing pins
	@test "$$(uname -s)" = Darwin || { echo "ERROR: run lock-dev on macOS (see the comment above lock-main in the Makefile)" >&2; exit 1; }
	@$(call fresh_venv,$(LOCK314_VENV),$(PYTHON),LOCK314_VENV)
	@$(call check_python,$(LOCK314_VENV),3,14,PYTHON)
	@$(LOCK314_VENV)/bin/python -c 'import sys; print("pip-compile interpreter:", sys.version.split()[0], sys.executable)'
	$(LOCK314_VENV)/bin/pip install --require-hashes -r requirements-dev.txt
	$(LOCK314_VENV)/bin/pip-compile --allow-unsafe --generate-hashes --output-file=requirements-dev.txt requirements-dev.in

# The dashboard's set (v3 slice 3, S17; v3 slice 4, T2/F10): constrained by requirements.txt (`-c` in the .in), so run it after
# lock-main. Compiled inside the dashboard image's own base (linux/amd64), so it is the image's lockfile; watchdog is then marked
# Linux-only, because it has no macOS wheel for Python 3.14 (make setup installs this set with --only-binary=:all:).
lock-dashboard: ## Recompile requirements-dashboard.txt inside the image's base (linux/amd64, Python 3.14), keeping existing pins; needs Docker
	@test -n "$(DASHBOARD_BASE_IMAGE)" || { echo "ERROR: no digest-pinned FROM line in Dockerfile" >&2; exit 1; }
	docker run --rm --platform linux/amd64 --user "$$(id -u):$$(id -g)" -e HOME=/tmp -v "$(CURDIR)":/src -w /src $(DASHBOARD_BASE_IMAGE) sh -euc 'python -m venv /tmp/lock && /tmp/lock/bin/pip install --require-hashes -r requirements-dev.txt && /tmp/lock/bin/python -c "import sys; print(\"pip-compile interpreter:\", sys.version.split()[0], sys.platform)" && /tmp/lock/bin/pip-compile --generate-hashes --output-file=requirements-dashboard.txt requirements-dashboard.in && /tmp/lock/bin/python scripts/mark_linux_only_pins.py requirements-dashboard.txt watchdog'
	@grep -q '^watchdog==.* ; platform_system == "Linux" \\$$' requirements-dashboard.txt || { echo "ERROR: watchdog marker not applied (F10)" >&2; exit 1; }

# --upgrade on purpose: the runtime mirror is defined as the newest patch of each bundled minor at compile time.
lock-runtime: ## Recompile requirements-dataproc-3.0.txt under Python 3.12 (re-resolves to the newest allowed patches)
	@$(call fresh_venv,$(LOCK_VENV),$(PYTHON312),LOCK_VENV)
	@$(call check_python,$(LOCK_VENV),3,12,PYTHON312)
	@$(LOCK_VENV)/bin/python -c 'import sys; print("pip-compile interpreter:", sys.version.split()[0], sys.executable)'
	$(LOCK_VENV)/bin/pip install --require-hashes -r requirements-dev.txt
	$(LOCK_VENV)/bin/pip-compile --upgrade --generate-hashes --output-file=requirements-dataproc-3.0.txt requirements-dataproc-3.0.in

# Static checks only, like CI's terraform-validate job: no backend, no credentials, nothing applied. Locally, fmt
# -recursive also covers the gitignored infra/terraform.tfvars, which CI never sees (run `terraform -chdir=infra fmt`).
tf-validate: ## Check infra/ like CI's terraform-validate job: fmt -check, init without backend, validate
	$(TERRAFORM) -chdir=infra fmt -check -recursive -diff
	$(TERRAFORM) -chdir=infra init -backend=false -input=false -lockfile=readonly
	$(TERRAFORM) -chdir=infra validate

spark-zip: ## Build the Spark job's --py-files zip into dist/ and print its sha256 (FR3)
	$(BIN)/python scripts/build_spark_zip.py

generate-vm: ## LIVE, COSTS MONEY: generate the prereg population RUN on a spot VM (needs RUN, JDK_TARBALL; extra flags via GEN_ARGS)
	@test -n "$(RUN)" || { echo "ERROR: set RUN=<a population in prereg.yaml, e.g. pilot_20k>" >&2; exit 1; }
	@test -n "$(JDK_TARBALL)" || { echo "ERROR: set JDK_TARBALL=<the Temurin tarball pinned in prereg.yaml>" >&2; exit 1; }
	@echo "COST: creates a spot n2-standard-32 VM in synthea-readmission-risk (about \$$0.30/h, bounded by the template's max run duration)"
	$(BIN)/python scripts/launch_generation.py --run $(RUN) --jdk-tarball $(JDK_TARBALL) $(GEN_ARGS)

bigjoin-batch: ## LIVE, COSTS MONEY: run the Big Join for RUN on Dataproc Serverless 3.0 (needs RUN; extra flags via BATCH_ARGS)
	@test -n "$(RUN)" || { echo "ERROR: set RUN=<a population in prereg.yaml, e.g. pilot_20k>" >&2; exit 1; }
	@echo "COST: submits a Dataproc Serverless batch (maxExecutors=7, ttl 2h) in synthea-readmission-risk"
	$(BIN)/python scripts/submit_big_join_batch.py --run $(RUN) $(BATCH_ARGS)

check-population: ## LIVE, read-only: download raw subsets and gold for RUN and run checks C1-C9 (extra flags via CHECK_ARGS)
	@test -n "$(RUN)" || { echo "ERROR: set RUN=<a population in prereg.yaml, e.g. pilot_20k>" >&2; exit 1; }
	@echo "READ-ONLY: downloads RUN's raw patients/encounters (main_200k: ~4.5 GB) and gold into data/checks/"
	$(BIN)/python scripts/check_population.py --run $(RUN) $(CHECK_ARGS)

gate: ## LIVE, read-only: promotion gate + frozen-v1 for RUN (GATE_ARGS: --challenger-experiment, --challenger-bundle, ...)
	@test -n "$(RUN)" || { echo "ERROR: set RUN=<main_200k, or pilot_20k with GATE_ARGS=--rehearsal ...>" >&2; exit 1; }
	@echo "READ-ONLY: downloads RUN's gold; exit 0 PROMOTE, 3 REFUSE, 2 could not run (judge the outcome from the report)"
	$(BIN)/python scripts/run_promotion_gate.py --run $(RUN) $(GATE_ARGS)

prereg-lock: ## Append the slice-6b analysis lock to prereg.yaml before training (LOCK_ARGS: --experiment-name NAME)
	@test -n "$(LOCK_ARGS)" || { echo "ERROR: set LOCK_ARGS=\"--experiment-name <the experiment the challenger will be trained into>\"" >&2; exit 1; }
	$(BIN)/python scripts/prereg_append.py lock --challenger-model logistic_regression $(LOCK_ARGS)

prereg-phase-b: ## Append Phase B to prereg.yaml from the two main_200k gate reports (extra flags via PHASE_B_ARGS)
	$(BIN)/python scripts/prereg_append.py phase-b $(PHASE_B_ARGS)
