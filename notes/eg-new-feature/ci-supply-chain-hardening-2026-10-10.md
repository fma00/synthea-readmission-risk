# CI supply-chain hardening: SHA-pinned actions, Dependabot, zizmor (2026-10-10)

Design doc for `/eg-new-feature "SHA-pin the CI actions, plus Dependabot for github-actions only, needed before any workflow gets cloud credentials."`
Scope widened at the Step 0 gate (owner decision, 2026-10-10) to also include `persist-credentials: false` and zizmor in
CI, and after design-check round 1 (owner decisions, 2026-10-10) to run zizmor as a separate online, blocking job and to
fix the push/PR double-run by restricting `push` to `main`.

Revision 3 (round 1: comprehension passed, critic 12 gaps, readiness 3 questions; round 2: readiness ready, critic 6
gaps; round 3: readiness 1 question, critic 10 gaps; round 4: readiness ready, critic 7 gaps — 3-revision cap hit, owner
chose "apply the 7 fixes, re-run B/C"; round 5: readiness ready, critic 3 gaps with measured fixes — owner chose
"apply fixes, override and proceed"; this is revision 5 and the critic did NOT formally close). Resolution logs at the end.

## Why

The v2 PRD (`notes/prds/v2-mlops-keyless-scheduled-scale-up-2026-10-09.md`, FR0.2 "Supply-chain baseline (G17), merged
before any workflow holds `id-token: write`") requires the CI workflow to stop trusting movable tags before v2 gives any
workflow a GCP credential through Workload Identity Federation (FR8).

Today `.github/workflows/ci.yml` runs three third-party actions by **tag** (`actions/checkout@v5`,
`actions/setup-java@v5`, `actions/setup-python@v6`). A tag can be repointed by anyone with write access to the action's
repository; in March 2025 attackers repointed `tj-actions/changed-files`' tags to a commit that dumped CI secrets into
build logs (CVE-2025-30066). With `permissions: contents: read` and no secrets the blast radius today is small; once a
job can mint a GCP token it is the GCP project. A full 40-hex commit SHA is immutable. Pinning removes automatic updates,
so Dependabot is added to propose (not apply) updates as reviewable PRs. zizmor (a static auditor for GitHub Actions
workflows) is added to CI so regressions (a new unpinned `uses:`, a stale version comment, a missing cooldown, a known
vulnerable action) fail CI.

Measured facts (2026-10-10, zizmor 1.30.1):
- The tags resolve to: `actions/checkout@v5` → `fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09` = tag `v5.1.0`;
  `actions/setup-java@v5` → `b6effb05e454b25005698d916606bdc6ffcbf961` = `v5.7.0`;
  `actions/setup-python@v6` → `ece7cb06caefa5fff74198d8649806c4678c61a1` = `v6.3.0`
  (via `gh api repos/<owner>/<repo>/commits/<tag> --jq .sha`, which peels annotated tags to the commit, and
  `gh api repos/<owner>/<repo>/tags` to find the exact-version tag sharing that commit).
- Newer majors exist (checkout v7.0.1, setup-java v6.0.1, setup-python v7.0.0). **Owner decision: pin the current
  majors** (behaviour-neutral); majors arrive later as individual Dependabot PRs.
- All three are JavaScript actions (`runs.using: node24`, bundled `dist/*.js`), so the SHA fixes all of their code;
  they contain no nested `uses:`. **Residual (named, not fixed):** `setup-java` and `setup-python` *download* a
  toolchain at run time (newest Temurin 17 patch; newest 3.12 / 3.14 patch); SHA pins do not lock those downloads.
- zizmor on the current workflow: 3 × `unpinned-uses` (high), 1 × `artipacked` (medium, missing
  `persist-credentials: false`).
- On a scratch copy of `.github/` with the end state below (both jobs, `push: branches: [main]`; my run used a
  `dependabot.yml` differing only in group name and without the `patterns` line, and the round-3 critic re-ran it with
  the exact content below, same result), zizmor online and offline report **"No findings"**; the only suppressed findings are two pedantic-persona
  `anonymous-definition` infos (jobs have no `name:`). Without the `cooldown` block it reports `dependabot-cooldown`.
- zizmor collects inputs **honoring `.gitignore`** by default (`--help`; debug log shows `./.gitignore` loaded; the real
  working tree scans in 0.03 s), so the gitignored `.venv/`, `data/`, `mlflow/`, `tools/` are not scanned locally.
- zizmor reads its token from `--gh-token` or env `GH_TOKEN` / `GITHUB_TOKEN` / `ZIZMOR_GITHUB_TOKEN`. **With no token it
  silently degrades to offline mode** (one WARN line, exit 0 if clean). `--gh-token ""` is a hard error
  ("GitHub token cannot be empty"). Hence the CI command passes the token explicitly (below).
- **Exact CI command measured:** `zizmor --gh-token "$GH_TOKEN" .` on the scratch end state: last line
  `No findings to report. Good job! (2 suppressed)`, exit 0. Offline-mode warning text (no token anywhere):
  `WARN audit: zizmor: zizmor is running in offline mode by default; some audits and auto-fixes will not be available.`
- **zizmor suppression channels (measured, 1.30.1):** it discovers a config file named `.github/zizmor.yml`,
  `.github/zizmor.yaml`, `zizmor.yml` or `zizmor.yaml`; a config with `rules: unpinned-uses: disable: true` turned an
  unpinned `@v6` from a high finding into "No findings". Inline `# zizmor: ignore[<audit>]` comments also suppress.
- Online-only audits per docs.zizmor.sh/audits: `impostor-commit`, `known-vulnerable-actions` (GitHub Advisory
  Database), `ref-confusion`, `stale-action-refs`. **Measured:** `ref-version-mismatch` is marked offline-capable in
  the docs table, but a deliberately wrong comment (`# v5.6.0` on the v5.7.0 SHA) was flagged only online, not with
  `--offline`. So the comment↔SHA check also depends on online mode.
- GitHub's Dependabot options reference: `directory: "/"` makes Dependabot scan `/.github/workflows`; for
  `github-actions`, `cooldown` supports `default-days` only; dependencies matching no group rule get individual PRs.
  Dependabot rewriting the trailing `# vX.Y.Z` comment is documented only by third-party guides and real PRs.
- **Pre-existing bug (found by the critic, verified by reading `ci.yml:3-15`):** `on: push` + `on: pull_request` with
  concurrency group `${{ github.workflow }}-${{ github.ref }}` does NOT de-duplicate a push and its PR: `github.ref` is
  `refs/heads/<branch>` for push and `refs/pull/<N>/merge` for pull_request. The `ci.yml` comment (lines 11-12) and
  CLAUDE.md ("Repo hygiene" > CI: "de-duplicates the push+PR double trigger") both claim otherwise. Dependabot's PR
  branches live in this repo, so every Dependabot PR would run CI twice. **Owner decision: fix it by `push: branches:
  [main]`.**

## Scope

In:
1. Pin all `uses:` in `.github/workflows/ci.yml` to the SHAs above, each followed by two spaces, `# ` and the exact tag.
2. `persist-credentials: false` on every checkout step.
3. `on.push` restricted to `branches: [main]`; correct the concurrency comment.
4. New job `audit-workflows` (separate, runs once, not in the matrix) running zizmor online, blocking.
5. New `.github/dependabot.yml`: `github-actions` only, weekly, 7-day cooldown, minor+patch grouped.
6. zizmor added to `requirements-dev.in`, `requirements-dev.txt` recompiled (hash-locked).
7. Five new offline tests in `tests/test_docs.py`, section `(e) CI`.
8. Documentation: `docs/setup.md`, README layout block, `tests/test_docs.py` module docstring, CLAUDE.md CI bullet.

Out (explicitly): upgrading to the newer majors; the repo-level "require SHA pinning" policy (outward-facing GitHub
setting; after the push, with owner go-ahead); Dependabot for `pip` (it would fight the pip-compile hash lockfiles, the
hand-maintained `requirements-linux-extras.txt` and the deliberate pandas/numpy holds); job `name:`s (pedantic only);
SARIF/code-scanning upload (needs `security-events: write`); `id-token` rules (no such job exists yet); pinning the
toolchains `setup-java`/`setup-python` download; pre-commit hooks / Makefile (FR0.1, OQ6); the pre-existing missing
`requirements*.txt`-vs-`.in` CI check; the uncommitted label-horizon-purge work in the tree (see Data handling).

## Surfaces touched

- `.github/workflows/ci.yml` (edit)
- `.github/dependabot.yml` (new)
- `requirements-dev.in`, `requirements-dev.txt` (edit / recompile)
- `tests/test_docs.py` (module docstring bullet "CI:" at line 17; five new tests and two constants in section
  `# ---- (e) CI`, after `test_ci_workflow_requests_read_only_token_permissions`, lines 448-453)
- `docs/setup.md` ("Verify the code works": command block lines 27-30, sentence at line 32)
- `README.md` (repository-layout block, line 156)
- `CLAUDE.md` ("Repo hygiene" > "CI" bullet)
- Nothing under `src/`, no data, model, MLflow or pipeline surface.

## Interfaces

### `.github/workflows/ci.yml` — full end state

```yaml
name: CI

on:
  push:
    # Pushes to other branches get CI through their pull request; running on both would double every PR's jobs.
    branches: [main]
  pull_request:

# Least privilege: this workflow only checks out code, lints, tests and audits workflows.
permissions:
  contents: read

# Pull requests: one group per PR, so a new push cancels the superseded run. Pushes to main: one group per commit, so
# no run on main is ever cancelled, not even while pending (GitHub keeps one pending run per group and cancels the
# older one). github.ref differs between push and pull_request, so the old ref-only group never merged a push with
# its PR; the push filter above is what prevents the double run.
concurrency:
  group: ${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref || github.sha }}
  cancel-in-progress: true

# Every `uses:` is pinned to a full commit SHA with a `# vX.Y.Z` comment: a tag can be moved by whoever controls the
# action's repository, a commit cannot. Dependabot (.github/dependabot.yml) proposes updates as PRs; the
# audit-workflows job fails on an unpinned action, a comment that does not match its SHA, or a known-vulnerable action.
jobs:
  lint-and-test:
    <unchanged comment + strategy + runs-on>
    steps:
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
        with:
          # CI never pushes; do not leave the job token in .git/config for later steps.
          persist-credentials: false

      - uses: actions/setup-java@b6effb05e454b25005698d916606bdc6ffcbf961  # v5.7.0
        with:
          <unchanged>

      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
        with:
          <unchanged>

      <Install dependencies, Lint, Test: unchanged>

  # Runs zizmor ONLINE: some audits (known-vulnerable-actions, impostor-commit, and in practice ref-version-mismatch)
  # need the GitHub API. So this job can turn red without a commit, e.g. when an advisory is published for a pinned
  # action; that is the point (bump the action). It is a separate job so its result never hides lint/test results.
  audit-workflows:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
        with:
          persist-credentials: false

      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
        with:
          # The floating minor, like lint-and-test: an exact .python-version patch may not be on the runner yet.
          python-version: "3.14"
          cache: "pip"
          cache-dependency-path: requirements-dev.txt

      - name: Install zizmor
        run: pip install --require-hashes -r requirements-dev.txt

      - name: Audit workflows
        env:
          GH_TOKEN: ${{ github.token }}
        # --gh-token makes a missing token an error; without it zizmor would silently skip the online audits.
        run: zizmor --gh-token "$GH_TOKEN" .
```

`<unchanged …>` means the current lines are kept byte-for-byte. The top-level `permissions: contents: read` covers both
jobs (the existing test requires it at top level). zizmor exits non-zero on any finding at its default persona; no
`continue-on-error`, `--no-exit-codes`, `--min-severity`, `|| true` anywhere. `requirements-dev.txt` installs on its
own (every entry is pinned and hashed). The `lint-and-test` pip cache key changes on the first run because
`requirements-dev.txt` changes; expected.

### `.github/dependabot.yml` — full content

```yaml
# Proposes updates to the SHA-pinned actions in .github/workflows/ as pull requests; nothing updates without a merge.
# github-actions only: Python dependencies are hash-locked with pip-compile (see docs/setup.md), which Dependabot's pip
# updater would fight. The cooldown skips releases younger than 7 days, so a compromised release is usually pulled
# before it is ever proposed. Majors are not in the group and arrive as one PR each.
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    cooldown:
      default-days: 7
    groups:
      github-actions-minor-and-patch:
        patterns: ["*"]
        update-types: ["minor", "patch"]
```

### Dependency lockfile

- `requirements-dev.in` gains one line, `zizmor`, after `pip-tools`, unpinned like its siblings.
- Recompile under **Python 3.12.11** (`~/.pyenv/versions/3.12.11/bin/python -m venv` in the session scratchpad,
  `pip install pip-tools==7.6.1`, the version in `.venv`), keeping the lockfile's "compiled with Python 3.12" header, with
  `pip-compile --allow-unsafe --generate-hashes --output-file=requirements-dev.txt requirements-dev.in` (the header's
  command minus `--no-index`, per CLAUDE.md; pip-compile re-writes `--no-index` into the header itself, as before).
  No `--upgrade`. **Expected zizmor version: 1.30.1** (newest on PyPI on 2026-10-10, the version every measurement in
  this doc was made with). If the recompile resolves a different version, verification steps 4-5 are the
  re-measurement; any difference from the expected outputs there is reported before continuing.
- **Acceptance rule (exact):** on the content lines of the diff only,
  `git diff --unified=0 -- requirements-dev.txt | grep -E '^[-+]' | grep -vE '^(---|\+\+\+) '`, there must be no
  line starting with `-`, and every `+` line must belong to the new contiguous block `zizmor==<version> \`, its `--hash=sha256:` lines, and its
  `    # via -r requirements-dev.in`. zizmor has no Python dependencies (`requires_dist` is null on PyPI), so no `# via`
  annotation of another package can change. Expected position: alphabetically after the `wheel==` block and before
  the blank line that precedes `# The following packages are considered to be unsafe in a requirements file:`.
  Any other difference: stop and report, do not hand-edit.
- Install into the existing 3.14 `.venv` with `pip install --require-hashes -r requirements-dev.txt`; `pip check` clean.

### Tests (`tests/test_docs.py`, section `(e) CI`; offline, no YAML library, line-based like the existing CI test)

New module-level constants next to the CI section: `WORKFLOWS_DIR = ROOT / ".github" / "workflows"` and
`DEPENDABOT = ROOT / ".github" / "dependabot.yml"`. Helper `_workflow_files()` returns
`sorted([*WORKFLOWS_DIR.glob("*.yml"), *WORKFLOWS_DIR.glob("*.yaml")])`. A "uses line" is a line matching
`^(\s*)(?:- )?uses:\s*(.+?)\s*$`; a `uses:` text inside a `run: |` block scalar would also match — accepted (none
exist; a false red there is visible and cheap). Group 2 is the "reference text" (reference plus any comment).

1. `test_every_workflow_action_is_pinned_to_a_commit_sha_with_a_version_comment` — over every uses line of every
   workflow file: references starting with `./` are skipped (in-repo code); every other reference text must fullmatch
   `[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}  # v\d+\.\d+\.\d+` (exactly two spaces before `#`). So `docker://`
   references fail, deliberately. Asserts ≥ 1 uses line was found. Failure message lists `file:lineno: line`.
2. `test_checkout_steps_do_not_persist_credentials` — for every uses line whose reference starts with
   `actions/checkout@`: find the **step start** = the nearest line at or above it matching `^(\s*)- ` whose dash column
   is less than the `uses` key's column (on a `- uses:` line, that line itself); the **step block** = the step start
   plus all following lines up to (excluding) the first non-blank line whose indentation is ≤ the dash column. The
   block must contain a line whose stripped form is `persist-credentials: false` (works whether `uses:` or `with:`
   comes first, and for `- name:`-first steps). Asserts ≥ 1 checkout step found (there will be 2).
3. `test_dependabot_config_is_github_actions_only_weekly_with_cooldown_and_grouping` — `DEPENDABOT` exists; its lines,
   with comment-only lines (`lstrip().startswith("#")`) and blank lines removed and trailing whitespace stripped,
   **equal exactly** the 12 non-comment lines of the full content above. Deliberately strict (like this file's other
   "changes on purpose" checks): any edit to the config must update the test, which makes the ecosystem, interval,
   cooldown nesting and grouping all checked.
4. `test_ci_audits_workflows_with_zizmor_online_and_blocking` — in `ci.yml`: exactly one stripped line equals
   `run: zizmor --gh-token "$GH_TOKEN" .`; the substring `continue-on-error` occurs nowhere; no **raw** line matches
   `^\s*(?:- )?if:` anywhere in the file (catches `if:` as a later key and `- if:` as a step's first key; there are
   none today; any future conditional must update this test on purpose); a stripped line equals
   `GH_TOKEN: ${{ github.token }}`. Install path: within the `audit-workflows` job block (from the line
   `  audit-workflows:` to the next non-blank line at indentation ≤ 2, or end of file), a stripped line equals
   `run: pip install --require-hashes -r requirements-dev.txt`. Suppression channels: no workflow file contains the substring `zizmor: ignore`, and
   none of `ROOT/".github/zizmor.yml"`, `ROOT/".github/zizmor.yaml"`, `ROOT/"zizmor.yml"`, `ROOT/"zizmor.yaml"` exists
   (the four names 1.30.1 discovers, measured). `requirements-dev.in` has a stripped line `zizmor`; `requirements-dev.txt` has a
   line starting `zizmor==` whose next line, stripped, starts with `--hash=sha256:`.
5. `test_ci_push_trigger_runs_only_on_main` — in `ci.yml`, the lines from `on:` up to (excluding) the next
   **non-blank** line whose first character is not a space (blank lines never end the block), with comment-only and
   blank lines removed, equal exactly
   `["on:", "  push:", "    branches: [main]", "  pull_request:"]`.

All five are written first and run against the unchanged files: each must fail (red) for the expected reason, then pass
after the edits. The existing `test_ci_workflow_requests_read_only_token_permissions` must still pass unchanged.

### Documentation (exact replacement text)

- `docs/setup.md`, "Verify the code works": add `zizmor --offline .` as a third line in the fenced block (after `ruff check .`,
  before `pytest`). Replace line 32 entirely with:
  "This is the fast path to confirm the codebase is sound — no generated data, and no external downloads beyond what the
  dependency install already fetched (it does need a JDK on `PATH` for the PySpark tests). It's what CI runs on every
  push to `main` and on every pull request. `zizmor` audits the GitHub Actions workflows. `zizmor --offline .` (the
  command above) never touches the network and skips the online audits CI runs, which query the GitHub API
  (known-vulnerable actions, and a check that each pin's `# vX.Y.Z` comment matches its SHA);
  `GH_TOKEN=$(gh auth token) zizmor .` reproduces CI's audit exactly. A plain `zizmor .` goes online whenever
  `GH_TOKEN`, `GITHUB_TOKEN` or `ZIZMOR_GITHUB_TOKEN` is set."
- `README.md` layout block line 156 `.github/workflows/              # CI: ruff + pytest` becomes
  `.github/workflows/              # CI: ruff + pytest, plus a zizmor workflow audit; actions pinned to commit SHAs`,
  plus a new next line `.github/dependabot.yml          # weekly update PRs for the pinned actions` (`#` in column 33,
  as in the rest of the block).
- `tests/test_docs.py` docstring bullet (line 17) "- CI: the workflow requests read-only token permissions." becomes
  "- CI: the workflow requests read-only token permissions, runs pushes only on main, pins every action to a commit SHA
  with a version comment, does not persist checkout credentials and runs zizmor online with a failing exit code;
  Dependabot covers github-actions only (weekly, cooldown, grouped)."
- CLAUDE.md — re-read the file immediately before editing (another session may be editing it). Three edits (the third
  is the test count, see Data handling):
  1. "Repo hygiene" > CI bullet (line 88): replace the two sentences from "GitHub Actions from day one" up to and
     including "keyed on the three lockfiles." with:
     "GitHub Actions from day one, running `ruff check .` + `pytest` (job `lint-and-test`) and a zizmor workflow audit
     (job `audit-workflows`) on every push to `main` and every pull request — no GCP credentials needed. `push` is
     restricted to `main` (since 2026-10-10): the earlier claim that the `concurrency` group de-duplicated the push+PR
     double trigger was wrong, because `github.ref` is `refs/heads/<branch>` for push and `refs/pull/<N>/merge` for
     pull_request. The `concurrency` group is per PR for pull requests (superseded runs cancelled) and per commit SHA
     for pushes to `main`, so no run on `main` is cancelled, not even while pending. `setup-python`'s pip cache is keyed on the three lockfiles in
     `lint-and-test` and on `requirements-dev.txt` in `audit-workflows`."
     Replace "and the action versions are on Node 24 (`checkout@v5`, `setup-java@v5`, `setup-python@v6`)" with
     "and every action is pinned to a full commit SHA with a `# vX.Y.Z` comment (the current versions are in
     `.github/workflows/ci.yml`; Dependabot proposes updates, so they are deliberately not repeated here)" (changed in
     pre-commit review round 1: hard-coded versions would go stale with the first Dependabot merge).
     Replace "Since 2026-10-09 the job is a `python-version` matrix" with "Since 2026-10-09 `lint-and-test` is a
     `python-version` matrix".
     Append at the end of the bullet: "**Supply-chain baseline (2026-10-10, v2 PRD FR0.2, design in
     `notes/eg-new-feature/ci-supply-chain-hardening-2026-10-10.md`):** every `uses:` is pinned to a full commit SHA with
     a `# vX.Y.Z` comment, and checkout does not persist credentials. `.github/dependabot.yml` proposes updates weekly
     for **github-actions only**, with a 7-day cooldown and minor+patch grouped; pip is deliberately excluded because it
     would fight the pip-compile hash lockfiles, the hand-maintained Linux extras and the pandas/numpy holds. zizmor
     (hash-pinned in `requirements-dev.txt`) runs **online** in `audit-workflows`, so CI now calls the GitHub API and
     that job can turn red without a commit when an advisory appears. A red job is a signal, not a merge gate: `main`
     has no branch protection or ruleset."
  2. "Build & test commands", line 105 (the `- Lint:` bullet): keep its text and append, after the closing
     parenthesis: "; workflow audit: `zizmor --offline .` (`GH_TOKEN=$(gh auth token) zizmor .` also runs CI's online
     audits)".

