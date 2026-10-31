# Monitoring: laptop-timer burn-in (PR 1) and weekly retry slots with a skip check (PR 2)

Follow-up to `notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md` (hereafter "the slice-7 note"). It
amends PRD `notes/prds/v2-mlops-keyless-scheduled-scale-up-2026-10-09.md` FR19 and M3 (owner decisions below). One
design, two PRs: **Part 1 = PR 1**, merged first; the burn-in runs on it; **Part 2 = PR 2**, merged after the burn-in.

## Owner decisions (Step 0, 2026-10-20)

- **E1, burn-in trigger:** the accelerated burn-in is no longer driven by GitHub's scheduler. A timer on the owner's
  laptop dispatches `monitor.yml` with a new input `cadence=burn_in`, one tick at a time, at any hour (an afternoon is no
  longer required). Rows carry `cadence = burn_in`, `trigger = workflow_dispatch`; the run is titled
  "Monitoring tick (burn-in)".
- **E2, M3 amended:** M3 becomes "≥ 5 consecutive green **timer-dispatched burn-in** runs, then ≥ 1 green **scheduled
  weekly** tick". The "scheduled" requirement moves from the burn-in to the weekly part only. This is a change to a
  pre-registered success metric, disclosed here and in the PRD.
- **E3, weekly slots:** the weekly cron becomes hourly Monday to Wednesday, `17 * * * 1-3` (72 slots), replacing
  `17 6 * * 1`.
- **E4, skip check and retry cap:** a scheduled weekly run skips (does nothing, writes no row, stays green) when this
  week's weekly tick has already succeeded, or when 3 weekly attempts this week have already failed or been refused.
- **E5, delivery:** two PRs (Part 1, then Part 2 after the burn-in).
- **E6, design doc on disk:** this file.

**Methodology disclosure.** E2 changes a pre-registered success metric (PRD M3) *after* watching the scheduled burn-in
fail to deliver slots. **The bar is lower in one respect, stated plainly:** FR19 says the burn-in proves "repeatability,
idempotency and running without the owner". A timer on the owner's laptop *is* the owner. The amended burn-in therefore
proves repeatability and idempotency (≥ 5 consecutive green ticks at an accelerated cadence, each a full Actions run on
`main` with keyless credentials), but not unattended operation. The only evidence of running without the owner is the
scheduled weekly tick(s) that M3 still requires (≥ 1). E3 also relaxes a recorded scope line: PRD "Out: a daily cron" and
OQ1. The relaxation is needed because 72 hourly slots a week fire more often than daily. At most one *scheduled weekly*
tick succeeds per week, barring owner dispatches or re-runs, which can move the clock further (Failure modes); the
extra slots are retries that do nothing once the week's tick has run. Both are recorded as
post-outcome owner decisions in the PRD (Records).

## Why

GitHub's `schedule` event is best-effort, and for this repository it has dropped almost every slot (measured below).
Neither the accelerated burn-in (FR19: "≥ 5 consecutive green ticks" in one afternoon of 30-minute slots) nor a
single-slot weekly tick can work reliably at this rate. Diagnosis (conversation 2026-10-20, read from the GitHub API and
githubstatus.com):

| Workflow | Cron | Slots due | Runs started |
|---|---|---|---|
| Monitoring tick, 2026-10-19 | `7,37 13-18 * * *` | 12 | 1 (20:08 UTC, 91 min after the last slot) |
| Monitoring tick, 2026-10-20 | same | 12 | 2 (runs created 17:55 and 21:31 UTC) |
| Schedule probe (`schedule-probe.yml`, no permissions, prints the time) | `11,26,41,56 * * * *` | ≈ 120 by 2026-10-20 22:20 UTC | 7 |

- Ruled out: workflow state (active), default branch, cron syntax, repository visibility/archival, the 60-day
  inactivity rule, concurrency cancellations (none listed), SHA-pinning (fails runs, does not suppress them), plan and
  billing (Free plan, Actions net charge 0; public-repository minutes are free).
- A GitHub incident (2026-10-19 15:49-16:56 UTC, "Degraded availability for GitHub Actions") explains at most two
  missed slots. The rest is unexplained; GitHub documents that scheduled runs may be delayed or dropped under load. The
  owner decided not to open a support ticket and to make the design independent of slot reliability.
- The three scheduled burn-in ticks that did run were all green and advanced the clock (rows in `serving.monitoring`:
  2023-09-04 run 39316779856, 2023-09-11 run 39470293730, 2023-09-18 run 39501115506). They stay in the history and in
  the ledger; they do not count towards the amended M3 (E2).

## Scope

**Part 1 (PR 1):**
1. `monitor.yml`: a `cadence` dispatch input (`dispatch` | `burn_in`), a dynamic `run-name`, and the burn-in
   `schedule:` block removed (cron state "none").
2. The clock accepts `cadence=burn_in` only on `workflow_dispatch` and never together with a replay date.
3. `deploy/monitoring.json`: `crons` holds only `weekly` (the burn-in cron is retired); the weekly value stays
   `17 6 * * 1` until Part 2.
4. A laptop timer (`scripts/burn_in_timer.py`, logic in `src/readmission_risk/monitoring/burn_in.py`, `make
   burn-in-timer`).
5. M3 amended in `scripts/m3_check.py`.
6. The ledger labels burn-in ticks by trigger and its header text is corrected.
7. `schedule-probe.yml` deleted.
8. Records: PRD amendment, slice-7 note amendment + Results, `docs/setup.md`, CLAUDE.md, README's operations row.

**Part 2 (PR 2):**
1. `WEEKLY_CRON = "17 * * * 1-3"` (config, constant, workflow).
2. The clock's weekly skip check (E4).
3. Skip-aware workflow job conditions.
4. M3 treats a skip run as neutral.
5. Records.

**Out:**
- External schedulers (Cloud Scheduler, a GitHub token in Secret Manager).
- Moving the tick off GitHub Actions.
- A GitHub support ticket.
- Changing data tests, the BigQuery schema, Terraform/IAM, or the alert rule.


## Surfaces touched

| Surface | Part | Change |
|---|---|---|
| `.github/workflows/monitor.yml` | 1, 2 | P1: `cadence` input, `run-name`, clock-step env `REQUESTED_CADENCE`, `schedule:` removed, header comment. P2: `schedule: - cron: "17 * * * 1-3"`, clock output `skip`, job `if:`s |
| `.github/workflows/schedule-probe.yml` | 1 | deleted |
| `deploy/monitoring.json` | 1, 2 | P1: `"crons": {"weekly": "17 6 * * 1"}`. P2: `"crons": {"weekly": "17 * * * 1-3"}` |
| `src/readmission_risk/monitoring/config.py` | 1, 2 | P1: `BURN_IN_CRONS` removed; `crons` exactly `{"weekly"}`; `cadence_for` gains `requested_cadence`; `_PERIODS` only the weekly cron. P2: `WEEKLY_CRON` value |
| `src/readmission_risk/monitoring/tick.py` | 1, 2 | P1: `ClockRequest.requested_cadence`; refuse burn_in + replay. P2: `prepare_tick(..., now=None)`, skip check, `ClockResult.skip_reason` |
| `src/readmission_risk/monitoring/weekly.py` (new) | 2 | pure skip rule |
| `src/readmission_risk/monitoring/burn_in.py` (new) | 1 | pure timer loop + `BURN_IN_TITLE` |
| `scripts/burn_in_timer.py` (new) | 1 | Typer CLI over `burn_in.py` |
| `scripts/monitor_tick.py` | 1, 2 | P1: `clock` reads `REQUESTED_CADENCE`. P2: skip output |
| `scripts/m3_check.py` | 1, 2 | P1: amended M3, two run lists. P2: `skipped` verdict |
| `src/readmission_risk/monitoring/ledger.py` | 1 | `cadence_label(cadence, trigger)`, `HEADER` text, `cron_state` |
| `Makefile` | 1 | `burn-in-timer` target, `.PHONY`, `BURN_IN_TICKS` / `BURN_IN_INTERVAL`, `CAFFEINATE`; `m3-check` help text |
| tests | 1, 2 | listed under Verification (new and changed, so the semantic test-change count is visible) |
| `notes/prds/…2026-10-09.md`, slice-7 note, `docs/setup.md` (Monitoring tick), `CLAUDE.md`, `README.md` (operations table, row "Run the monitoring tick") | 1, 2 | records (see Records) |

Unchanged on purpose:
- `monitoring.sql`: MON-11 already allows `burn_in` with any trigger.
- `warehouse/schemas/monitoring.json`: its `cadence` description is kept, because a description change shows up in
  `terraform plan` as an in-place change.
- Terraform: the WIF provider already accepts `workflow_dispatch` and `schedule` on `main`.
- `score.yml`.

## Interfaces

### Part 1

**`config.py`**
- Delete `BURN_IN_CRONS`.
- `WEEKLY_CRON = "17 6 * * 1"`.
- `_PERIODS = {WEEKLY_CRON: timedelta(days=7)}`.
- `MonitoringConfig.crons` is a `dict[str, str]` holding exactly `{"weekly"}`.
- The parser raises `ValueError(f"monitoring.json: crons must have exactly the key weekly; got {crons!r}")` for a
  non-dict or any other key set. It keeps the existing `crons.weekly` message for any other value.
- `cadence_for(event_name, schedule, config, requested_cadence=None)`. First `requested_cadence = requested_cadence or
  None`. Then, in order:
  - `event_name == "laptop"`:
    - `requested_cadence` not `None` raises `ValueError("a cadence can be requested only on workflow_dispatch")`;
    - otherwise it returns `catch_up`.
  - `event_name == "workflow_dispatch"`:
    - `None` or `"dispatch"` gives `dispatch`;
    - `"burn_in"` gives `burn_in`;
    - anything else raises `ValueError(f"requested cadence must be dispatch or burn_in; got {requested_cadence!r}")`.
  - `event_name == "schedule"`:
    - `requested_cadence` not `None` raises the same message as the laptop case;
    - `schedule == config.crons["weekly"]` gives `weekly`;
    - otherwise it raises the existing `"scheduled by cron {schedule!r}, which deploy/monitoring.json does not name"`.
  - Any other event: the existing error.

**`tick.py` (Part 1)**
- `ClockRequest` gains a last field `requested_cadence: str | None = None`, after `champion_path`.
- `prepare_tick`:
  - its `cadence_for` call becomes `cadence_for(request.event_name, request.schedule or None, config,
    request.requested_cadence)`;
  - immediately after the existing line `record.cadence = cadence`, and before `read_ticks`: if `cadence == "burn_in"
    and request.requested`, raise `ValueError("a burn-in tick processes the clock head; it cannot be combined with
    sim_date")`.
- Recorded results:
  - `_classify` maps that ValueError to `refused`, so the row is `refused`/`clock` with `cadence = burn_in`;
  - a refusal inside `cadence_for` leaves `cadence` null, as today.

**`scripts/monitor_tick.py clock` (Part 1)**
- The clock builds its request as
  `ClockRequest(_event_name(), os.environ.get("SCHEDULE") or None, requested, config_path=config,
  requested_cadence=os.environ.get("REQUESTED_CADENCE") or None)`.
- No new CLI option.

**`monitor.yml` (Part 1)**
- Added under `workflow_dispatch.inputs`, after `sim_date`:
  ```yaml
      cadence:
        description: "dispatch (default) or burn_in (used only by the laptop burn-in timer, make burn-in-timer)"
        required: false
        type: choice
        options: [dispatch, burn_in]
        default: dispatch
  ```
- After `name: Monitoring tick`, a top-level line:
  `run-name: ${{ github.event_name == 'workflow_dispatch' && inputs.cadence == 'burn_in' && 'Monitoring tick (burn-in)' || 'Monitoring tick' }}`.
- The clock step's `env:` gains
  `REQUESTED_CADENCE: ${{ github.event_name == 'workflow_dispatch' && inputs.cadence || '' }}`.
