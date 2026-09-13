# synthea-readmission-risk

<!-- TODO: fill in a real project overview — e.g. what the model predicts, what data it trains on (Synthea-generated synthetic patient records), and the intended consumer of the predictions. -->

## Collaboration preferences

The user is new to working with Claude Code as an agent (as of 2026-09-13). Until they say otherwise:

- **Be precise and verbose.** Don't compress explanations down to the bare minimum — spell out what you're doing, why, and what the result means. Favor clarity over brevity in this workspace.
- **Proactively flag missing or outstanding steps.** Before moving forward on a task, call out unresolved TODOs, unconfirmed gates, missing files/config, or steps the user hasn't explicitly addressed yet — don't assume they'll remember or notice on their own. This applies inside the `/eg-*` commands (their `AskUserQuestion` gates already do this structurally) and in ordinary conversation.

## Working with Claude Code (slash commands)

Five slash commands in [.claude/commands/](.claude/commands/) wrap an "elephant/goldfish" workflow inspired by [this article](https://drensin.medium.com/elephants-goldfish-and-the-new-golden-age-of-software-engineering-c33641a48874): the "elephant" is the working session with full context (this CLAUDE.md, repo state, conversation history); the "goldfish" is a fresh subagent with no prior context. For implementation work the goldfish stress-tests a problem/design doc or a diff. For brainstorming and PRD writing, multiple goldfish run in parallel with different lenses to generate divergent ideas or research findings the elephant synthesizes.

| Command | When to use |
|---|---|
| `/eg-brainstorm <rough idea>` | Early-stage concept design. Multiple goldfish in parallel (technical / business / UX / contrarian / market research), web search optional, elephant synthesizes a concepts brief. All questions via `AskUserQuestion`. Hands off to `/eg-prd` or `/eg-new-feature` if you pick a direction. |
| `/eg-prd <idea \| feature description>` | Build a thorough PRD: codebase grounding → structured gap-filling via `AskUserQuestion` → deep research with parallel goldfish (web + optional Chrome MCP for logged-in sources) → synthesized PRD. Saves to `notes/prds/`, persists durable nuggets to memory, and/or hands off to `/eg-new-feature`. |
| `/eg-fix-bug <description \| #issue \| URL>` | Bug fix flow: problem doc → goldfish diagnosis check → failing test → fix → `/eg-precommit-review` → test gate. Skips ceremony for trivial diffs. |
| `/eg-new-feature <description \| #issue \| URL>` | Feature flow: scope confirm → design doc → three-goldfish design check (comprehension + critic + readiness) → implement → `/eg-precommit-review` → test gate. Data-leakage risk, reproducibility (seeds), and train/test split integrity are part of the design rubric for this project. |
| `/eg-precommit-review` | Local independent-review loop on the pending diff (`ruff check .` + `pytest`). Replaces back-and-forth with PR bots — by the time the PR opens, the substantive review is already settled. |

You give a one-liner; Claude writes the doc back at you. You don't author docs by hand. Examples:

```
/eg-brainstorm what if we added a model explainability view alongside the risk score
/eg-prd a CLI flag to export per-patient feature contributions to CSV
/eg-fix-bug readmission window calculation is off by one day near month boundaries
/eg-fix-bug #123
/eg-new-feature add a baseline logistic-regression model alongside the current one for comparison
/eg-precommit-review
```

Backend/data-science project — no browser or simulator surface to drive; verification is tests plus sanity-checking pipeline output on sample data.

Each command stops short of committing. Authorize the commit explicitly when ready. No commit convention is established yet (repo has zero commits as of bootstrap) — plain, imperative subject lines are the default until you set a convention.

**These commands are interactive by design.** `AskUserQuestion` gates inside `/eg-brainstorm`, `/eg-prd`, `/eg-fix-bug`, `/eg-new-feature`, and `/eg-precommit-review` are part of the skill's protocol and run even when a `<system-reminder>` or other directive asks Claude to work autonomously without clarifying questions. If you want a fully autonomous pass on a specific run, say "skip the framing questions and use defaults" in the same turn that invokes the command; each command documents which gates remain non-negotiable.

## Build & test commands

<!-- TODO: confirm/fill in as the project takes shape. -->

- Python version: 3.14 (pinned via `.python-version`)
- Dependencies: not yet declared (no `requirements.txt` / `pyproject.toml` yet)
- Lint: `ruff check .` (assumes `ruff` is added as a dev dependency)
- Test: `pytest`