## UX flow

No data pipeline. CI on a push to `main` or any PR: job `lint-and-test` (matrix 3.12 + 3.14: checkout pinned without
persisted token → setup-java → setup-python → hash-locked install → `ruff check .` → `pytest`) and, in parallel, job
`audit-workflows` (checkout → setup-python 3.14 → hash-locked dev install → `zizmor --gh-token … .`).
Out of band, weekly: Dependabot reads `.github/dependabot.yml` on the default branch and opens PRs bumping SHA + comment;
each PR runs the CI above once; the owner reviews and merges.

## Data handling

No Synthea data, gold table, split, label, feature, model, seed or MLflow artifact is read or changed; train/test
integrity and result reproducibility are unaffected. CI toolchain reproducibility improves (pinned actions, hash-locked
zizmor).

**Working tree and the other in-flight feature:** at design time the tree holds another feature's uncommitted work
(13 modified `src/`/`tests/` files, label-horizon-purge, modified 2026-10-10 14:19-14:23, not by this session). Its
design note (`notes/eg-new-feature/label-horizon-purge-2026-10-09.md`) also plans edits to `README.md`,
`docs/setup.md` and `CLAUDE.md` (incl. the test count), which are surfaces of this change too. Rules:
- This change never stashes, reverts or commits the other feature's work.
- **Before each edit to a shared file** (`README.md`, `docs/setup.md`, `CLAUDE.md`, `tests/test_docs.py`): run
  `git diff --stat -- <file>`; re-read the file; apply only this change's edits.