- Both are guarded by `event_name`, so neither depends on how GitHub fills `inputs` (or a choice input's default) on a
  schedule event. On schedule, both evaluate to the plain title and `''`, and `or None` normalises `''`.
- The step-env allow-list regex (`^ {10}[A-Z_]+: (?:\$\{\{ [^}]+ \}\}|[^$])+$`) already admits this line: the
  expression contains no `}`.
- The whole `schedule:` block is deleted: the `schedule:` key, all five comment lines under it (three burn-in lines
  and two "Registration note" lines) and the cron line.
- The header comment:
  - the sentence starting "Cron states (exactly one at a time, tests/test_infra.py):" up to "for one afternoon, then the
    weekly cron." is replaced with: "Cron states (exactly one at a time, tests/test_infra.py): none (Part 1 of
    notes/eg-new-feature/monitoring-burn-in-timer-and-weekly-retry-2026-10-20.md), then the weekly retry cron (Part 2);
    the burn-in is dispatched by a laptop timer (make burn-in-timer), not scheduled.";
  - the following sentence "The values below must match deploy/ and infra/." is kept.

**`burn_in.py` (pure: no `subprocess`, no wall clock of its own)**
```python
BURN_IN_TITLE = "Monitoring tick (burn-in)"
GROUP_WORKFLOWS = ("monitor.yml", "score.yml")   # share concurrency group "score"

@dataclass(frozen=True)
class TimerSettings:
    ticks: int = 5                                    # inclusive 1..12
    interval: timedelta = timedelta(minutes=30)       # inclusive 10..120 minutes, compared as timedelta
    run_timeout: timedelta = timedelta(minutes=150)   # > monitor.yml's 100 min of job timeouts + queueing
    idle_timeout: timedelta = timedelta(minutes=150)
    poll_seconds: float = 30.0
    max_consecutive_drops: int = 3
    blocked_weekdays: frozenset[int] = frozenset()    # UTC weekday() values on which no dispatch starts (Part 2 CLI: {0, 1, 2})
    # __post_init__ raises ValueError(f"ticks must be in [1, 12]; got {ticks}") /
    # ValueError(f"interval must be between 10 and 120 minutes; got {interval}")

@dataclass(frozen=True)
class TickOutcome:
    run_id: int
    url: str
    kind: str          # green | red | dropped
    conclusion: str

class GhError(RuntimeError): ...
class BurnInStop(RuntimeError):
    def __init__(self, code: int, message: str, outcomes: list[TickOutcome]):
        super().__init__(message); self.code = code; self.outcomes = list(outcomes)

def classify_run(view: dict) -> str | None
def run_burn_in(settings: TimerSettings, *, gh: Callable[[list[str]], str], now: Callable[[], datetime],
                sleep: Callable[[float], None], log: Callable[[str], None]) -> list[TickOutcome]
```
**The `gh` callable**
- `gh(args)` returns the **raw text** and never parses it. It raises `GhError(f"gh {' '.join(args)}: {stderr.strip()}")`
  on a non-zero exit. On success it returns stdout alone for every call containing `--json`, and
  `f"{stdout}\n{stderr}"` for every other call (only `workflow run`, whose run URL gh may print on either stream).
  `run_burn_in` parses the `--json` replies with `json.loads`. The CLI implements `gh` with `subprocess.run(["gh",
  *args], capture_output=True, text=True, check=False)`; the test fakes return strings the same way.
- **Repository pinned:**
  - `burn_in.py` defines `REPO = "fma00/synthea-readmission-risk"`.
  - Every **`run` and `workflow`** argument list in this design is prefixed with `["-R", REPO]`, for example
    `gh(["-R", REPO, "workflow", "run", …])`. The lists below omit the prefix for brevity; the tests assert it on every
    `run`/`workflow` call.
  - **`auth status` takes no `-R`.** gh 2.102.0 rejects it ("unknown shorthand flag: 'R'", verified 2026-10-21), so
    the start check's call is exactly `["auth", "status", "--hostname", "github.com", "--active"]`, and a test asserts that exact
    list.
  - `scripts/m3_check.py`'s two `run list` calls and its `run view` calls gain the same prefix.
- **Read-only retries:** `burn_in.py` also defines
  `def with_retries(gh: Callable[[list[str]], str], sleep: Callable[[float], None], *, attempts: int = 4, delay_seconds: float = 30.0) -> Callable[[list[str]], str]`.
  - **Wrapping:** the **CLI** wraps its subprocess `gh` with it and passes the wrapped callable to `run_burn_in` and to
    the start check. `run_burn_in` itself never retries, so its unit tests (fake `gh`) see no retry sleeps.
  - **Read-only detection:** first drop a leading `["-R", REPO]` if present; the call is read-only iff its first two
    elements are `["run", "list"]`, `["run", "view"]` or `["auth", "status"]`.
  - **Retry rule:** a read-only call that raises `GhError` is retried after `sleep(delay_seconds)`, for at most
    `attempts` calls in total; the last `GhError` propagates.
  - **`workflow run` is never retried:** it is not idempotent, because a failed dispatch call may still have created a
    run.
  - **Retries count against the deadlines.** The idle, run and 120 s identification deadlines are all measured with
    `now()`, so retry sleeps inside a call count against them.
  - A transient GitHub or Wi-Fi error no longer ends an overnight session.
- `now()` must return an aware UTC datetime. GitHub's `createdAt` strings (`…Z`) are parsed with
  `datetime.fromisoformat(s.replace("Z", "+00:00"))`.

**`classify_run(view)`**, where `view` is the parsed `gh run view --json status,conclusion,attempt,jobs,url`:
- `view["status"] != "completed"` gives `None`;
- `conclusion == "success"` gives `"green"`;
- `conclusion == "cancelled"` and the job named `clock` is absent, or its `startedAt` is falsy or starts with
  `"0001"`, gives `"dropped"` (the same rule as `m3_check.classify`);
- otherwise `"red"`.

"Green" here means only that the Actions run concluded `success`. `m3_check` is the authority: it also requires
`attempt == 1` (always true for a dispatched run the timer never re-runs) and a succeeded `serving.monitoring` row for
the run URL. The timer's final message says so.

**Start (once, at the top of `run_burn_in`, before the loop; its `GhError` and parse errors go through step 6 like any
other call).**
- Parse `gh(["run", "list", "--workflow", "monitor.yml", "--event", "workflow_dispatch", "--json",
  "databaseId,createdAt,status,conclusion,displayTitle,url", "--limit", "100"])`. "Newest" means the maximum
  `createdAt`; the order gh returns the list in is not relied on.
- Log the newest run with `displayTitle == BURN_IN_TITLE`: `f"previous burn-in run: {status} {conclusion or '-'}
  {url}"`, or "previous burn-in run: none among the last 100 dispatch runs".
- If it completed with a conclusion other than `success`, add the line `f"WARNING: the previous burn-in run concluded
  {conclusion}; unless it was cancelled before its clock started (dropped), M3's streak restarts with this session;
  make m3-check decides"`. The list carries no `jobs`, so the timer cannot tell dropped from red, and the wording says
  so.
- No stop: the run in flight is covered by the idle wait.

**`run_burn_in` loop.** `outcomes = []`, `greens = 0`, `drops = 0`. Repeat until `greens == ticks`:
1. **Wait for idle.**
   - `wait_start = now()`.
   - Loop: for each `w` in `GROUP_WORKFLOWS`, call `gh(["run", "list", "--workflow", w, "--json", "status", "--limit",
     "20"])`.
   - If no listed run has `r["status"] != "completed"`, stop waiting. Use item access, not `.get`, so a reply without
     `status` raises `KeyError`, which step 6 turns into exit 2.
   - Elif `now() - wait_start >= idle_timeout`, raise `BurnInStop(2, f"a monitor.yml or score.yml run stayed active for
     {int(idle_timeout.total_seconds() // 60)} minutes", outcomes)`.
   - Else `sleep(poll_seconds)`.
2. **Dispatch.**
   - **Weekday guard, checked here, immediately before every dispatch** (after the idle wait and after any post-green
     sleep, so a wait that crosses midnight is caught): if `now().weekday() in blocked_weekdays`, raise
     `BurnInStop(2, "the weekly retry slots run Monday to Wednesday (UTC); the burn-in timer does not dispatch on those
     days, so resume on Thursday", outcomes)`. The CLI also checks once at start, before the Start step.
   - `before` is the set of `databaseId`s from `gh(["run", "list", "--workflow", "monitor.yml", "--event",
     "workflow_dispatch", "--json", "databaseId", "--limit", "20"])`.
   - `t0 = now()`.
   - `out = gh(["workflow", "run", "monitor.yml", "--ref", "main", "-f", "cadence=burn_in"])`.
3. **Identify the run.**
   - If `re.search(r"/actions/runs/(\d+)", out)` matches, `run_id = int(group(1))`.
   - Otherwise, until `now() - t0 >= 120 s`, poll `gh(["run", "list", "--workflow", "monitor.yml", "--event",
     "workflow_dispatch", "--json", "databaseId,createdAt,displayTitle", "--limit", "20"])`, sleeping `poll_seconds`
     between polls.
     - Candidates are the runs with `databaseId not in before` and `displayTitle == BURN_IN_TITLE`. No time filter is
       applied, so the laptop's clock skew does not matter. `before` holds the 20 newest dispatch runs, and the timer
       dispatches one run at a time.
     - Exactly one candidate is the run.
     - Two or more raises `BurnInStop(2, "could not identify the dispatched run: <n> candidates <ids>", outcomes)` at
       once.
     - None by the deadline raises `BurnInStop(2, "could not identify the dispatched run: none within 120 s",
       outcomes)`.
4. **Wait for completion.** Loop, with the first view made immediately:
   1. `view = gh(["run", "view", str(run_id), "--json", "status,conclusion,attempt,jobs,url"])`.
   2. If `classify_run(view)` is non-None, stop. A completed run wins even when the timeout has passed.
   3. Elif `now() - t0 >= run_timeout` (measured from the dispatch), raise `BurnInStop(2, f"run
     {url} did not finish within {int(run_timeout.total_seconds() // 60)} minutes of its dispatch", outcomes)`, where
     `url` is the last `view["url"]`.
   4. Else `sleep(poll_seconds)`.
5. **Act on the outcome.**
   - Append `TickOutcome(run_id, view["url"], kind, view["conclusion"])` and
     `log(f"{now():%Y-%m-%d %H:%M:%S} UTC {kind} {view['conclusion']} {view['url']}")`.
   - `red`: raise `BurnInStop(3, f"tick {url} concluded {conclusion}; the streak is broken: fix the cause and restart
     the timer", outcomes)`.
   - `dropped`:
     - `drops += 1`;
     - if `drops >= max_consecutive_drops`, raise `BurnInStop(2, f"{drops} consecutive dispatched runs were cancelled
       before their clock job started; check the concurrency group 'score'", outcomes)`;
     - else continue at step 1 without sleeping (re-dispatch).
   - `green`:
     - `greens += 1` and `drops = 0`;
     - if `greens == ticks`, return `outcomes`;
     - else `sleep(max(0.0, (t0 + interval - now()).total_seconds()))`. The interval runs start to start, from this
       tick's dispatch.
6. **gh errors.** A `GhError` raised anywhere becomes `BurnInStop(2, f"gh failed: {exc}", outcomes)`. Any `json.JSONDecodeError`, `KeyError`, `TypeError` or `ValueError` raised while
   parsing or reading a gh reply (missing fields included) becomes `BurnInStop(2, f"gh returned unexpected output:
   {type(exc).__name__}: {exc}", outcomes)`. Only the gh-reply handling sits in that `try`; `BurnInStop` itself passes
   through.
7. **Ctrl+C.** A `KeyboardInterrupt` propagates.

**`scripts/burn_in_timer.py` (Typer, `app = typer.Typer(add_completion=False, rich_markup_mode=None,
pretty_exceptions_enable=False)` as in `m3_check.py`)**
- Options:
  - `--ticks` (int, default 5);
  - `--interval-minutes` (int, default 30). It becomes `timedelta(minutes=...)`.
- Refusals, each exit 1 with `ERROR: <message>` on stderr:
  - `GITHUB_ACTIONS == "true"`: "the burn-in timer runs on the laptop only";
  - a `TimerSettings` ValueError: its message.
- Wiring: a module-level `def _now() -> datetime: return datetime.now(UTC)` (the test patch point), `now = _now`,
  `sleep = time.sleep`, `log = lambda s: print(s, flush=True)`.
- **The CLI's `gh`** is a module-level `def _gh(args: list[str]) -> str` implementing the wrapper contract (the
  stdout-only `--json` rule, `OSError` mapped to `GhError`). `main` passes `with_retries(_gh, time.sleep)` to the start
  check and to `run_burn_in`.
- Exit 0: prints `f"burn-in timer: {greens} runs concluded success ({dropped} dropped and re-dispatched); make m3-check
  is the authority for M3"` to stdout. Here `greens = sum(o.kind == "green")` (equal to `--ticks`) and
  `dropped = sum(o.kind == "dropped")`.
- Part 1 constructs `TimerSettings(..., blocked_weekdays=frozenset())`; Part 2 changes this to `frozenset({0, 1, 2})`.
  Part 2's test asserts it.
- `BurnInStop`:
  - prints `ERROR: <message>` to stderr;
  - then one stderr line per outcome, `f"  {o.kind} {o.conclusion} {o.url}"`;
  - exits with `exc.code` (2 or 3).
- `KeyboardInterrupt`: prints "interrupted; the run in flight (if any) continues on GitHub" to stderr and exits 130.
- It needs no cloud credentials. It needs a `gh` whose token can dispatch workflows: the `workflow` scope (classic
  token) or Actions read/write (fine-grained). At start the CLI checks, in order:
  1. the Actions refusal (exit 1);
  2. `TimerSettings` (exit 1);
  3. the weekday guard: Part 2 only, `BurnInStop(2, …)` with the loop's message, exit 2;
  4. `gh auth status --hostname github.com --active` through the wrapper. `--active` limits the check to the active
     account; without it, a stale second stored account makes the command exit 1 (`gh auth status --help`, gh 2.102.0). A failure prints `ERROR: gh failed: <GhError>` and
     exits 2. A missing scope surfaces as the `GhError` of the first `workflow run` (exit 2).
- The CLI's `gh` wrapper maps `OSError` from `subprocess.run` (for example `FileNotFoundError`, `gh` not installed)
  to `GhError("gh is not installed or not executable: <exc>")`, so it exits 2, not with a traceback. It does not require a clean checkout,
  because it dispatches `main` on GitHub.

