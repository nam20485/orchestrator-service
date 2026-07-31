# Complexity Hotspots

See [Cleanup Opportunities](index.md) for the parent overview. Line counts, coverage percentages, and commit counts below are measured directly from `/home/nam20485/src/github/nam20485/orchestrator-service` (`wc -l`, `coverage.xml`, `git log --oneline`), not estimated.

## `webhook_receiver/` module size ranking

```
850  webhook_receiver/runner.py
808  webhook_receiver/watchdog.py
948  webhook_receiver/dashboard.py
738  webhook_receiver/beads_loop.py
430  webhook_receiver/app.py
```

These five modules are the largest in the package by a wide margin (the next-largest, `workspace.py`, is 339 lines). They are also the most-churned: `app.py` has 17 commits touching it, `dashboard.py` 14, `runner.py` 12, `beads_loop.py` 10, `watchdog.py` 9 (via `git log --oneline -- <path>`), consistent with them being the modules that keep needing follow-up fixes (see [Pitfalls](../background/pitfalls.md) for two concrete incidents in `runner.py`/webhook entrypoint interaction and `watchdog.py`'s activity-detection redesign).

## Coverage relative to the repo's stated floor

`AGENTS.md` states test coverage must stay **> 85%** as new code is added. Per-file line-rate from `coverage.xml` (root: `coverage.xml`), sorted ascending:

| File | Line coverage | Margin above 85% floor |
|------|---------------|------------------------|
| `webhook_receiver/dashboard.py` | 86.11% | 1.1 pts |
| `webhook_receiver/app.py` | 87.50% | 2.5 pts |
| `webhook_receiver/event_store.py` | 87.50% | 2.5 pts |
| `webhook_receiver/workspace.py` | 88.14% | 3.1 pts |
| `webhook_receiver/simulator.py` | 88.24% | 3.2 pts |
| `webhook_receiver/runner.py` | 88.80% | 3.8 pts |
| `webhook_receiver/run_narrative.py` | 90.48% | 5.5 pts |
| `webhook_receiver/bead_context.py` | 93.07% | 8.1 pts |

`dashboard.py` is the module of concern: it is simultaneously the **largest** file in the package (948 lines) and the file with the **least margin** above the repo's own coverage floor (86.11%, only 1.1 points above 85%). A modest coverage regression in a follow-up change to `dashboard.py` would drop it below the stated threshold without needing a large amount of new untested code. `app.py`, `event_store.py`, `workspace.py`, `simulator.py`, and `runner.py` sit in the same narrow 87–89% band and share the same risk, though at smaller absolute sizes.

## Actionable follow-ups

1. **Prioritize additional test coverage for `dashboard.py` before its next feature change.** It has the largest surface area (948 lines) combined with the smallest safety margin (1.1 pts) of any module in the package; a coverage-neutral refactor is the safest next step before adding new dashboard functionality.
2. **Treat `runner.py`, `watchdog.py`, `beads_loop.py`, and `dashboard.py` as the modules requiring the most reviewer scrutiny on any future PR** — they are the four largest files and, per `git log`, also the four with the most historical commits (fix churn), a combination the testing-approach case study (see [Pitfalls](../background/pitfalls.md)) already shows can hide real regressions (`runner.py`'s log-dir write path) behind a passing local/CI test run when the change under review is container/runtime-adjacent.
3. **When splitting any of the four largest modules, keep the coverage floor check in mind per extracted unit** — splitting `dashboard.py` (86.11%) into smaller modules without adding matching tests to each new module risks silently dropping below 85% on the newly separated pieces even if the aggregate looks unchanged.