- **At commit time** (owner-authorized only): stage this change's files by name; for any shared file whose
  `git diff` contains hunks not from this change, stage with `git add -p` selecting only this change's hunks, and show
  the owner `git diff --cached` before committing. Never `git add .` / `-a`.
- **CLAUDE.md test count (line 106, "545 tests"):** stale at HEAD already (a clean `git archive HEAD` export collects
  **570**). Correction at implementation: 545 was right at `76a991b`; the drift is from `4f8b016` (the label-horizon-purge
  commit, made by the other session during this design check, +25 tests, CLAUDE.md not updated), not from `91cb37c`. This change sets it to (fresh collection of a clean HEAD export) + 5 = **575**,
  timing text unchanged, and reports the pre-existing drift. If line 106 no longer reads "545 tests" when edited (the
  other feature changed it), leave it and report the conflict instead of guessing.
- **If the other feature's tests go red** between baseline and gate, report it with the failing IDs and their files;
  do not fix or skip them.
- **Test gate count:** re-collect at the gate rather than trusting an earlier N: save
  `pytest --collect-only -q` node IDs before the first edit (baseline) and at the gate; the gate's set minus the
  baseline set must be exactly the five new test IDs, and nothing may disappear except IDs whose file changed outside
  this change (reported). If the other feature changed tests in between, report that explicitly.