**`Makefile`**
- `BURN_IN_TICKS ?= 5` and `BURN_IN_INTERVAL ?= 30`. These names were chosen over `TICKS`/`INTERVAL` to avoid
  collisions with the environment.
- `CAFFEINATE := $(shell command -v caffeinate >/dev/null 2>&1 && echo caffeinate -i)`. It is empty off macOS, and `:=`
  means the environment cannot change it.
- The target:
  ```make
  burn-in-timer: ## LIVE: dispatch BURN_IN_TICKS burn-in ticks of monitor.yml, one every BURN_IN_INTERVAL minutes (laptop; needs gh)
  	$(CAFFEINATE) $(BIN)/python scripts/burn_in_timer.py --ticks $(BURN_IN_TICKS) --interval-minutes $(BURN_IN_INTERVAL)
  ```
- `burn-in-timer` is added to `.PHONY`.
- `m3-check`'s help text becomes "Check metric M3 (>= 5 consecutive green timer-dispatched burn-in ticks, then a
  scheduled weekly one), as the ledger-reader SA; needs gh".

**`m3_check.py` (Part 1)**
- `--limit` keeps its name, its default becomes 1000 and its help text becomes "Runs to list per event (dispatch and
  schedule)". Two lists:
  - **burn-in:** `["run", "list", "--workflow", "monitor.yml", "--event", "workflow_dispatch", "--json",
    "databaseId,conclusion,createdAt,url,displayTitle", "--limit", str(limit)]`, filtered to `displayTitle ==
    BURN_IN_TITLE` (imported from `readmission_risk.monitoring.burn_in`);
  - **scheduled:** the same call with `--event schedule`, unfiltered.
  - Both are sorted oldest first by `createdAt`.
- **Cost bound, and how it composes with the pure functions:**
  - `main` views (`gh run view <id> --json attempt,jobs`) and classifies every burn-in run, then calls
    `burn_in_streak`.
  - If the streak is non-empty, `main` walks the scheduled runs with `createdAt > streak[-1].run["createdAt"]`, oldest
    first. It views and classifies one run at a time, appends the verdict to a list `seen`, and stops as soon as
    `first_weekly_after(seen, after)` is not None.
  - The pure function therefore decides; the loop only bounds the number of views.
  - Test `test_m3_views_stop_at_the_first_green_weekly`: with 3 scheduled runs after the streak (red, green weekly,
    green weekly), exactly 2 `run view` calls are made for scheduled runs.
- `classify` is unchanged in Part 1.
- `m3_met` is replaced by two pure functions:
  - `burn_in_streak(verdicts: list[Verdict]) -> list[Verdict]`: the **first 5** consecutive green verdicts with
    `cadence == "burn_in"`. The streak ends at its 5th green and is not extended, so a later timer session can never
    move the weekly-part boundary past a weekly tick that already satisfied M3. `dropped` verdicts are passed over;
    any other non-green verdict, or a green one with another cadence, resets the run of greens. Returns `[]` when no 5
    consecutive greens exist (never a partial streak). **This deliberately changes today's `m3_met`**
    (`scripts/m3_check.py:64-82`), which extends the streak past the 5th green and returns a partial streak when not
    met. The existing assertions that change are in `tests/monitoring/test_cli.py:180-200`:
    - `test_m3_streak_then_weekly`: the evidence stays u1-u6 (5 + weekly);
    - `test_m3_dropped_red_and_rerun`: `len(m3.m3_met(verdicts)[1]) == 5` becomes
      `burn_in_streak(verdicts)` has length 5 and M3 is not met; `m3.m3_met(broken) == (False, [])` becomes
      `burn_in_streak(broken) == []`.
  - `first_weekly_after(verdicts: list[Verdict], after: str) -> Verdict | None`: the first verdict with `run["createdAt"]
    > after` that is green with `cadence == "weekly"`. **Earlier red, dropped or (Part 2) skipped scheduled runs after
    the streak are passed over; they do not invalidate it** (as in the slice-7 rule).
  - M3 is met iff `streak` is non-empty and `first_weekly_after(scheduled, streak[-1].run["createdAt"])` is not None.
- Other dispatches (manual, `cadence = dispatch`, other titles) are not listed, so they neither break nor extend the
  streak.
- Printed evidence:
  - one line per burn-in verdict, then one per viewed scheduled verdict, each
    `f"{prefix:<9} {v.run['createdAt']} {v.kind:<7} {v.cadence or '-':<8} {v.run['url']}"` with prefix `burn-in` or
    `scheduled`;
  - with no streak, no scheduled run is viewed, and the line `scheduled not viewed (no burn-in streak yet)` is printed
    instead;
  - then the `M3 MET` / `M3 NOT MET` line as today.
  - The scheduled burn-in runs of 2026-10-19/20 precede any timer streak, so they are never viewed, never printed and
    never counted.
- Docstring: first paragraph "Checks metric M3 (PRD, amended 2026-10-20): ≥ 5 consecutive green timer-dispatched burn-in
  ticks, then ≥ 1 green scheduled weekly tick." The second paragraph, which is the `--help` text, becomes: "Burn-in
  runs are the workflow_dispatch runs titled 'Monitoring tick (burn-in)'; weekly runs are the scheduled runs. A run
  counts green only if its Actions conclusion is success, its attempt is 1, AND serving.monitoring holds a succeeded
  row for its URL with the expected cadence. A cancelled run that never started its clock job is dropped (neither green
  nor red); in Part 2 a scheduled run whose score job was skipped is a skip (neutral). The streak is the first 5
  consecutive green burn-in runs; M3 is met by the first green weekly run after it, and red or skipped weekly runs in
  between do not invalidate it. Needs `gh` and BigQuery read access (laptop: the ledger-reader SA). Exit 0 met, 3 not
  met, 2 on a gh or BigQuery error." The Part 1 text omits the Part 2 skip clause.

**`ledger.py` (Part 1)**
- Remove `"burn_in"` from `CADENCE_LABELS`.
- New `cadence_label(cadence, trigger) -> str`:
  - `burn_in` with `trigger == "schedule"` gives `"burn-in (scheduled, accelerated)"`;
  - `burn_in` with any other trigger gives `"burn-in (dispatched, accelerated)"`;
  - otherwise `CADENCE_LABELS.get(cadence, "-")`.
- `render_ledger` calls `cadence_label(row.get("cadence"), row.get("trigger"))`.
- `HEADER`: the sentence "ticks labelled burn-in (accelerated cadence) are scheduled every 30 or 60 minutes on a burn-in
  afternoon; weekly ticks are scheduled on Mondays." is replaced. **Part 1 (changed in the pre-commit review, round 1,
  because the retry slots do not exist before Part 2 and the header is published on every tick):** "… dispatched by a
  timer on the owner's laptop (dispatched); after the burn-in, weekly ticks are scheduled once a week." **Part 2**
  replaces the whole sentence with: "ticks labelled burn-in ran at an accelerated cadence, either fired by GitHub's scheduler (scheduled) or, because
  that scheduler dropped most slots, dispatched by a timer on the owner's laptop (dispatched); weekly ticks are
  scheduled, with retry slots that normally stop once the week's tick has succeeded."
- `cron_state(workflow_text, config)` (`ledger.py:95-106`): the accepted set becomes `(config.crons["weekly"],)`; the
  error text is kept. `last_tick_badge` is unchanged: cron `None` gives "dispatch only", Part 1's state.

### Part 2

**`config.py`**
- `WEEKLY_CRON = "17 * * * 1-3"`.
- `period_of(WEEKLY_CRON) == timedelta(days=7)`.

**`ledger.py` (Part 2): the badge's staleness basis**
- In `last_tick_badge`, the line `scheduled = ordered[ordered["trigger"] == "schedule"]` becomes
  `scheduled = ordered[(ordered["trigger"] == "schedule") & (ordered["cadence"] == "weekly") & (ordered["status"] ==
  "succeeded")]`.
- "Stale after" is therefore the newest *succeeded weekly* tick + 14 days. The scheduled burn-in rows of 2026-10-19/20
  and failed attempts no longer set it, and with no such row the badge reads "schedule not started", as today.

**`weekly.py` (pure, new)**
```python
RETRY_BUDGET = 3
def week_start(at: datetime) -> datetime
def weekly_skip_reason(ticks: pd.DataFrame, config: MonitoringConfig, at: datetime) -> str | None
```
- `week_start(at)`: `at` must be tz-aware, else `ValueError("at must be timezone-aware")`. It converts to UTC and
  returns that day's Monday at 00:00 UTC (ISO week).
- `weekly_skip_reason`. A row is a **weekly attempt** iff all of these hold:
  - `trigger == "schedule"`;
  - `cadence == "weekly"`, or `cadence` is null/NaN;
  - `model_bundle_id == config.model_bundle_id`;
  - `week_start(at) <= pd.to_datetime(started_at, utc=True) < week_start(at) + 7 days`. The week is bounded on both
    sides, so rows from a later week, such as test rows written at the real date, never count for an earlier `at`.
- What the rule counts and leaves out:
  - **Null-cadence scheduled rows count:** a champion mismatch, an unknown cron, and any refusal after the line
    `record.model_bundle_id = config.model_bundle_id` in `prepare_tick`.
  - **Rows that never count:** a config-parse failure (no `model_bundle_id`), and runs with no row at all.
  - **Historical rows:** scheduled `burn_in` rows have a non-null other cadence, so they never count.
- Return value:
  - `"this week's weekly tick already succeeded (<github_run_url, else monitoring_run_id>)"` if any weekly attempt is
    `succeeded` (the earliest by `started_at`, ties by `monitoring_run_id`);
  - else `f"retry budget spent: {RETRY_BUDGET} weekly attempts this week did not succeed"` if at least `RETRY_BUDGET`
    attempts are `failed` or `refused`;
  - else `None`.
- An empty frame returns `None`. The input is not mutated.
- Which time counts: the week is that of the **clock stage's start** (`at`). A counted row's `started_at` is its own
  stage's start: evaluate-stage rows start a few minutes after their clock. The ISO week runs Monday to Sunday, so the
  two stages fall in the same week unless a slot is delayed into the following Monday (about four days or more for a
  Wednesday slot), which has not been observed (worst delay seen: about 3 hours).

**`tick.py` (Part 2)**
- `ClockResult` gains a last field `skip_reason: str | None = None`.
- `prepare_tick(request, *, client_factory=None, now: Callable[[], datetime] | None = None)`. `now` is used only for the
  skip check and the record's start; when given, `prepare_tick` sets `record.started_at = now()` right after
  `_new_record()`. In **both** cases (given or not) `at = record.started_at`; there is no second call to
  `datetime.now`. The same instant therefore stamps the row and decides its week, which
  makes the budget test deterministic: its rows carry the injected time, whatever the real date.
- **New order inside the `try`.** The skip check comes *before* the champion and cadence checks, so the retry cap also
  covers their refusals:
  1. `client = …`;
  2. `config = read_monitoring_config(...)`;
  3. `record.model_bundle_id = config.model_bundle_id`;
  4. `ticks = store.read_ticks(client, config)`, moved up from its current place and reused below;
  5. if `request.event_name == "schedule"` and `(reason := weekly_skip_reason(ticks, config, at))`: return
     `ClockResult(sim_date=None, cadence=None, head=None, record=None, error=None, recorded=True, skip_reason=reason)`.
     That writes no row and the clock does not move. Eligibility is decided by the event, not by the resolved cadence,
     because the cadence is unknown when `cadence_for` would refuse;
  6. `check_champion_matches(...)`;
  7. `cadence = cadence_for(...)` and `record.cadence = cadence`;
  8. the burn-in + replay refusal (Part 1);
  9. `requested`, then `succeeded = store.succeeded_sim_dates(ticks, config)`, `next_sim_date`, `head`, unchanged.
