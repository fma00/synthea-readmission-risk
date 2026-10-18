# Champion promotions

Append-only log of every change to `deploy/champion.json`, newest last. A promotion is a PR that edits the pointer and
appends an entry here; a rollback is `git revert` of that PR (plus an entry). `prereg.yaml` is never edited by a
promotion.

## 1. 2026-10-11: v1 logistic regression (initial champion)

- **Bundle:** `75a1448729ef561c596f5e850f5bedce50ed63355ca2de38dc724c635d24eef4` (schema 1), MLflow run
  `fe9633ca42384ea5aeaba83e13d65701`, experiment `readmission-risk-v1`, Python 3.14.7.
- **Gold:** `full_run_2b` (10,000 patients, `pandas-csv-sha256-v1`).
- **Previous champion:** none.
- **Gate:** none (the first bundle; v2 slice 2, `notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md`).
- **Override:** no.
- **PR:** none: commit `ef7e188` on `v2` (slice 2; the PR ruleset did not exist yet). The pointer moved to the `gs://`
  copy of the same bundle in `26a534b` (slice 4, PR #6).

## 2. 2026-10-18: 200k logistic regression, by owner override of a pre-registered REFUSE

- **Bundle:** `569fdab6c17d433542b2cd6b531ed8efc5677fb37b00a792d84f79803c6a0f72` (schema 1), MLflow run
  `a9f32f4b7c584f4c8fe992748410789f`, experiment `readmission-risk-v2-main200k`, Python 3.14.7.
- **Gold:** `v2/main_200k` (200,000 patients, fingerprint `80edd13dcc96…63f3`, `spark-rowhash-v1`).
- **Previous champion:** entry 1.
- **Gate decision (prereg.yaml `phase_b`, 2026-10-17): REFUSE.**

  | Rule | Value | Threshold | Passed |
  |---|---|---|---|
  | ROC-AUC vs champion (paired bootstrap lower bound) | +0.0012 | > 0 | yes |
  | ROC-AUC vs reason-code lookup (lower bound) | −0.0033 | > 0 | **no** |
  | Brier non-inferior vs champion (upper bound) | 0.00027 | ≤ 0.00091 | yes |

- **The pre-registered rule this overrides, verbatim** (`prereg.yaml` `outcomes.lookup_still_wins`): "If the challenger
  does not beat the reason-code lookup on ROC-AUC under the rule above, the champion is not replaced, and
  docs/results.md leads with "the lookup is at least as good as the models" with its intervals. This is a reported
  finding, not a failure."
- **Override:** yes, by the owner, 2026-10-18. This is a post-hoc decision against a pre-registered outcome, and a
  standards deviation; `prereg.yaml` is not changed (it is final), and its recorded decision stays REFUSE.
- **Reason:** the 10k population was a technical feasibility run. That stance is on record before any 200k data existed:
  CLAUDE.md's slice-3 caveat (1), commit `74accd7` (2026-09-20), names the v2 population "the untouched validation". The
  caveat does not say to promote regardless of the gate. From the gate itself: the challenger beats the champion it
  replaces, and the v1 champion does not satisfy the lookup criterion either (v1 test AUC 0.869 vs lookup 0.873 on its
  own window). The operational need: on the 10k population a 13-week monitoring cohort never reaches the pre-registered
  20-positive floor (measured: at most 10), so the monitoring tick (v2 slice 7) could never evaluate.
- **Consequences, stated:** the lookup is still at least as good as the models (docs/results.md); after the gate, this
  override and monitoring, the 200k test window has been used three times, so no untouched validation population is
  left (a fresh-seed population would be needed for one).
- **Considered and declined:** shadow-monitoring the 200k bundle with v1 kept as champion (no override).
- **Re-running the 6b gate:** `ops/gate_run.py` reads `deploy/champion.json` and assumes the frozen-v1 champion, so it
  refuses after this promotion. Reproduce Phase B from a worktree at the Phase-B commit
  (`git worktree add ../phase-b <commit of PR #19>`).
- **PR:** pending (filled in by a follow-up commit on the same PR).
- **Design:** `notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md` (D2).
