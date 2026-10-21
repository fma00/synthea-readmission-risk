# Run ledger

**Simulated clock.** Each tick advances a simulated date by one week through the held-out 2023–2026 discharges of a synthetic 200,000-patient population; the wall-clock time is when the tick ran. Ticks labelled catch-up (laptop) were run by the owner to fill the first weeks; ticks labelled burn-in ran at an accelerated cadence, either fired by GitHub's scheduler (scheduled) or, because that scheduler dropped most slots, dispatched by a timer on the owner's laptop (dispatched); after the burn-in, weekly ticks are scheduled once a week. This ledger shows repeatability and idempotency, not weeks of unattended operation. The monitored discharges come from a gold table built with hindsight (deaths and later stays shape which discharges are included), so this is a demonstration of the monitoring mechanics, not an estimate of live calibration. The exact Poisson interval treats readmissions as independent events; patients with several stays in a cohort make it somewhat too narrow. Synthetic data, not for clinical use. Source of truth: BigQuery `serving.scoring_runs` and `serving.monitoring`.

Monitored bundle: `569fdab6c17d…`.

## Ticks

| wall time | cadence | sim date | status | verdict | O/E [95% CI] | observed / expected | n matured | AUC (report only) | immature expected | link |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-21 10:20 UTC | burn-in (dispatched, accelerated) | 2023-10-16 | succeeded | ok | 1.151 [0.864, 1.501] | 54.0 / 46.9 | 2157.0 | 0.861 | 12.1 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39583252242) |
| 2026-10-21 09:50 UTC | burn-in (dispatched, accelerated) | 2023-10-09 | succeeded | ok | 1.151 [0.857, 1.513] | 51.0 / 44.3 | 2162.0 | 0.869 | 15.0 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39579421403) |
| 2026-10-21 09:20 UTC | burn-in (dispatched, accelerated) | 2023-10-02 | succeeded | ok | 1.189 [0.888, 1.559] | 52.0 / 43.7 | 2171.0 | 0.868 | 15.5 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39575534341) |
| 2026-10-21 08:50 UTC | burn-in (dispatched, accelerated) | 2023-09-25 | succeeded | ok | 1.195 [0.890, 1.571] | 51.0 / 42.7 | 2169.0 | 0.865 | 16.2 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39571676250) |
| 2026-10-20 21:37 UTC | burn-in (scheduled, accelerated) | 2023-09-18 | succeeded | ok | 1.190 [0.886, 1.564] | 51.0 / 42.9 | 2180.0 | 0.884 | 15.4 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39501115506) |
| 2026-10-20 18:00 UTC | burn-in (scheduled, accelerated) | 2023-09-11 | succeeded | ok | 1.186 [0.877, 1.568] | 49.0 / 41.3 | 2168.0 | 0.879 | 15.5 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39470293730) |
| 2026-10-19 20:13 UTC | burn-in (scheduled, accelerated) | 2023-09-04 | succeeded | ok | 1.201 [0.891, 1.583] | 50.0 / 41.6 | 2172.0 | 0.878 | 14.5 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39316779856) |
| 2026-10-19 09:13 UTC | dispatch | 2023-08-21 | succeeded | ok | 1.222 [0.904, 1.616] | 49.0 / 40.1 | 2171.0 | 0.882 | 14.8 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39227773570) |
| 2026-10-19 09:05 UTC | dispatch | 2023-08-21 | succeeded | ok | 1.222 [0.904, 1.616] | 49.0 / 40.1 | 2171.0 | 0.882 | 14.8 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39226802048) |
| 2026-10-19 08:57 UTC | dispatch | 2023-08-21 | succeeded | ok | 1.222 [0.904, 1.616] | 49.0 / 40.1 | 2171.0 | 0.882 | 14.8 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39225834375) |
| 2026-10-19 08:49 UTC | dispatch | 2023-08-21 | succeeded | ok | 1.222 [0.904, 1.616] | 49.0 / 40.1 | 2171.0 | 0.882 | 14.8 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39224862336) |
| 2026-10-19 08:40 UTC | dispatch | 2023-08-21 | succeeded | ok | 1.222 [0.904, 1.616] | 49.0 / 40.1 | 2171.0 | 0.882 | 14.8 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39223818333) |
| 2026-10-19 08:32 UTC | dispatch | 2023-08-21 | succeeded | ok | 1.222 [0.904, 1.616] | 49.0 / 40.1 | 2171.0 | 0.882 | 14.8 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39222853927) |
| 2026-10-19 07:21 UTC | dispatch | 2023-08-21 | succeeded | ok | 1.222 [0.904, 1.616] | 49.0 / 40.1 | 2171.0 | 0.882 | 14.8 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39214900473) |
| 2026-10-19 07:13 UTC | dispatch | 2023-08-21 | succeeded | ok | 1.222 [0.904, 1.616] | 49.0 / 40.1 | 2171.0 | 0.882 | 14.8 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39214023014) |
| 2026-10-19 07:05 UTC | dispatch | 2023-08-21 | succeeded | ok | 1.222 [0.904, 1.616] | 49.0 / 40.1 | 2171.0 | 0.882 | 14.8 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39213125521) |
| 2026-10-18 20:37 UTC | dispatch | 2023-08-28 | succeeded | ok | 1.139 [0.834, 1.520] | 46.0 / 40.4 | 2156.0 | 0.889 | 15.8 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39163742615) |
| 2026-10-18 20:29 UTC | dispatch | - | refused (clock) | - | - | - | - | - | - | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39163419990) |
| 2026-10-18 20:25 UTC | dispatch | - | refused (clock) | - | - | - | - | - | - | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39163124023) |
| 2026-10-18 20:20 UTC | dispatch | 2023-08-21 | succeeded | ok | 1.222 [0.904, 1.616] | 49.0 / 40.1 | 2171.0 | 0.882 | 14.8 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39162432781) |
| 2026-10-18 19:55 UTC | dispatch | 2023-08-21 | succeeded | ok | 1.222 [0.904, 1.616] | 49.0 / 40.1 | 2171.0 | 0.882 | 14.8 | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39160504842) |
| 2026-10-18 19:41 UTC | catch-up (laptop) | 2023-08-14 | succeeded | ok | 1.262 [0.940, 1.659] | 51.0 / 40.4 | 2175.0 | 0.892 | 15.7 | laptop |
| 2026-10-18 19:40 UTC | catch-up (laptop) | 2023-08-07 | succeeded | incomplete_cohort | 1.128 [0.823, 1.509] | 45.0 / 39.9 | 2140.0 | 0.886 | 14.0 | laptop |
| 2026-10-18 19:39 UTC | catch-up (laptop) | 2023-07-31 | succeeded | incomplete_cohort | 1.084 [0.774, 1.476] | 40.0 / 36.9 | 1966.0 | 0.874 | 13.6 | laptop |
| 2026-10-18 19:38 UTC | catch-up (laptop) | 2023-07-24 | succeeded | incomplete_cohort | 1.104 [0.773, 1.529] | 36.0 / 32.6 | 1801.0 | 0.879 | 15.3 | laptop |
| 2026-10-18 19:37 UTC | catch-up (laptop) | 2023-07-17 | succeeded | incomplete_cohort | 1.087 [0.743, 1.534] | 32.0 / 29.4 | 1640.0 | 0.885 | 15.9 | laptop |
| 2026-10-18 19:36 UTC | catch-up (laptop) | 2023-07-10 | succeeded | incomplete_cohort | 1.093 [0.732, 1.569] | 29.0 / 26.5 | 1490.0 | 0.877 | 13.9 | laptop |
| 2026-10-18 19:35 UTC | catch-up (laptop) | 2023-07-03 | succeeded | incomplete_cohort | 1.088 [0.711, 1.594] | 26.0 / 23.9 | 1307.0 | 0.871 | 13.7 | laptop |
| 2026-10-18 19:34 UTC | catch-up (laptop) | 2023-06-26 | succeeded | incomplete_cohort | 1.061 [0.673, 1.592] | 23.0 / 21.7 | 1148.0 | 0.913 | 12.3 | laptop |
| 2026-10-18 19:33 UTC | catch-up (laptop) | 2023-06-19 | succeeded | incomplete_cohort | 1.004 [0.605, 1.569] | 19.0 / 18.9 | 962.0 | 0.906 | 11.2 | laptop |
| 2026-10-18 19:32 UTC | catch-up (laptop) | 2023-06-12 | succeeded | incomplete_cohort | 0.993 [0.567, 1.612] | 16.0 / 16.1 | 800.0 | 0.895 | 11.5 | laptop |
| 2026-10-18 19:31 UTC | catch-up (laptop) | 2023-06-05 | succeeded | incomplete_cohort | 1.021 [0.528, 1.784] | 12.0 / 11.8 | 622.0 | 0.884 | 12.7 | laptop |
| 2026-10-18 19:30 UTC | catch-up (laptop) | 2023-05-29 | succeeded | incomplete_cohort | 1.069 [0.513, 1.967] | 10.0 / 9.4 | 459.0 | 0.891 | 12.8 | laptop |
| 2026-10-18 19:29 UTC | catch-up (laptop) | 2023-05-22 | succeeded | incomplete_cohort | 0.976 [0.392, 2.011] | 7.0 / 7.2 | 301.0 | 0.935 | 12.3 | laptop |
| 2026-10-18 19:28 UTC | catch-up (laptop) | 2023-05-15 | succeeded | incomplete_cohort | 0.365 [0.009, 2.031] | 1.0 / 2.7 | 128.0 | 0.874 | 14.0 | laptop |
| 2026-10-18 19:27 UTC | catch-up (laptop) | 2023-05-08 | succeeded | incomplete_cohort | - | 0.0 / 0.0 | 0.0 | - | - | laptop |
| 2026-10-18 19:26 UTC | catch-up (laptop) | 2023-05-01 | succeeded | incomplete_cohort | - | 0.0 / 0.0 | 0.0 | - | - | laptop |
| 2026-10-18 19:25 UTC | catch-up (laptop) | 2023-04-24 | succeeded | incomplete_cohort | - | 0.0 / 0.0 | 0.0 | - | - | laptop |
| 2026-10-18 19:24 UTC | catch-up (laptop) | 2023-04-17 | succeeded | incomplete_cohort | - | 0.0 / 0.0 | 0.0 | - | - | laptop |

## Other scoring runs

| wall time | run date | status | trigger | link |
|---|---|---|---|---|
| 2026-10-18 19:04 UTC | 2025-06-30 | succeeded | workflow_dispatch | [run](https://github.com/fma00/synthea-readmission-risk/actions/runs/39156975790) |
| 2026-10-18 14:30 UTC | 2023-04-17 | succeeded | laptop | laptop |