- Consequence:
  - **What the cap now covers:** a champion mismatch or an unknown cron on schedule is recorded (null cadence, the
    config's bundle) and counts. After 3 such rows the rest of the week skips: at most 3 red runs a week.
  - **One accepted side effect:** a BigQuery read error now surfaces before the champion check. Either way the row is
    `failed`/`refused` at stage `clock`.

**`monitor_tick.py` `_report_clock` (Part 2)**
- If `result.error is None and result.skip_reason`, it is checked before the existing success branch:
  - print `f"skip: {result.skip_reason}"`;
  - append `"skip=true\n"` to `GITHUB_OUTPUT` (no `sim_date`/`cadence` lines; every downstream job is gated on `skip`);
  - append to `GITHUB_STEP_SUMMARY` `"## Monitoring clock (simulated)\n\n" + _table([("event", "schedule"), ("status",
    "skipped"), ("reason", result.skip_reason)])` (the skip result carries `cadence=None`);
  - return 0.
- The existing success branch appends `"skip=false\n"` to its `GITHUB_OUTPUT` text, after `cadence=…`.

**`monitor.yml` (Part 2)**
- After the `workflow_dispatch` block:
  ```yaml
    schedule:
      # Weekly tick with hourly retry slots Monday-Wednesday (notes/eg-new-feature/monitoring-burn-in-timer-and-weekly-retry-2026-10-20.md):
      # GitHub drops most scheduled slots for this repository; the clock skips once this week's tick succeeded or 3 attempts failed.
      - cron: "17 * * * 1-3"
  ```
- Clock job `outputs:` gains `skip: ${{ steps.clock.outputs.skip }}`.
- `score`: `if: needs.clock.outputs.skip != 'true'`. The implicit `success()` remains, so a failed clock still skips
  score as today.
- `evaluate`: `if: always() && needs.clock.result == 'success' && needs.clock.outputs.skip != 'true'`.
- `ledger-render`: `if: always() && needs.clock.outputs.skip != 'true'`. A failed clock leaves the output empty, so the
  ledger still renders, as today.
- `issues`: `if: always() && needs.clock.outputs.skip != 'true'`.
- `ledger-commit` is unchanged; it is skipped because `ledger-render` did not succeed.
- A skip run concludes `success`, because skipped jobs do not fail a run. T4 verifies this live.

**`m3_check.py` (Part 2)**
- In `classify`, before the green/red decision: `conclusion == "success"`, no row for the URL, and the job named
  `score` has `conclusion == "skipped"` give `Verdict("skipped", None, run_info)`.
- `first_weekly_after` already passes over non-green verdicts.
- Burn-in runs never skip: skip eligibility is `request.event_name == "schedule"` (checklist item 4), and burn-in runs are dispatches. A scheduled run whose cron is unknown (null cadence) can skip.

### Part 2 implementation checklist (refreshed 2026-10-21 against `main` at `281dea5`, after Part 1 merged)

This list consolidates every Part 2 change, including the ones stated elsewhere in this note (Part 1 sections, Failure
modes, Verification, Records), with anchors checked against the current code. Where it and an older passage disagree,
this list wins.

**Code**
1. `src/readmission_risk/monitoring/config.py:19`: `WEEKLY_CRON = "17 * * * 1-3"`; its comment becomes "weekly tick with
   hourly retry slots Monday-Wednesday; the clock skips once the week's tick succeeded or 3 attempts failed (weekly.py)".
   `_PERIODS` keeps `{WEEKLY_CRON: timedelta(days=7)}`.
2. `deploy/monitoring.json:9`: `"crons": {"weekly": "17 * * * 1-3"}`.
3. `src/readmission_risk/monitoring/weekly.py` (new): exactly as in "`weekly.py` (pure, new)" above, with the module
   docstring stating the event/trigger invariant (Verification > the design invariant).
4. `src/readmission_risk/monitoring/tick.py`:
   - `ClockResult` (line 68) gains the last field `skip_reason: str | None = None`;
   - `prepare_tick` (line 133) gains `now: Callable[[], datetime] | None = None`; after `record, start =
     _new_record()` (line 134): `if now is not None: record.started_at = now()`;
   - inside the `try`, the current order (lines 140-147: config, `model_bundle_id`, champion, cadence, burn-in check,
     requested, `succeeded = store.succeeded_sim_dates(store.read_ticks(...))`) becomes: config; `model_bundle_id`;
     `ticks = store.read_ticks(client, config)`; the skip check (`request.event_name == "schedule"`, `at =
     record.started_at`), returning the skip `ClockResult`; champion; cadence; `_check_burn_in_request`; requested;
     `succeeded = store.succeeded_sim_dates(ticks, config)`; the rest unchanged.
   - `evaluate_tick` (line 164) is not changed.
5. `scripts/monitor_tick.py`:
   - `_report_clock` (line 93): the skip branch first (as in "`monitor_tick.py` `_report_clock` (Part 2)" above); the
     success branch's `GITHUB_OUTPUT` text at line 96 becomes `f"sim_date={...}\ncadence={result.cadence}\nskip=false\n"`;
   - a module-level `from datetime import UTC, datetime` (the imports at lines 12-19 have none today);
   - a new `_check_catch_up_window()` raising `ValueError("catch-up does not run from Monday 00:00 to Thursday 06:00
     (UTC): the weekly retry slots (and their delayed runs) may be active")` when `now = datetime.now(UTC)` has
     `now.weekday() in {0, 1, 2}` or (`now.weekday() == 3 and now.hour < 6`). The six Thursday hours cover a delayed
     Wednesday 23:17 slot (delays of up to about 3 h have been observed).
   - It is called (a) at the end of `_check_catch_up_args` (line 178), after its existing two checks, and (b) **at the
     top of every iteration** of the `catch_up` loop, before `prepare_tick`, **together with**
     `require_no_running_workflow()`. The existing pre-loop call (line 198) **stays**, so the order of today's
     refusals is unchanged and the call count for `--weeks N` is **N + 1**. In Part 2, `require_no_running_workflow`
     (lines 82-90) also checks **both** workflows of the concurrency group, `monitor.yml` and `score.yml`, and pins the
     repository: for each `w` it runs `["gh", "run", "list", "-R", "fma00/synthea-readmission-risk", "--workflow", w,
     "--json", "status", "--limit", "20"]`, and its error names the workflow (`f"a {w} run is {status}; never catch up
     while an Actions tick may run"`). A refusal there prints `ERROR:
     <message>` and exits 1 (`EXIT_REFUSED`) after the weeks already done. A catch-up started on a Sunday evening
     therefore stops before Monday's first slot, like the timer.
   - The `catch-up` command's docstring (line 192) gains "Refused from Monday 00:00 to Thursday 06:00 UTC (the weekly
     retry slots)."
   - The timer keeps `BLOCKED_WEEKDAYS = {0, 1, 2}` (item 6). Its idle wait sees Actions runs, so a delayed Wednesday
     slot running on Thursday only delays the timer's next dispatch; a conflict with a pending dispatch is handled as
     `dropped`.
6. `scripts/burn_in_timer.py:31`: `BLOCKED_WEEKDAYS: frozenset[int] = frozenset({0, 1, 2})  # the weekly retry slots`.
7. `src/readmission_risk/monitoring/ledger.py`:
   - `HEADER` (line 15): exactly one substring changes, old `"after the burn-in, weekly ticks are scheduled once a
     week."` → new `"weekly ticks are scheduled, with retry slots that normally stop once the week's tick has
     succeeded."`. The rest of the header is unchanged, including "Ticks labelled catch-up (laptop) …" and "This ledger
     shows repeatability and idempotency, not weeks of unattended operation": a few weekly ticks before the `v2.0` tag
     are still not weeks of operation. The Interfaces wording "replaces the whole sentence" means only this burn-in
     sentence's last clause; this item is the exact edit.
   - `last_tick_badge`, line 126: the staleness basis becomes succeeded weekly scheduled rows (as above). **No bundle
     filter (accepted):** a champion promotion must change `deploy/monitoring.json` and re-anchor the clock anyway
     (slice-7 `check_champion_matches`), and an old bundle's last weekly tick only sets a staleness time that the new
     bundle's first weekly tick replaces.
8. `scripts/m3_check.py`:
   - `classify` (line 48): the `skipped` verdict, checked first: `run_info["conclusion"] == "success"`, no row for the
     URL, and the job named `score` with `conclusion == "skipped"`, **with no `attempt` condition** (a re-run of a skip
     run is still a skip, neutral either way);
   - `Verdict.kind`'s comment (line 36) becomes `# green | red | dropped | skipped`;
   - **outcome-neutral by design:** today a skip run would classify as red (success without a row), which
     `first_weekly_after` already passes over; the new verdict changes only the printed evidence (`skipped` instead of
     `red`), not whether M3 is met;
   - **listing bound (critic round P2-1, gap 6):** in `collect`, the scheduled-run listing gains
     `"--created", f">={streak[-1].run['createdAt'][:10]}"` (the streak end's UTC date), so the 1000-run limit counts
     only runs from the streak onwards and the first weekly tick after the streak cannot drop out of the listing for
     about 14 weeks at a full fire rate; the help text of `--limit` notes "scheduled runs are listed from the streak's
     date". Because of this, `collect` lists the scheduled runs only after the streak is known (it already does);
   - the docstring's second paragraph (the `--help` text) is **replaced** by exactly: "Burn-in runs are the
     workflow_dispatch runs titled 'Monitoring tick (burn-in)'; weekly runs are the scheduled runs. A run counts green
     only if its Actions conclusion is success, its attempt is 1, AND serving.monitoring holds a succeeded row for its
     URL with the expected cadence. A cancelled run that never started its clock job is dropped (neither green nor
     red); a scheduled run whose score job was skipped is a skip (neutral). The streak is the first 5 consecutive green
     burn-in runs; M3 is met by the first green weekly run after it, and red, dropped or skipped weekly runs in between
     do not invalidate it. Scheduled runs are listed from the streak's date. Needs `gh` and BigQuery read access
     (laptop: the ledger-reader SA). Exit 0 met, 3 not met, 2 on a gh or BigQuery error." (one line in the file, as
     today);
   - `first_weekly_after`'s docstring (line 81) becomes "The first green weekly verdict created after `after`; red,
     dropped or skipped scheduled runs before it are passed over."
9. `.github/workflows/monitor.yml`: the `schedule:` block after `workflow_dispatch` (as above); the clock job's
   `outputs:` gains `skip: ${{ steps.clock.outputs.skip }}`; the `if:`s of `score`, `evaluate`, `ledger-render` and
   `issues` as above; the header's cron-state sentence becomes "Cron states (exactly one at a time,
   tests/test_infra.py): the weekly retry cron (Part 2 of
   notes/eg-new-feature/monitoring-burn-in-timer-and-weekly-retry-2026-10-20.md); the burn-in was dispatched by a laptop timer (make
   burn-in-timer).".
10. `Makefile`: `burn-in-timer`'s help gains "; refuses Mon-Wed UTC (weekly retry slots)" after "(laptop; needs gh)";
    `monitor-catch-up`'s help (line 119) gains "; refused Mon 00:00-Thu 06:00 UTC (weekly retry slots)".

**Tests (changed and new; each listed so the semantic-change count is visible)**
- `tests/monitoring/test_config.py`: lines 75, 92 and 109 read `mc.WEEKLY_CRON` instead of `"17 6 * * 1"` (literal);
  lines 49 and 51 keep the old literal as an arbitrary value (unchanged).
- `tests/monitoring/test_tick.py`: lines 177 and 189 read `WEEKLY_CRON` (literal); new tests
  `test_weekly_skip_returns_no_row`, `test_weekly_skip_does_not_apply_to_dispatch_or_burn_in`,
  `test_weekly_tick_runs_when_this_week_has_no_attempt`, `test_weekly_skip_precedes_champion_and_cadence_checks`,
  `test_budget_counts_champion_mismatch_refusals` (as specified in Verification).
- `tests/monitoring/test_weekly.py` (new), as in Verification.
- `tests/monitoring/test_ledger.py`:
  - `test_badge_cases` (lines 66-76) changes **semantically**: the default `_tick` is a scheduled `burn_in` row, so with
    the weekly cron it now reads "schedule not started"; a `_tick(1, cadence="weekly")` row gives "stale after
    2026-11-03 13:07 UTC"; the laptop and clock-failure assertions use `WEEKLY_CRON` (literal);
  - `test_cron_state` (lines 79-86) uses `WEEKLY_CRON` (literal);
  - new `test_badge_staleness_uses_succeeded_weekly_only`;
  - the header assertion follows the new `HEADER`.
- `tests/monitoring/test_cli.py`:
  - a new autouse fixture `_thursday` patching `cli.datetime` and `timer._now` to Thursday 2026-10-22 10:00 UTC, so the
    existing catch-up tests (lines 114-170) and timer-CLI tests stay weekday-independent;
  - new `test_catch_up_refuses_mon_wed`, `test_timer_refuses_mon_wed_at_start` (no `gh` call is made);
  - new `test_clock_skip_writes_skip_output` and the resolved-path `skip=false` assertion in
    `test_clock_resolved_writes_outputs`;
  - new `test_m3_skip_runs_are_neutral`: the verdict is `skipped` (also on attempt 2), and a skipped verdict before
    the first green weekly leaves M3 met with the same evidence;
  - `test_m3_collect_lists_events_separately_and_bounds_the_views` additionally asserts the scheduled listing carries
    `--created >=2026-10-20` (the streak end's date in its fixtures);
  - new `test_catch_up_rechecks_the_window_each_week`: `cli.datetime` is patched with a class whose `now(tz)` returns
    `state["now"]` (initially Sunday 2026-10-25 23:50 UTC); the patched `evaluate_tick` sets `state["now"]` to Monday
    2026-10-26 00:05 UTC on its first call (driven by tick progress, not by the number of `now` calls); `--weeks 3`
    gives exactly **1** `prepare_tick` call, then exit 1 with the window message;
  - new `test_catch_up_window_boundaries`: Thursday 05:59 UTC is refused, Thursday 06:00 and Sunday 23:59 are allowed;
  - `test_catch_up_switches_identities_monitor_scorer_monitor` runs with `--weeks 2` and asserts
    `require_no_running_workflow` is called **3** times (start + each week; patched counter);
  - new `test_require_no_running_workflow_checks_both_workflows_with_repo`: the faked `subprocess.run` sees two calls
    carrying `-R fma00/synthea-readmission-risk` and `--workflow monitor.yml` / `--workflow score.yml`, and a busy
    `score.yml` refuses with "a score.yml run is in_progress".
- `tests/test_infra.py`: `test_monitor_workflow_job_graph`'s expected `if:` strings; a check that the clock job's
  outputs include `skip`; `test_monitor_workflow_cron_state_and_literals_match_the_config` is **tightened** to
  `crons == [config["crons"]["weekly"]] == ["17 * * * 1-3"]` and `"schedule:" in text`, so a Part 2 diff without the
  schedule fails CI.

**Records (Part 2)**:
- **README and its pins (the burn-in is done since T2/T3):** the roadmap row status `Built in v2; burn-in pending`
  becomes `Built in v2; burn-in done, scheduled weekly tick pending`; the paragraph heading "3. **Monitoring (built
  in v2, burn-in pending):**" becomes "3. **Monitoring (built in v2, weekly tick pending):**"; `tests/test_docs.py`
  `STATUS_ROWS["**Monitoring**"]` and `ROADMAP_STATUS["Monitoring"]` (`"(built in v2, weekly tick pending"`) change
  with them, and the comment above them says so. They change again at T5/the `v2.0` tag (out of scope here).