## Failure modes

- **GitHub API down or rate-limited:** `audit-workflows` fails; `lint-and-test` is unaffected. Re-run. Fallback if it
  becomes noisy: an `--offline` gate plus a non-blocking online step (would lose the comment↔SHA check as a gate).
- **New advisory for a pinned action:** `audit-workflows` turns red with no commit (accepted, owner decision); fix by
  bumping that action by hand (Dependabot alerts and Dependabot security updates are **disabled** on this repo, checked
  2026-10-10 via the GitHub API, so no security PR will arrive; the weekly version PR arrives only after the 7-day
  cooldown).
- **"Blocking" means a failed check, not a merge gate:** `main` has no branch protection and no rulesets (checked
  2026-10-10), so a red `audit-workflows` (or `lint-and-test`) blocks nothing, including a Dependabot PR merge, and
  runs on `main` are after-the-fact signals. Accepted for this change; a ruleset is a named follow-up.
- **Token missing** (should not happen; `github.token` is always issued, including for fork and Dependabot PRs,
  read-only): `--gh-token ""` errors, the job fails visibly instead of silently auditing offline.
- **Dependabot does not rewrite the version comment:** that PR's `audit-workflows` fails on `ref-version-mismatch`; fix
  the comment on the PR. If this recurs on every Dependabot PR, it becomes an owner decision then (hand-fix, or an
  automated comment-fix step) — named follow-up, not pre-built.