- **The scheduled-runs badge disclosure (failure-mode row "README's 'Monitoring tick (scheduled)' badge"):** README's
  limitations bullet "…the 'Monitoring tick (scheduled)' badge shows the latest scheduled run, which can lag, …"
  becomes "… which can lag and, with the weekly retry slots, can be a slot that skipped (green even after a week whose
  3 attempts failed), …"; `docs/setup.md`'s Staleness bullet sentence "…so it can lag; …" gains the same clause.
- **Memory:** `project_slice7_monitoring.md` records the Part 2 merge and T4-T6 status.
- **`docs/setup.md` catch-up refusal:** the commands-block comment on `make monitor-catch-up WEEKS=18` gains "; not Mon
  00:00-Thu 06:00 UTC", and the "Never overlap" bullet gains: "From the weekly retry slots on, the catch-up refuses from
  Monday 00:00 to Thursday 06:00 UTC and re-checks for a running Actions tick before every week; the timer refuses
  Monday to Wednesday."
- PRD FR19 first sub-bullet, FR21 and the 60-day risk row (Records above); `docs/setup.md` (Cron
states → the weekly retry cron is live; a "Weekly retry slots" bullet; the Staleness bullet's Part 2 sentence; the Cost
bullet; the "do not re-run a weekly run" guidance; the burn-in-timer bullet's Mon–Wed refusal); README operations row
(", with hourly retry slots Monday to Wednesday"); CLAUDE.md (the slice-7 sub-bullet: Part 2 merged); this note's
Results (T1-T3 now, T4-T6 later).

**Timing:** merge on a Thursday to Sunday (UTC). The first retry slot is Monday 2026-10-26 00:17 UTC.

## UX flow

**Part 1 (PR 1 merged):**
1. **Merge timing.** Merge PR 1 between 00:00 and 12:00 UTC. A delayed run of the retired burn-in cron (seen up to 3 h
   after the last 18:37 slot) would then be unlikely: it would be refused by the clock (unknown cron), red, recorded as
   `refused`/`clock`, and comment on the failure issue.
2. **Run the timer.** With no other monitor/score run active, run `make burn-in-timer`: 5 ticks, 30 minutes start to
   start, about 2.5 hours. It can run overnight on mains power; `caffeinate -i` prevents idle sleep (closing the lid on
   battery can still sleep).
3. **Check.** `make m3-check` lists the streak and prints "M3 NOT MET", because there is no weekly tick yet. Through
   make, a non-zero script exit shows only as "make: *** [m3-check] Error 3" and make exits 2. The documented script
   exit codes (m3: 0/2/3; timer: 0/1/2/3/130) are visible as that `Error N` or by running
   `python scripts/m3_check.py` / `python scripts/burn_in_timer.py` directly. `docs/setup.md` says so.
4. **Recovery.**
   - A red tick: fix the cause and re-run the timer; M3's streak restarts.
   - A restart after Ctrl+C or an exit-2 stop keeps the streak **only if** the run left in flight ends green. At start
     the timer logs the newest burn-in run's status and conclusion (see the loop's step "Start"), and its idle wait
     covers a run still in flight. A red one is logged as a warning; `make m3-check` is the authority.
   - Every timer start dispatches up to `--ticks` new runs, so each restart moves the clock up to that many more
     weeks.

**Part 2 (PR 2 merged on a Thu–Sun, after the burn-in):**
1. **Slots.** The hourly Mon–Wed slots fire when GitHub lets them.
2. **Weekly tick.** The first fired slot of the week runs a full tick (or retries until 3 failures); later slots skip
   after the clock job: an estimate of a few minutes, because the clock job does the full hash-locked install with no
   pip cache. T4 measures it.
3. **Close-out.** After the first green weekly tick, `make m3-check` prints `M3 MET` (script exit 0), then L16/L17 of the slice-7 runbook
   follow.

**Pipeline per non-skipped tick:** unchanged (clock → score → evaluate → ledger → issues).

## Data handling

- No model, feature, split or label changes, so there is no leakage surface.
- The simulated clock's semantics are unchanged: one grid week per successful non-replay tick. A skip run writes
  nothing.
- **Clock movement.**
  - The burn-in moves the head from 2023-09-25 by 5 or more weeks; each timer restart (after a red tick or an
    interruption) dispatches up to `--ticks` more.
  - The weekly cadence moves it one week per real week, so the clock is exhausted around 2029.
  - A manual dispatch during the week moves the clock an extra week, as today.
- **Determinism.** No randomness is added. The skip decision is a pure function of the BigQuery rows and the recorded
  (or injected) clock-stage start time. *Which* fired slot runs the week's tick depends on GitHub's timing and is not
  deterministic; the tick's outputs for a given simulated date are not affected by it.
- **Cost.**
  - A tick costs what slice 7's does.
  - A skip run costs one `read_ticks` query (10 MB minimum billing) and free runner minutes.
  - At most 72 queries a week, about 0.7 GB billed, which is inside the free tier.

## Failure modes

| Case | Behaviour |
|---|---|
| Timer: gh missing, not authenticated, lacking the workflow scope, or a network error | `GhError` → exit 2; the run in flight continues on GitHub; restart later |
| Timer: run cancelled before its clock started (concurrency replaced a pending run) | `dropped`, re-dispatched; 3 in a row → exit 2 |
| Timer: another monitor/score run active ≥ 150 min | exit 2 |
| Timer: run not identifiable (no URL, 0 or ≥ 2 candidates) | exit 2; check `gh run list`; M3 is unaffected (it reads the history) |
| Timer: laptop sleeps mid-wait | the run finishes on GitHub; polling resumes on wake; if `run_timeout` (from dispatch) passed → exit 2, restart; a green run still counts for M3 |
| Two timers at once | the second waits for idle; runs interleave; the clock stays consistent; documented as "run one timer", not prevented |
| `cadence=burn_in` with `sim_date` | clock refused (`refused`/`clock`, cadence `burn_in`); run red |
| `cadence=burn_in` dispatched by hand | counted by M3 like a timer run; documented: only the timer dispatches burn_in |
| `REQUESTED_CADENCE` set on schedule or laptop | `cadence_for` raises → clock refused |
| Delayed run of the retired burn-in cron after PR 1 | clock refused (unknown cron), red, recorded; mitigated by merge timing (UX flow); harmless to M3 (not weekly) |
| Weekly: a fired slot fails with a row | next fired slot retries; after 3 recorded failed/refused weekly attempts the rest of the week skips; the failure issue collects one comment per red run |
| Weekly: champion mismatch / unknown cron | recorded with null cadence and the config's bundle; it counts, and the skip check runs before these checks, so at most 3 red runs a week |
| Weekly: no attributable row (config-parse failure, BigQuery unreachable before `read_ticks`, evaluate-job install/auth failure, runner loss, insert failure = exit 4) | not counted: a red run per fired slot (≤ 72 a week) until fixed. **Accepted**: config changes reach `main` only through CI-tested PRs, and infrastructure outages are transient; follow-up 4 |
| Weekly: no slot fires Mon–Wed | no tick that week; the next week processes the head; P ≈ 2.5% at a 5% fire rate |
| Weekly: the tick succeeds in BigQuery but the Actions run ends red (ledger-commit push conflict, issues-job gh error) | **Accepted.** The skip rule sees the succeeded row (BigQuery is the truth), so the week is done and later slots skip. M3 counts that run red and waits for the next week's green weekly run. The red job shows in the run, and the issues job comments if it ran |
| Part 2: an owner dispatch of `monitor.yml`/`score.yml` still *pending* when a retry slot fires (Mon–Wed, or a delayed Wednesday slot landing on Thursday) | the newer slot run replaces the pending dispatch in group `score`: the dispatch is cancelled (dropped, not failed). Documented in `docs/setup.md` ("Never overlap"); re-dispatch it. The timer treats such a drop as `dropped` and re-dispatches |
| README's "Monitoring tick (scheduled)" badge (`?event=schedule`) | Part 1: it freezes on the last scheduled run (a 2026-10-20 burn-in, or a delayed retired-cron refusal). Part 2: skip runs conclude `success`, so it turns green again after a week's retry budget is spent. **Disclosed, not changed** (the badge is pinned by `test_docs` and FR20): the README limitations bullet and `docs/setup.md` say that the last-tick badge and the ledger are the truth for the week's tick |
| Part 2: `make monitor-catch-up` from Monday 00:00 to Thursday 06:00 UTC | **Refused** (exit 1) at start and before every week, together with a re-check for a running Actions tick; exact placement, message and tests in "Part 2 implementation checklist" items 5 and Tests (that list wins over earlier wording). The timer and a catch-up cannot see each other: documented, "run one at a time" |
| Part 2: an owner "Re-run failed jobs" on a red weekly run | the re-run's row has `trigger = schedule`, `cadence = weekly`. It counts towards the budget, and if green it ends the week, but M3 scores it red (`attempt > 1`). **Guidance in `docs/setup.md`: never re-run a weekly run (green or red); let the next slot retry, or dispatch.** Accepted |
| Part 2: "Re-run failed jobs" on a red weekly run *after* a later slot succeeded | the re-run's score/evaluate replay the same `sim_date` (the evaluate re-check allows a date at or before the head), so the partition is replaced and a second succeeded weekly row with the same `sim_date` and an identical evaluation is appended. This is slice 7's documented replay idempotency; the clock does not move. Covered by the same "do not re-run a weekly run" guidance; the ledger header does not claim "once per week" |
| Part 2: any re-run ("Re-run all jobs" or "Re-run failed jobs") of the week's **green** weekly run | the clock skips (the week's succeeded row exists), so the run ends `success` at attempt 2. `m3_check.classify` reads the latest attempt: there is a row for the URL (attempt 1), so it is not `skipped`, and attempt 2 is not green, so it is `red`. If that run was M3's evidence, M3 reads NOT MET until the next week's green weekly tick. **Accepted, with the guidance widened: never re-run a weekly run, green or red** (`docs/setup.md`) |
| Timer started or still running on Mon–Wed after Part 2 | refused by the weekday guard (exit 2) before any dispatch |
| Weekly: dispatch tick during Mon–Wed | not the weekly tick (cadence `dispatch`); clock moves an extra week |
| Week boundary | ISO week in UTC from the clock-stage start; slots are Mon–Wed only |
| Clock exhausted | refused weekly rows count → 3 red runs a week until the schedule is removed (slice-7 follow-up) |
| 60-day inactivity auto-disable | applies again once Part 2 adds a schedule (slice-7 risk row and L16 reminder) |

## Verification criteria

**New and changed unit tests** (no network; `gh` and BigQuery are injected or faked):

*`tests/monitoring/test_config.py`*
- Changed:
  - the `crons` cases at lines 49-51 (a `burn_in` key is refused, a missing `weekly` is refused);
  - `test_cadence_for` (lines 76-85);
  - `test_on_grid_and_period_of` (lines 95-104, only the weekly cron).
- Deleted: `test_hourly_burn_in_fallback_is_accepted`.
- New `test_cadence_for_requested_cadence`:
  - dispatch + `burn_in` gives `burn_in`;
  - dispatch + `"dispatch"`, `""` or `None` gives `dispatch`;
  - dispatch + `"weekly"` raises;
  - schedule + requested raises;
  - laptop + requested raises;
  - the retired `"7,37 13-18 * * *"` on schedule raises.

*`tests/monitoring/test_tick.py`*
- Changed: line 176, where the burn-in cron assertion becomes a dispatch + `requested_cadence="burn_in"` assertion.
- New:
  - `test_burn_in_with_replay_date_is_refused_with_cadence`: the row is `refused`/`clock` with cadence `burn_in`, and
    the clock does not move;
  - (P2) `test_weekly_skip_returns_no_row`: with `now=` injected, the fake records no insert and `sim_date` is None;
  - (P2) `test_weekly_skip_does_not_apply_to_dispatch_or_burn_in`;
  - (P2) `test_weekly_tick_runs_when_this_week_has_no_attempt`;
  - (P2) `test_weekly_skip_precedes_champion_and_cadence_checks`: with a champion mismatch and 3 refused scheduled rows
    this week, the result is a skip, and no row is inserted;
  - (P2) `test_budget_counts_champion_mismatch_refusals`: 2 prior refused rows → a 3rd refused row is recorded; then the
    4th call skips. Every call passes the same injected `now` (a fixed Tuesday), so the copied rows fall in that week.
    The test sets `GITHUB_ACTIONS=true`, `GITHUB_EVENT_NAME=schedule` and the `GITHUB_SERVER_URL`/
    `GITHUB_REPOSITORY`/`GITHUB_RUN_ID` env (overriding the autouse `_laptop` fixture with `monkeypatch.setenv`), so the
    recorded `trigger` is `schedule`. It copies each inserted row into the `World`'s tick rows between calls (`World`
    does not do this itself).
- **Design invariant (stated in `weekly.py`'s docstring):** skip eligibility uses `request.event_name`; budget counting
  uses the recorded `trigger`, which comes from `GITHUB_EVENT_NAME` (`warehouse/run.py::_trigger_and_url`). Both come
  from the same Actions environment, so they agree in Actions. On the laptop the event is never `schedule`.

*`tests/monitoring/test_weekly.py` (P2)*
- `week_start`: a Monday 00:00 maps to itself; Sunday 23:59 maps to the previous Monday; a naive datetime raises; a
  +02:00 time converts to UTC first.
- Skip:
  - a succeeded attempt skips;
  - 3 failed/refused attempts skip;
  - 2 failed attempts do not.
- Ignored rows:
  - the previous week's rows;
  - another bundle's rows;
  - `dispatch`/`catch_up`/`burn_in` successes;
  - a null-cadence dispatch refusal.
- A null-cadence scheduled refusal counts; a NaN cadence counts like `None`.
- The upper week bound: rows from the **next** week are ignored.
- Precedence: 3 failed + 1 succeeded attempt gives the "already succeeded" reason.
- The succeeded reason names the earliest succeeded attempt (ties broken by `monitoring_run_id`), using its
  `github_run_url`, else its `monitoring_run_id`; both exact strings are asserted.
- The exact budget string: `"retry budget spent: 3 weekly attempts this week did not succeed"`.
- Rows with `trigger` `laptop` or `None` never count, even with cadence `weekly` or null.
- The input frame is unchanged.

*`tests/monitoring/test_burn_in.py`* (a scripted fake `gh` keyed on args, a fake clock advanced by the fake `sleep`)
- Happy path:
  - five greens give 5 outcomes, and every `workflow run` call is exactly `["workflow", "run", "monitor.yml", "--ref",
    "main", "-f", "cadence=burn_in"]`;
  - the run id comes from a URL on the stderr part of the output.
- Identification fallback:
  - list diff + title, with no time filter;
  - zero candidates by 120 s gives code 2;
  - two candidates give code 2.
- Stops:
  - red gives code 3 with the outcomes so far;
  - dropped is re-dispatched, and 3 drops give code 2 with the exact message.
- Waiting:
  - idle checks both workflows;
  - the idle timeout gives code 2 with the message naming 150;
  - the run timeout counts from `t0`.
- `test_read_only_calls_retry_and_dispatch_does_not` (on `with_retries` directly):
  - a `run list` / `run view` / `auth status` call failing twice then succeeding returns the reply, after 2 sleeps of
    30;
  - one failing 4 times raises after 3 sleeps;
  - a `workflow run` failure raises at once, with no sleep;
  - the read-only detection works with and without the `-R` prefix.
- (in `test_cli.py`) `test_cli_gh_wrapper_contract`:
  - `timer.subprocess.run` is monkeypatched;
  - a `--json` call with a stderr warning returns stdout only;
  - a `workflow run` call returns stdout + `\n` + stderr;
  - a non-zero exit raises `GhError`;
  - a `FileNotFoundError` from `subprocess.run` raises `GhError("gh is not installed or not executable: …")`.
- (in `test_cli.py`) `test_cli_wraps_gh_with_retries`: `timer.run_burn_in` is monkeypatched to capture its `gh`; a
  `run list` through the captured callable that fails twice, then succeeds, returns the reply.
- (in `test_burn_in.py`) `test_retry_sleeps_count_against_the_idle_deadline`: `run_burn_in` receives
  `with_retries(fake_gh, fake_sleep)`, where `fake_sleep` advances the fake clock. Retried idle polls push
  `now() - wait_start` past `idle_timeout`, giving code 2.
- `test_auth_status_has_no_repo_flag`: the start check's call is exactly `["auth", "status", "--hostname",
  "github.com", "--active"]`; every `run`/`workflow` call starts with `["-R", REPO]`.
- Pacing: the sleep equals the interval minus elapsed time, and is 0 when a tick took longer than the interval.
- Start: the previous burn-in run is logged (none, green and red cases), and a red one adds the warning line.
- Completion: the first view is made without a sleep; a completed view after the timeout counts as completed.
- Weekday guard:
  - with `blocked_weekdays={0, 1, 2}` and a Tuesday clock, the timer stops with code 2 before calling `workflow run`;
  - `test_weekday_guard_rechecked_after_idle_wait`: the fake clock is on Sunday 23:50, the idle wait crosses into
    Monday, and the result is code 2 with no `workflow run` call.
- Malformed reply: a list missing `status` gives code 2 with "unexpected output: KeyError".
- Errors: `GhError` gives code 2; malformed JSON gives code 2.
- `TimerSettings` bounds: 1, 12, 10 and 120 minutes are accepted; 0, 13, 9 and 121 minutes are refused.

*`tests/monitoring/test_cli.py`*
- **Test environment:**
  - The autouse `_env` fixture (lines 40-45) also deletes `REQUESTED_CADENCE` (Part 1).
  - **Part 2:** a new autouse fixture `_thursday` monkeypatches the clocks of both CLIs to Thursday 2026-10-22
    10:00 UTC:
    - `cli.datetime` (`scripts/monitor_tick.py`'s module-level `datetime`) with a subclass whose `now(tz)` returns it;
    - `timer._now` (`scripts/burn_in_timer.py`) with a lambda.
  - This keeps every existing catch-up test and every timer-CLI test independent of the real weekday (current line
    numbers: "Part 2 implementation checklist" > Tests).
    Mon–Wed tests override it with a Tuesday.
- Changed:
  - the clock test at lines 61-67 (schedule + burn-in cron), which becomes a dispatch with `REQUESTED_CADENCE=burn_in`;
  - the M3 tests at lines 170-200, ported to `burn_in_streak`/`first_weekly_after`.
- New:
  - `test_m3_counts_only_titled_dispatch_runs`;
  - `test_m3_red_weekly_then_green_weekly_is_met`;
  - `test_m3_weekly_before_the_streak_does_not_count`;
  - `test_m3_later_burn_in_session_does_not_hide_an_earlier_weekly`: 5 burn-in greens, then a green weekly, then 3
    more burn-in greens → MET, and the evidence is the first 5 plus the weekly;
  - `test_m3_scheduled_burn_in_runs_never_count`;
  - `test_m3_lists_dispatch_and_schedule_separately`: the faked `gh` calls carry the two `--event` values and
    `--limit 1000`, and scheduled runs before the streak end are not viewed;
  - (P2) `test_m3_skip_runs_are_neutral`.
- Burn-in timer CLI:
  - it refuses in Actions;
  - exit codes 0, 2, 3 and 130 with the exact stdout/stderr lines;
  - (P2) `_report_clock` writes exactly `skip=true\n` and returns 0, and the resolved path ends `skip=false\n`.

*`tests/monitoring/test_ledger.py`*
- Changed:
  - the badge and `cron_state` tests at lines 58-79, now on the weekly cron only;
  - the header text.
- Changed: `test_ledger.py:41`. Its `_tick` default `trigger="schedule"` means the expected label becomes
  `"burn-in (scheduled, accelerated)"`.
- New: `cadence_label` for `burn_in` × {`schedule`, `workflow_dispatch`, `laptop`} and for the other cadences.
- (P1, done in PR #33) `test_badge_cases`: the burn-in-cron and hourly-fallback assertions were deleted.
- (P2) the changed tests and their current line numbers are listed in "Part 2 implementation checklist" > Tests, which
  replaces the line references previously given here.
- (P2) `test_badge_staleness_uses_succeeded_weekly_only`: a newer scheduled `burn_in` row and a newer failed weekly row
  do not move "stale after".

*`tests/test_infra.py`*
- Changed: `test_monitor_workflow_cron_state_and_literals_match_the_config` (line 682), which accepts `[]` or
  `[config["crons"]["weekly"]]`.
- New `test_monitor_workflow_burn_in_dispatch`:
  - the `cadence` input block is present with `options: [dispatch, burn_in]` and `default: dispatch`;
  - `run-name` equals `f"run-name: ${{{{ github.event_name == 'workflow_dispatch' && inputs.cadence == 'burn_in' && '{BURN_IN_TITLE}' || 'Monitoring tick' }}}}"`,
    built from the imported constant;
  - the clock step env contains
    `REQUESTED_CADENCE: ${{ github.event_name == 'workflow_dispatch' && inputs.cadence || '' }}`;
  - `schedule-probe.yml` does not exist.
- The expression allow-list (`test_monitor_workflow_expressions_only_where_allowed`) gains one **literal** full-line
  match: the exact `run-name` line, built from `BURN_IN_TITLE` like the equality test. It is not a regex, so the
  allow-list cannot widen by accident.
- (P2) the job-graph test's `if:` strings and the clock outputs (`skip`).

*`tests/test_tooling.py`*
- `EXPECTED_TARGETS` gains `burn-in-timer`.
- `MAKEFILE_VARIABLES` gains `BURN_IN_TICKS`, `BURN_IN_INTERVAL` and `CAFFEINATE`. `CAFFEINATE` is set with `:=`, so the
  environment cannot change it, but `make CAFFEINATE=…` on the command line can, so it is scrubbed like the others.
- New: `make -n burn-in-timer BURN_IN_TICKS=6 BURN_IN_INTERVAL=20` *contains* `scripts/burn_in_timer.py --ticks 6
  --interval-minutes 20`. Containment only, because the `caffeinate -i` prefix is present on macOS and absent on CI's
  Linux.

*`tests/test_docs.py`*: the `STATUS_ROWS` pin changes (Records); links and the README table stay green.

**Gates:** `ruff check .`, the full `pytest`, `zizmor --offline .`.

**Live (owner; recorded in Results):**
- **T1:** merge PR 1 (CI green) between 00:00 and 12:00 UTC. Pass criteria:
  - `git show origin/main:.github/workflows/monitor.yml | grep -c 'cron:'` prints `0`;
  - `git ls-tree --name-only origin/main .github/workflows/` does not list `schedule-probe.yml`.
  - Recorded, not a pass criterion: the probe's `state` from `gh workflow list --all --json name,state`. The Actions
    API may keep a workflow with run history.
  - Recorded, not a pass criterion: any scheduled `monitor.yml` run created after the merge (a delayed run of the
    retired cron). Its expected form is red with a `refused`/`clock` row; it is accepted.
- **T2:** first record the clock head, as the operator, with exactly the filters of `read_ticks` and
  `succeeded_sim_dates`: `bq query --use_legacy_sql=false 'SELECT DATE_ADD(MAX(sim_date), INTERVAL 7 DAY) AS head FROM
  `synthea-readmission-risk.serving.monitoring` WHERE started_at >= TIMESTAMP "2026-10-18" AND status = "succeeded"
  AND model_bundle_id = "569fdab6c17d433542b2cd6b531ed8efc5677fb37b00a792d84f79803c6a0f72"'`.
  Then `make burn-in-timer`. Expected:
  - 5 green runs titled "Monitoring tick (burn-in)";
  - `serving.monitoring` has 5 succeeded rows with `cadence = burn_in` and `trigger = workflow_dispatch` on the 5
    consecutive grid dates starting at the recorded head;
  - the ledger shows "burn-in (dispatched, accelerated)".
- **T3:** `.venv/bin/python scripts/m3_check.py --impersonate-service-account
  ledger-reader@synthea-readmission-risk.iam.gserviceaccount.com; echo $?` prints a 5-run burn-in streak, `M3 NOT MET`
  and `3`.
- **T4:** merge PR 2 on a Thu–Sun (CI green). On the following Mon–Wed:
  1. exactly one succeeded weekly row that week (`cadence = weekly`, `trigger = schedule`), preceded by 0-3 failed or
     refused weekly attempts;
  2. every later fired slot of that week concludes `success` with clock green and score/evaluate/ledger-render/
     ledger-commit/issues `skipped` (`gh run view <id> --json conclusion,jobs`), and no row;
  3. the count of fired versus due slots is recorded;
  3b. the measured duration of each skip run (`gh run view --json createdAt,updatedAt`). If the median exceeds 5
      minutes, follow-up 3 (a credential-free first job) is promoted from "if noisy" to "next";
  4. **pass criterion: at least one skip run observed live** (item 2). If no slot fires after the week's tick, T4.2 and
     T4.4 carry over to the following weeks, up to 3. If still none, that is recorded, and the skip path's live
     semantics stay under "checked only by unit tests". M3 (T5) does not wait for it.
  5. **failed-clock path, live:** `gh workflow run monitor.yml --ref main -f sim_date=20230418` (off-grid). Expected:
     the run is red; `clock` failed (refused, row `refused`/`clock`, `trigger = workflow_dispatch`, so it never counts
     towards the weekly budget); `score` and `evaluate` skipped; `ledger-render`, `ledger-commit` and `issues` ran (the
     empty `skip` output satisfies `!= 'true'`); one failure issue created or commented, closed by the owner after the
     next green tick. Run it on a Thursday to Sunday, so it does not interfere with the weekly slots;