- **A future zizmor release adds an audit:** offline audits change only on a deliberate lockfile recompile (seen in that
  change's CI). Online audit results can change at any time (above).
- **Recompile drifts other dev pins:** caught by the exact acceptance rule.
- **A later workflow with tag refs, persisted credentials, or a weakened audit step** (changed command,
  `continue-on-error`, any `if:` incl. `- if:`, missing token env, an inline `zizmor: ignore`, a zizmor config file):
  tests 1, 2, 4 and zizmor fail. Not guarded: a zizmor config passed via `--config`/`ZIZMOR_CONFIG` (would change the
  exact run line or add an env line; the run-line check catches the former, the latter is accepted). Not covered: deleting the whole
  `audit-workflows` job together with its run line would fail test 4 too; renaming the step does not matter.
- **zizmor ages:** Dependabot does not cover pip, so the hash-pinned zizmor is refreshed only by hand (named follow-up
  with a cadence below). Its online audits still read live advisory data in the meantime.
- **`github.token` behaves differently from the personal token used for every online measurement** (scopes,
  rate limits): unmeasured before the first push, a named first-push risk. Step 9 distinguishes a clean online run from
  a degraded or erroring one.
- **A pushed branch without a PR gets no CI** (consequence of the push filter, accepted by the owner).
- **Expected noise after the first push:** three major-version Dependabot PRs (checkout v7, setup-java v6,
  setup-python v7; all older than the cooldown). Each is a separate decision, not part of this change.

## Verification criteria

Local, before reporting done:
1. Save `pytest --collect-only -q` node IDs before any edit (baseline; see Data handling for the comparison rule).
2. The five new tests fail on the unchanged files (red, for the stated reasons), then pass after the edits.
3. `ruff check .` clean (also on a clean export of the tracked files, per CLAUDE.md); full `pytest` green; collected IDs = baseline + the
   five new tests (per Data handling).
4. In the repo, both `zizmor --offline .` and `GH_TOKEN=$(gh auth token) zizmor .` print exactly
   `No findings to report. Good job! (2 suppressed)` as their last line and exit 0 (the 2 suppressed are the
   pedantic-persona `anonymous-definition` infos, one per job, as measured on the scratch end state with 1.30.1).
   `zizmor --persona=pedantic .` lists exactly those 2 infos and nothing else. Also run the literal CI line,
   `GH_TOKEN=$(gh auth token) bash -c 'zizmor --gh-token "$GH_TOKEN" .'`: same last line, exit 0.
5. Mutation checks on a scratch copy of `.github/` (never the repo): reverting one pin to `@v5` → `unpinned-uses`;
   a wrong `# vX.Y.Z` → `ref-version-mismatch` (online); deleting the cooldown → `dependabot-cooldown`. (The tests'
   own detection power is shown by step 2's red phase against the unchanged files; no test-side mutation run.)
6. `requirements-dev.txt` diff meets the exact acceptance rule; `pip install --require-hashes -r requirements-dev.txt`
   succeeds in `.venv`; `pip check` clean.
7. Each SHA re-verified at implementation time with `gh api repos/<repo>/commits/<tag>`; if `v5`/`v6` moved since
   2026-10-10, still pin the exact versions named here.

After an owner-authorized push (not part of this change; `main` is 7 commits ahead of `origin/main` today):
8. CI green: `lint-and-test` on both legs and `audit-workflows`.
9. The output of the **"Audit workflows" step** (not the whole job log; pip prints its own `WARNING:` lines) does not
   contain `zizmor is running in offline mode` and has no line matching ` WARN audit: zizmor:`, and ends with `No findings to report. Good job! (2 suppressed)`: that distinguishes "online and clean" from a degraded run
   (WARN) or an erroring one (red with an error line). If it is red or warns because of `github.token`, that is the
   named first-push risk: report it before any further change.
10. Repo Insights → Dependency graph → Dependabot shows the config parsed; the first major-version PRs appear, each runs
   CI once (not twice), and their diffs replace both SHA and comment.

## Out-of-scope follow-ups

- Owner-gated repo settings (outward-facing; after the push, with explicit go-ahead): a ruleset on `main` requiring
  the `lint-and-test` and `audit-workflows` checks (makes "blocking" a merge gate for PRs); enabling Dependabot alerts
  and Dependabot security updates (so advisories arrive as PRs that bypass the cooldown).
- Enable GitHub's repo-level "require actions to be pinned to a full-length commit SHA" policy after the push (FR0.2).
- Review the three major-version Dependabot PRs one at a time (read each changelog).
- Mark FR0.2's SHA-pin, Dependabot, zizmor and `persist-credentials` bullets "Done in <commit>" in the v2 PRD once
  committed; "no `pull_request_target`" and job-level `id-token` remain rules for the v2 workflows (zizmor's
  `dangerous-triggers` and `excessive-permissions` audits cover them).
- If Dependabot does not rewrite comments, decide hand-fix vs an automated fix step.
- Pin the runtime toolchain downloads (JDK / Python patch) if exact reproducibility of CI's interpreter matters.
- Refresh zizmor by hand: `pip-compile --upgrade-package zizmor` (same 3.12 recipe, same acceptance rule except the
  zizmor block may change) monthly, or when a zizmor release adds audits relevant to the v2 workflows; re-measure
  verification step 4 on each bump.
- pre-commit hook running ruff + zizmor (FR0.1 / OQ6); optional job `name:`s; SARIF upload.
- Pre-existing: a CI check that `requirements*.txt` still match their `.in` files.

## Round-1 resolution log

Critic: (1) online audits can turn CI red without a commit — accepted explicitly, owner chose online+blocking; online
audit list given, incl. measured `ref-version-mismatch` behaviour. (2) audit hiding test results — separate job.
(3) push/PR double run — fixed with `push: branches: [main]`, false claims corrected in ci.yml and CLAUDE.md.
(4) local scan of gitignored dirs — rebutted by measurement: zizmor honors `.gitignore`. (5) dirty tree — Data
handling + baseline-relative count + stage by name. (6) test-2 ambiguity — step-block algorithm specified; block-scalar
false match accepted. (7) test-3 weakness — exact non-comment content match. (8) neutered audit — exact command,
no `continue-on-error`. (9) silent offline — `--gh-token "$GH_TOKEN"` (empty is a hard error, measured); env var
names verified. (10) acceptance rule — exact `+`-only rule; zizmor has no deps; cache key change noted. (11) comment
rewrite recurrence → named follow-up; transitive actions — measured none (JS actions), toolchain-download residual
named. (12) CLAUDE.md edit — exact content specified.
Readiness: (1) test count — baseline + 5, other feature's files untouched and staged separately. (2) step start
algorithm — specified (test 2). (3) real-tree findings — measured clean, `.gitignore` honored. Line numbers corrected
(README 156, setup.md 27-32).

## Round-2 resolution log

Readiness: implementation ready; its two wording notes (setup.md line 32, CLAUDE.md "every push/PR" and the
`checkout@v5` mention) are covered by the exact replacement text above.
Critic: (1) setup.md line 32 "every push" / "no external downloads" — full replacement sentence given. (2) CLAUDE.md
line 88 stale "every push/PR", version mention, pip-cache sentence — exact replacements given. (3) "blocking" not
enforced (no branch protection/rulesets, verified by the critic) — defined as a failed check, ruleset named as an
owner-gated follow-up, recorded in CLAUDE.md. (4) Dependabot security updates disabled — failure mode corrected to a
manual bump; enabling alerts + security updates added as an owner-gated follow-up. (5) CLAUDE.md "Build & test
commands" — zizmor added to the Lint line, with the offline/online note. (6) exact expected zizmor output and suppressed
count specified (measured: identical last line offline and online, exit 0); expected zizmor version 1.30.1, with the
re-measurement rule if the recompile resolves another version.

## Round-3 resolution log

Readiness: (1) CLAUDE.md test count — set to 550 (committed 545 + 5); report instead of summing if the other feature
changed it first.
Critic: (1) other feature also edits README/setup.md/CLAUDE.md — per-file diff check before each edit, `git add -p` at
commit time, owner sees `git diff --cached`. (2) test-count ownership — as readiness (1). (3) "the job" — replaced with
`lint-and-test`. (4) measurement wording — corrected; the critic's re-run with the exact content confirms the result.
(5) zizmor ageing — named follow-up with a monthly cadence. (6) `.python-version` patch dependence — audit job uses
`"3.14"` like `lint-and-test`. (7) `github.token` unmeasured — named first-push risk; verification step 9 tells clean,
degraded and erroring runs apart. (8) `if:` bypass — test 4 asserts no `if:` in ci.yml. (9) test-5 stop rule —
blank lines never end the block. (10) count gate — node-ID set comparison at the gate, not a frozen N.

## Round-4 resolution log (revision 4, owner: "apply the 7 fixes, re-run B/C")

Readiness: implementation ready. Critic: (1) `- if:` bypass — raw-line regex `^\s*(?:- )?if:`. (2) zizmor suppression
channels — measured the four config names 1.30.1 discovers and that a config silences findings; test 4 asserts no
`zizmor: ignore` and none of the four files; `--config`/`ZIZMOR_CONFIG` env named as accepted. (3) diff headers —
acceptance rule defined on content lines with the exact pipeline; expected block position named. (4) literal CI command
— measured (last line as expected, exit 0) and added to verification 4. (5) WARN check — scoped to the step, exact
offline-warning text quoted. (6) setup.md — documents `zizmor --offline .` as the local command; plain `zizmor .`
online-if-token behaviour stated. (7) cancelled `main` runs — `cancel-in-progress: ${{ github.event_name ==
'pull_request' }}` (measured zizmor-clean), comment and CLAUDE.md text updated.

## Round-5 resolution log (revision 5, owner: "apply fixes, override and proceed")

Readiness: implementation ready (its non-blocking note on a red other-feature suite is now a rule in Data handling).
Critic: (1) stale 545 — verified HEAD collects 570; CLAUDE.md count = fresh HEAD collection + 5 = 575, drift reported.
(2) pending-run cancellation on `main` — verified in GitHub's concurrency docs ("any existing pending job or workflow in
the same concurrency group will be canceled"); group keyed per commit SHA for pushes, per ref for PRs; measured
zizmor-clean (exit 0, same 2 suppressed). (3) install path unguarded — test 4 asserts the hash-locked install line
inside the `audit-workflows` block. The critic did not re-review revision 5 (owner override).