- **T5:** the same command prints `M3 MET` and `0`.
- **T6:** the slice-7 runbook's L16 and L17, then the Dependabot PR #24 reminder (memory).

**Checked only by unit tests, not live (stated, accepted):**
- the retry cap after 3 failures;
- (moved to T4.5, now checked live) a failed clock in Part 2;
- `m3_check`'s neutral handling of skip runs, if none precede the first green weekly.

## Records (all decisions and design changes reported)

- **PRD** (`notes/prds/v2-mlops-keyless-scheduled-scale-up-2026-10-09.md`): a dated "Amendment 2026-10-20 (owner
  decision, made after observing the scheduled burn-in)" bullet under the D1 block. It covers E1-E4 and points here. The
  inline amendments quote the old wording and give the new:
  1. FR19 (the burn-in bullet), including its ledger-label wording "burn-in (accelerated cadence)", which becomes
     "burn-in (scheduled, accelerated)" / "burn-in (dispatched, accelerated)";
  2. the M3 row in Success metrics;
  3. the "Out" line "a daily cron (an accelerated cron is allowed only for the one-afternoon burn-in, FR19)";
  4. OQ1;
  5. the 60-day auto-disable risk row (it applies again from Part 2).

  The bullet states plainly that M3 was changed after its scheduled form was observed to fail, and that the bar
  is **lowered in one respect**: the burn-in no longer evidences unattended operation (the scheduled weekly tick still
  does).
- **Slice-7 note:**
  - an "Amendment 2026-10-20" bullet under its Owner decisions, pointing here;
  - runbook rows L13-L15 marked "replaced by T1-T5 of `monitoring-burn-in-timer-and-weekly-retry-2026-10-20.md`";
  - Results:
    - the scheduled burn-in attempt: 3 green ticks, clock 2023-09-04 to 2023-09-18, the fire-rate table;
    - the probe: added in PR #32, removed in PR 1, final run count.
- **This note:** a Design-check log and Results for T1-T6.
- **`docs/setup.md` > Monitoring tick:**
  - the commands block gains `make burn-in-timer`;
  - "Cron states" is rewritten (none → weekly retry; the burn-in by timer);
  - a "Burn-in timer" bullet: prerequisites (`gh auth status`; mains power; one timer; not Mon–Wed after Part 2), exit
    codes and recovery;
  - after Part 2, a "Weekly retry slots" bullet: skip semantics and the 3-attempt cap;
  - the Staleness bullet: in Part 1 the badge reads "dispatch only"; in Part 2, "stale after" is the newest succeeded
    weekly tick + 14 days;
  - "Never overlap" covers the timer too.
- **`docs/setup.md`, further stale text (Part 1):**
  - the paragraph above the commands block, "… and the two cron strings", becomes "… and the weekly cron string";
  - the Staleness bullet's sentence "In the burn-in cron state the badge reads stale about an hour after the
    afternoon's last slot, until the weekly-cron PR is merged." is deleted;
  - the "Burn-in timer" bullet states the token scope and, from Part 2, the exit-2 refusal on Mon–Wed UTC.
- **`docs/setup.md` Cost bullet (Part 2):** append "From Part 2, up to 72 retry-slot runs a week, of which all but the
  week's tick skip: one `read_ticks` query each (10 MB minimum billing, inside the free tier) and a few runner minutes
  (free on a public repository)."
- **`docs/setup.md` (Part 1):** a sentence on make's `Error N` versus the scripts' exit codes, in the "Burn-in timer"
  bullet.
- **PRD FR19, first sub-bullet** ("runs on an off-the-hour cron (e.g. `17 6 * * 1`) plus `workflow_dispatch`") gains
  an amendment for Part 2: "amended 2026-10-20: hourly retry slots `17 * * * 1-3`; the clock skips once the week's tick
  succeeded or 3 attempts failed".
- **PRD FR21** (the staleness line): an amendment for Part 2: "stale after" is computed from the newest *succeeded
  weekly* tick (scheduled burn-in rows and failed attempts no longer refresh it).
- **PRD risk row** "Burn-in read as months of operation": its mitigation's label text "burn-in (accelerated cadence)"
  becomes "burn-in (scheduled, accelerated)" / "burn-in (dispatched, accelerated)", with a pointer to the 2026-10-20
  amendment.
- **Makefile help (Part 2):** `burn-in-timer`'s help text gains "; refuses Mon-Wed UTC (weekly retry slots)".
- **CLAUDE.md:**
  - the v2 "Operations" bullet's phrase "preceded by a one-afternoon accelerated burn-in that is labelled as such"
    becomes "preceded by an accelerated burn-in (dispatched by a laptop timer since 2026-10-20, labelled as such)";
  - a sub-bullet under the slice-7 bullet: "Burn-in and weekly schedule amended 2026-10-20
    (`notes/eg-new-feature/monitoring-burn-in-timer-and-weekly-retry-2026-10-20.md`): GitHub's scheduler dropped most
    slots, so the burn-in is dispatched by a laptop timer (`make burn-in-timer`), M3 amended to count those runs
    (post-outcome owner decision), weekly cron `17 * * * 1-3` with a clock skip once the week's tick succeeded or 3
    attempts failed (Part 2)";
  - `burn-in-timer` added to the Make targets list.
- **README.md** (Part 1):
  - the roadmap table row "**Monitoring**" status `Built in v2; scheduled burn-in pending` becomes
    `Built in v2; burn-in pending`;
  - the limitations bullet "…; the burn-in runs every 30 minutes for one afternoon — [design](…)" becomes "…; the
    burn-in is dispatched every 30 minutes by a timer on the owner's laptop, because GitHub's scheduler dropped most
    accelerated slots; the 'Monitoring tick (scheduled)' badge shows the latest scheduled run, which can be a retry slot
    that skipped, so the last-tick badge and the ledger are the record of each week's tick — [design](…)"; the
    existing slice-7 design link stays, and a second link `[amendment](notes/eg-new-feature/monitoring-burn-in-timer-and-weekly-retry-2026-10-20.md)`
    is added after it;
  - README line 5 ("a scheduled monitoring tick on a simulated clock scores each week") is reviewed and **kept**: it
    is true of the weekly tick;
  - the "3. **Monitoring (built in v2, burn-in pending):**" paragraph: "a one-afternoon accelerated burn-in, then a
    weekly cadence" becomes "an accelerated burn-in dispatched by a laptop timer, then a scheduled weekly cadence";
  - the operations table row "Run the monitoring tick": "(scheduled during the burn-in and weekly)" becomes "(dispatched
    by a laptop timer during the burn-in; scheduled weekly)". Part 2 appends ", with hourly retry slots Monday to
    Wednesday".
  - the Release line "v2.0 is tagged once the monitoring tick has run on its schedule (metric M3)" becomes "v2.0 is
    tagged once metric M3 (amended 2026-10-20: a dispatched burn-in, then a scheduled weekly tick) is met";
  - the Roadmap intro "(v2.0 is tagged once the scheduled monitoring has run, metric M3)" becomes "(v2.0 is tagged
    once metric M3 is met: a dispatched burn-in, then a scheduled weekly tick)";
  - the same paragraph's opening "a scheduled tick on a simulated clock" becomes "a tick on a simulated clock (scheduled
    weekly; the burn-in dispatched by a laptop timer)".
- **`tests/test_docs.py`** (Part 1): `STATUS_ROWS["**Monitoring**"]` changes from `"Built in v2; scheduled burn-in
  pending"` to `"Built in v2; burn-in pending"`. `ROADMAP_STATUS["Monitoring"]` (`"(built in v2, burn-in pending"`)
  stays, because it is still true. The comment above them (`tests/test_docs.py:452-454`, "… with its scheduled burn-in
  pending …") becomes "… with its burn-in pending (dispatched by a laptop timer since 2026-10-20) …".
- **Memory:** `project_slice7_monitoring.md` updated.

## Out-of-scope follow-ups

1. A GitHub support ticket or community post with the probe data (owner declined for now).
2. External scheduling (Cloud Scheduler → `workflow_dispatch`, which needs a GitHub token as a documented keyless
   exception) if the weekly retry still misses weeks.
3. A cheaper skip path (a credential-free first job deciding from the GitHub API) if skip runs become noisy at a
   recovered fire rate.
4. Bounding the retries of failures that leave no attributable row (evaluate-job install/auth failure, runner loss,
   insert failure, config-parse failure): for example, count red scheduled runs from the GitHub API.
5. Disabling the schedule when the clock is exhausted (inherited from slice 7).

## Design-check log

- **Round 1 (2026-10-20):**
  - **Comprehension:** passed.
  - **Critic:** 13 gaps + 2 smaller points. **Readiness:** 10 open questions.
  - **Revision 1 addressed all of them:**
    - the budget rule restated: null-cadence scheduled rows with the config's bundle count, and the failure-mode table
      and follow-up 4 now agree;
    - PRD amendments added for "Out: daily cron", OQ1 and the 60-day risk row, with a post-outcome disclosure;
    - `m3_check` uses two event lists with `--limit 1000`, views scheduled runs only after the streak, and states the
      weekly rule explicitly (earlier red weekly runs pass over), with a test;
    - injectable `now` for the skip check, and which timestamp defines the week;
    - a failure-mode row for weekly failures without a row (accepted, follow-up 4);
    - timer timeouts 150/150 minutes from the dispatch, with messages built from the settings;
    - `gh` output parsed from stdout+stderr for `workflow run`;
    - `BURN_IN_TICKS`/`BURN_IN_INTERVAL` and the existing tests that change, listed;
    - the `run-name` test built from `BURN_IN_TITLE`;
    - a fact-neutral ledger header;
    - concrete T1 checks, T4 job-skip semantics, and a stated list of items checked only by unit tests;
    - the README referenced by row, not line;
    - the timer's "green" defined against M3's;
    - the skip output and summary specified, with exact log and stop formats;
    - `CADENCE_LABELS["burn_in"]` removed;
    - the header's last sentence kept;
    - inclusive bounds.

- **Round 2:**
  - **Critic:** 12 gaps. **Readiness:** 6 open questions.
  - **Revision 2 addressed all of them:**
    - the skip check moved before the champion and cadence checks, keyed on `event_name == "schedule"`, with tests
      for the order;
    - an honest disclosure that the timer burn-in no longer shows unattended operation;
    - README lines 27, 146 and 160 and the `test_docs` status pin added to Records;
    - an accepted failure row for a "succeeded tick, red run";
    - `run-name` and `REQUESTED_CADENCE` guarded by `event_name`, with a T4 check of `cadence = weekly`;
    - the ledger header says "succeed at most once per week";
    - the badge's staleness basis = succeeded weekly ticks (Part 2), with a test;
    - T1 split into pass criteria and recorded observations;
    - T2 checked against the recorded head;
    - a weekday guard in the timer (Part 2: Mon–Wed refused);
    - the fallback identification no longer compares clocks;
    - `CAFFEINATE` scrubbed in `test_tooling`, and the dry-run test uses containment;
    - m3 evidence format and the no-streak line specified;
    - the exit-0 count defined;
    - the malformed-reply exceptions listed;
    - the completion-poll order fixed;
    - the `tick.py` line reference replaced by the statement.

- **Round 3:**
  - **Critic:** 11 gaps. **Readiness:** 3 open questions.
  - **Revision 3 addressed all of them:**
    - the PRD wording made consistent with the lowered bar;
    - the scheduled-runs badge behaviour disclosed (failure row, README, setup), not changed;
    - the M3 streak is the first 5 greens, not extended, with a test;
    - the budget test's environment and the event/trigger invariant stated;
    - the Part 2 changes to existing tests listed;
    - `gh` returns raw text, which the loop parses;
    - T4.1 reshaped, and "≥ 1 live skip run" made a pass criterion with a carry-over;
    - a failure row for pending dispatches replaced by a retry slot;
    - restart semantics corrected, the timer logs the previous burn-in run, and clock movement per restart stated;
    - the FR19 label wording added to the PRD amendment;
    - the label made neutral, "burn-in (dispatched, accelerated)";
    - the fallback test without a time filter;
    - the full m3 `--help` paragraph;
    - README's Release line added.

- **Round 4 (3-revision cap reached):**
  - **Critic:** 5 gaps. **Readiness:** 3 open questions.
  - **Owner decision at the cap:** "fix gaps and run another round".
  - **Revision 4:**
    - the streak rule made unambiguous (first 5, `[]` when unmet), with the changed assertions named;
    - the weekday guard moved immediately before every dispatch, with a midnight-crossing test;
    - the skip week bounded on both sides, an injected `now` that also stamps `record.started_at`, and a tie-break;
    - the stale `docs/setup.md`, PRD risk-row and README lines (156, 160) added to Records;
    - the Mon–Wed refusal named in the Make help and the docs;
    - the `gh` scope stated, with a `gh auth status` check at start and `OSError` mapped to exit 2;
    - one merge-window wording.

- **Round 5:**
  - **Readiness:** `implementation ready` (no open questions). **Critic:** 12 gaps.
  - **Revision 5 addressed all of them:**
    - make's `Error N` versus script exit codes, with T3/T5 run directly;
    - catch-up refused Mon–Wed in Part 2;
    - bounded retries for read-only gh calls, none for `workflow run`;
    - the repository pinned with `-R` on every gh call (timer and m3);
    - `gh auth status --hostname github.com`, with the start-check order;
    - `test_ledger.py:41` listed;
    - the Start step sorts by `createdAt`;
    - the `run-name` allow-list entry is a literal;
    - exact T2 SQL;
    - the README design link, line 5 kept, and the setup cost line;
    - a re-run failure row with guidance;
    - the skip-run duration measured in T4, replacing the estimate.

- **Round 6 (owner asked for this extra round):**
  - **Critic:** 7 gaps. **Readiness:** 1 blocking question.
  - **Both caught a real defect introduced in revision 5:** `gh auth status` rejects `-R`.
  - **Revision 6:**
    - `-R` only on `run`/`workflow` calls, and `auth status` is exact;
    - `with_retries` fully specified: the CLI wraps, read-only detection after the prefix, retries count against the
      deadlines, with tests;
    - the `test_ledger` badge changes split into P1 deletions and a P2 semantic rewrite;
    - how m3's lazy view loop composes with the pure functions, with a stop test;
    - PRD FR19 cron sub-bullet, FR21 staleness and the CLAUDE.md Operations bullet added to Records;
    - the ledger header no longer claims "at most once per week", with a failure row for a re-run after a success;
    - the `datetime` import and the patch target named;
    - the whole `schedule:` block deleted, and the idle wait uses item access.

- **Round 7 (owner asked for this extra round):**
  - **Readiness:** `implementation ready`. **Critic:** 10 gaps.
  - **Real defects found:** weekday-dependent tests in Part 2, an unscrubbed `REQUESTED_CADENCE`, and a missing
    `--active` on `gh auth status`.
  - **Revision 7:**
    - a `_thursday` autouse fixture and named clock patch points (`cli.datetime`, `timer._now`);
    - the catch-up guard placed after the existing checks;
    - `REQUESTED_CADENCE` scrubbed;
    - CLI `gh`-wrapper and retry-composition tests, plus a retry-vs-deadline test;
    - `--active`;
    - T1 uses `git ls-tree`;
    - the Start step's owner, error path, `--limit 100` and honest warning wording;
    - `at = record.started_at` always;
    - the `test_docs` comment added to Records;
    - the methodology sentence narrowed to "at most one scheduled weekly tick succeeds per week, barring owner
      dispatches or re-runs".

- **Closure (owner decision, 2026-10-21):** the design was accepted by owner override after round 7. Readiness closed
  in rounds 5 and 7; the critic never closed (each round found new, progressively narrower items, all addressed in the
  following revision). The rounds past the 3-revision cap (5-7) were run at the owner's request. The next independent
  checks are the tests and `/eg-precommit-review` on the diff.

- **Part 2 refresh round (2026-10-21; owner chose "refresh + one check round"):**
  - **Readiness:** `implementation ready`, no open questions. **Critic:** 10 gaps.
  - **Revision P2-1 addressed all of them:**
    - README status rows and `test_docs` pins updated for "burn-in done";
    - the scheduled-runs badge disclosure added to README and setup;
    - the catch-up weekday guard rechecked every iteration, with a test;
    - the exact `HEADER` substring edit;
    - six more `test_weekly` cases;
    - `--created` bounds the M3 scheduled listing;
    - the week-boundary argument restated;
    - the stale Verification line references removed;
    - the `m3_check` skip details made explicit (no attempt condition, the kind comment, outcome-neutral);
    - memory added to the Part 2 Records.

- **Part 2 round 2 (owner asked for one more round):**
  - **Readiness:** `implementation ready` again. **Critic:** 7 gaps.
  - **Revision P2-2 addressed all of them:**
    - the catch-up window becomes Mon 00:00–Thu 06:00 UTC, with a per-iteration running-tick re-check;
    - a live failed-clock check (T4.5);
    - the catch-up refusal recorded in the Makefile, setup and the docstring;
    - the exact m3 `--help` paragraph and the `first_weekly_after` docstring;
    - a deterministic test mechanism for the per-iteration re-check;
    - the Determinism bullet corrected;
    - `tick.py:164`, the stale test_cli reference and the failure row aligned.

- **Part 2 round 3 (owner asked for one more round):**
  - **Readiness:** `implementation ready` (third time). **Critic:** 7 gaps.
  - **Revision P2-3:**
    - the pre-loop running check kept (N + 1 calls);
    - the running check covers `monitor.yml` and `score.yml` with `-R`, with a test;
    - the cron-state test tightened to require the schedule;
    - a failure row for re-running a green weekly run, and the guidance widened;
    - the skip-eligibility wording fixed;
    - the full path in the workflow header;
    - the badge's missing bundle filter accepted, with the reason.
- **Part 2 closure (owner decision, 2026-10-21):** Part 2 accepted by owner override after its round 3. Readiness closed
  in all three Part 2 rounds; the critic found 10, 7 and 7 new gaps, each addressed in the following revision
  (P2-1..P2-3). The next independent check is `/eg-precommit-review` on the diff.

## Results

- **T1 (2026-10-21), pass.** PR #33 merged at 08:38 UTC (merge commit `281dea5`), before the 12:00 UTC cut-off.
  `git show origin/main:.github/workflows/monitor.yml | grep -c 'cron:'` printed `0`; `git ls-tree` of
  `.github/workflows/` on `main` lists `ci.yml`, `monitor.yml`, `score.yml`, `smoke.yml` (no `schedule-probe.yml`).
  Recorded: `gh workflow list --all` no longer lists "Schedule probe"; no scheduled `monitor.yml` run has been created
  since the merge (newest scheduled run: 2026-10-20 21:31 UTC).
- **T2 (2026-10-21), pass.** Recorded head before the timer: **2023-09-25**. `make burn-in-timer` (defaults: 5 ticks,
  30 min) printed "previous burn-in run: none among the last 100 dispatch runs", then 5 green ticks, 0 dropped:
  [39571676250](https://github.com/fma00/synthea-readmission-risk/actions/runs/39571676250) (08:53:45 UTC),
  [39575534341](https://github.com/fma00/synthea-readmission-risk/actions/runs/39575534341) (09:23:46),
  [39579421403](https://github.com/fma00/synthea-readmission-risk/actions/runs/39579421403) (09:53:17),
  [39583252242](https://github.com/fma00/synthea-readmission-risk/actions/runs/39583252242) (10:23:21),
  [39586927257](https://github.com/fma00/synthea-readmission-risk/actions/runs/39586927257) (10:54:25). `serving.monitoring`:
  5 succeeded rows, `cadence = burn_in`, `trigger = workflow_dispatch`, sim dates 2023-09-25, 10-02, 10-09, 10-16,
  10-23 (consecutive from the recorded head), all verdict `ok`, O/E 1.15-1.20. The ledger labels them "burn-in
  (dispatched, accelerated)" (the 3 earlier ones "burn-in (scheduled, accelerated)"); the badge read "2026-10-21 10:51
  UTC · sim 2023-10-23 · dispatch only", green.
- **T3 (2026-10-21), pass.** `python scripts/m3_check.py --impersonate-service-account ledger-reader@…` listed the five
  runs above as `burn-in … green burn_in`, no scheduled run after the streak, `M3 NOT MET` with those five as evidence,
  exit `3`. The burn-in half of M3 is met; the weekly half waits for Part 2.
- **Part 2 merge (2026-10-22).** PR #34 merged at 08:57 UTC (Thursday), as required.
- **T4 (week of 2026-10-26), pass; counts as of 2026-10-27 08:16 UTC.**
  - **The week's tick.** [40270628943](https://github.com/fma00/synthea-readmission-risk/actions/runs/40270628943), created Mon 04:44 UTC from the 04:17 slot, 27 minutes late,
    attempt 1, all six jobs green. Its row: `trigger = schedule`, `cadence = weekly`, sim date **2023-10-30** (the week
    after the burn-in's 2023-10-23), `succeeded`, verdict `ok`, O/E 1.155. It was the first weekly attempt of the week,
    so no failed or refused attempts preceded it (T4.1).
  - **Skip runs (T4.2 and T4.4 pass).** Five, each green at attempt 1 with `score`, `evaluate`, `ledger-render`,
    `ledger-commit` and `issues` skipped and no row written:
    [40312503343](https://github.com/fma00/synthea-readmission-risk/actions/runs/40312503343) (Mon 11:06), [40368002602](https://github.com/fma00/synthea-readmission-risk/actions/runs/40368002602) (Mon 17:22),
    [40414100609](https://github.com/fma00/synthea-readmission-risk/actions/runs/40414100609) (Mon 22:39), [40436294203](https://github.com/fma00/synthea-readmission-risk/actions/runs/40436294203) (Tue 02:04),
    [40469574314](https://github.com/fma00/synthea-readmission-risk/actions/runs/40469574314) (Tue 07:50). The first logged `skip: this week's weekly tick already succeeded
    (https://github.com/fma00/synthea-readmission-risk/actions/runs/40270628943)`.
  - **Fired vs due (T4.3).** 6 runs out of 32 due slots (about 19%) by Tue 08:16 UTC; the final count for Mon-Wed is
    appended after 2026-10-28 23:59 UTC.
  - **Skip duration (T4 3b).** 79-112 s, median 101 s, under the 5-minute threshold, so follow-up 3 (a credential-free
    first job) stays "if noisy".
  - T4.5 (the failed-clock check) is still open; it runs on a Thursday to Sunday.
- **T5 (2026-10-26), pass: M3 met.** `python scripts/m3_check.py --impersonate-service-account ledger-reader@…` listed
  the five burn-in runs of 2026-10-21 as `burn-in … green burn_in`, then `scheduled 2026-10-26T04:44:43Z green weekly
  …/40270628943`, and printed **`M3 MET`** with those six runs as evidence, exit `0`. The PRD's v2.0 condition (FR24:
  tag after slice 7's M3) holds.
- **T6.** The `v2.0` tag is set on the merge commit of the release-prep PR (owner). The 60-day calendar reminder,
  the billing check and Dependabot PR #24 follow.
- **T4 final count (week of 2026-10-26, complete).** **14 scheduled runs fired out of 72 due slots (about 19%)**, all
  green at attempt 1: the week's tick ([40270628943](https://github.com/fma00/synthea-readmission-risk/actions/runs/40270628943), 463 s) and 13 skips (79-116 s, median 101 s).
  - One skip was a **delayed Wednesday slot that ran on Thursday 2026-10-29 02:27 UTC**
    ([40806468680](https://github.com/fma00/synthea-readmission-risk/actions/runs/40806468680), `SCHEDULE: 17 * * * 1-3`). It skipped correctly ("this week's weekly tick already
    succeeded"), because the ISO week runs to Sunday.
  - This is the first observed case behind the catch-up's Thursday-06:00 window and the restated week-boundary argument.
- **T4.5 (2026-10-31, Saturday), pass.** `gh workflow run monitor.yml --ref main -f sim_date=20230418`, run
  [41185368282](https://github.com/fma00/synthea-readmission-risk/actions/runs/41185368282), 08:37-08:41 UTC:
  - **The run** concluded `failure`. `clock` failed with `ERROR: sim date 2023-04-18 is not on the simulated grid
    (2023-04-17 + multiples of 7 days, up to 2026-09-07)`.
  - **Gating:** `score` and `evaluate` were skipped. `ledger-render`, `ledger-commit` ("Ledger: 2 of 2 file(s)
    committed") and `issues` ran, which confirms live that an empty `skip` output satisfies `!= 'true'`.
  - **The row:** `trigger = workflow_dispatch`, `cadence = dispatch`, `status = refused`, `failed_stage = clock`,
    `sim_date` NULL. A dispatch row never counts towards the weekly budget, and the clock did not move.
  - **The issue:** [#38](https://github.com/fma00/synthea-readmission-risk/issues/38) "Monitoring tick failing (simulated
    clock)" (`monitoring-failure`) was created. The owner closes it after the next green tick (the week of 2026-11-02).
- **T6 status (2026-10-31).**
  - **Done:** `v2.0` released 2026-10-27 08:34 UTC as an annotated tag on `fe44a72` (the PR #37 merge commit).
    Dependabot #24 was superseded by #35 (provider 8.7.0; a read-only `terraform plan` on its branch gave "No changes")
    and #36 (`actions/setup-java` 6.1.0); both were merged, and the local `terraform init` is done.
  - **Open (L16):** a calendar reminder for 2026-12-16 (the last `main` commit, 2026-10-27, plus 50 days; GitHub
    disables scheduled workflows after 60 days without repository activity), and the billing check for the slice's days.

