# Agent-Readiness Action Plan

Source: [Agent-Readiness Report](https://app.factory.ai/analytics/readiness/https%253A%252F%252Fgithub.com%252Fnam20485%252Forchestrator-service) (Level 2, 2026-07-25).

This document lists every criterion that did **not** score full marks (repo-scope items scored `0/1`, app-scope items scored below `N/N` across the 3 identified applications: `image/`, `webhook_receiver/`, `deploy/caddy/`), then prioritizes remediation using a **Value vs. Effort** matrix.

## Methodology

- **Value (1–5):** impact on agent readiness / risk reduction if fixed. 5 = critical gap, 1 = marginal benefit.
- **Effort (1–5):** implementation difficulty, size, and risk. 1 = trivial (<1 hr, one file), 5 = major/architectural.
- **Score = Value ÷ Effort** — higher score = better return on investment. Used to rank within each group.
- **Groups** (classic impact/effort quadrants):
  - 🟢 **Quick Wins** — Value ≥ 3, Effort ≤ 2
  - 🔵 **Strategic Investments** — Value ≥ 3, Effort ≥ 3
  - 🟡 **Fill-Ins** — Value ≤ 2, Effort ≤ 2
  - 🔴 **Low Priority / Reconsider** — Value ≤ 2, Effort ≥ 3

49 items are tracked below (16 repo-scope, 33 app-scope). Items that were already scored `null` (skipped/not applicable) in the report are excluded.

---

## 🟢 Quick Wins (do these first) — 15 items

Items 1, 2, 3, 4, 6, 8, 9, 10, 11, 12, 13, 14, 15 landed together on
`dev/agent-readiness-quick-wins` (see
`plan_docs/factory/2026-07-25-agent-readiness-quick-wins-items-1-15.md` for
the implementation spec). Items 5 (`codeowners`) and 7 (`pr_templates`) were
intentionally excluded from that PR and remain open.

| # | Criterion | Scope | Value | Effort | Score | Status | Suggested Action |
|---|-----------|-------|:-:|:-:|:-:|---|---|
| 1 | `dependency_update_automation` | Repo | 5 | 1 | 5.00 | ✅ Done | Add `.github/dependabot.yml` for the `uv`/pip and npm ecosystems, weekly schedule. |
| 2 | `test_coverage_thresholds` | App | 5 | 1 | 5.00 | ✅ Done | Add `--cov-fail-under=85` to the pytest invocation (matches AGENTS.md's own 85% target). |
| 3 | `rollback_automation` | Repo | 4 | 1 | 4.00 | ✅ Done | Document a rollback runbook using the existing `<branch>-<run_number>` image tags already published by docker-publish.yml. |
| 4 | `pre_commit_hooks` | App | 4 | 1 | 4.00 | ✅ Done | Add `.pre-commit-config.yaml` wiring the existing `ruff` and `scan-uncommitted-secrets` scripts. |
| 5 | `codeowners` | Repo | 3 | 1 | 3.00 | ⏭ Deferred | Add a root `CODEOWNERS` file. |
| 6 | `gitignore_comprehensive` | Repo | 3 | 1 | 3.00 | ✅ Done | Add `.DS_Store`, `Thumbs.db`, root-level `node_modules/`; decide on `.vscode/` policy. |
| 7 | `pr_templates` | Repo | 3 | 1 | 3.00 | ⏭ Deferred | Add `.github/pull_request_template.md`. |
| 8 | `formatter` | App | 3 | 1 | 3.00 | ✅ Done | Enable `ruff format --check` in `validate.ps1` (ruff is already a dependency). |
| 9 | `cyclomatic_complexity` | App | 3 | 1 | 3.00 | ✅ Done (2 ratchet exemptions) | Enable ruff's `C901` rule + `max-complexity` in `pyproject.toml`. `dashboard.create_dashboard_router` (37) and `runner._run_completion_watcher` (22) exceed `max-complexity=15` and are per-file-ignored pending a dedicated refactor. |
| 10 | `test_isolation` | App | 3 | 1 | 3.00 | ✅ Done | Add `pytest-xdist`; run with `-n auto`. |
| 11 | `api_schema_docs` | App | 3 | 1 | 3.00 | ✅ Done | Add a script/CI step exporting FastAPI's `app.openapi()` to a committed `openapi.json`. |
| 12 | `error_tracking_contextualized` | App | 4 | 2 | 2.00 | ✅ Done | Add the Sentry SDK to `webhook_receiver` with DSN via env var. |
| 13 | `health_checks` | App | 3 | 2 | 1.50 | ✅ Done | Add Docker `HEALTHCHECK` to the root `Dockerfile` and `deploy/caddy/Dockerfile`. |
| 14 | `release_notes_automation` | Repo | 3 | 2 | 1.50 | ✅ Done | Add `release-please` or a conventional-commits changelog action. Implemented as GitHub auto-generated release notes on tag (`.github/release.yml` + `.github/workflows/release.yml`). |
| 15 | `devcontainer` | Repo | 3 | 2 | 1.50 | ✅ Done | Add `.devcontainer/devcontainer.json` (Python 3.11 + Node 24 + `uv`). |

## 🔵 Strategic Investments (high value, plan the work) — 8 items

| # | Criterion | Scope | Value | Effort | Score | Suggested Action |
|---|-----------|-------|:-:|:-:|:-:|---|
| 16 | `agents_md_validation` | Repo | 4 | 3 | 1.33 | Add a CI job that exercises a sample of AGENTS.md commands to catch drift. |
| 17 | `type_check` | App | 4 | 3 | 1.33 | Introduce mypy incrementally (start `--ignore-missing-imports`, tighten per-module). |
| 18 | `distributed_tracing` | App | 4 | 3 | 1.33 | Generate a request/correlation ID at webhook ingress; thread it through logs. |
| 19 | `dast_scanning` | App | 4 | 3 | 1.33 | Add an OWASP ZAP baseline scan against `webhook_receiver` in a staging CI job. |
| 20 | `interactive_qa_runnable` | App | 3 | 3 | 1.00 | Build/document a mock LLM-provider mode so `orchestratorservice` QA doesn't need a paid key. |
| 21 | `alerting_configured` | App | 3 | 3 | 1.00 | Add a Slack/webhook alert on watchdog kill or CI failure. |
| 22 | `strict_typing` | App | 3 | 4 | 0.75 | After the `type_check` baseline lands, enable mypy strict mode per-module. |
| 23 | `metrics_collection` | App | 3 | 4 | 0.75 | Instrument `webhook_receiver` with a Prometheus client + `/metrics` endpoint. |

## 🟡 Fill-Ins (cheap, but lower impact) — 17 items

| # | Criterion | Scope | Value | Effort | Score | Suggested Action |
|---|-----------|-------|:-:|:-:|:-:|---|
| 24 | `large_file_detection` | Repo | 2 | 1 | 2.00 | Add a simple CI `find`/`wc -l` size-guard step. |
| 25 | `issue_templates` | Repo | 2 | 1 | 2.00 | Add root `.github/ISSUE_TEMPLATE/bug.md` + `feature.md` (low urgency: repo has 0 open issues today). |
| 26 | `issue_labeling_system` | Repo | 2 | 1 | 2.00 | Reuse `scripts/import-labels.ps1` to seed priority (P0–P3) and area labels. |
| 27 | `dead_code_detection` | App | 2 | 1 | 2.00 | Add `vulture` for full dead-code coverage beyond ruff's F-rules. |
| 28 | `duplicate_code_detection` | App | 2 | 1 | 2.00 | Add `jscpd` as a CI step. |
| 29 | `unused_dependencies_detection` | App | 2 | 1 | 2.00 | Add `deptry` to the lint step. |
| 30 | `test_performance_tracking` | App | 2 | 1 | 2.00 | Add `--durations=10` to the pytest invocation. |
| 31 | `deployment_observability` | App | 2 | 1 | 2.00 | Add a README line pointing at the existing orchestration dashboard as the deploy-impact check. |
| 32 | `tech_debt_tracking` | Repo | 2 | 2 | 1.00 | Add a CI step that greps and reports TODO/FIXME counts. |
| 33 | `min_release_age` | Repo | 2 | 2 | 1.00 | Once Dependabot/Renovate lands (#1), add `minimumReleaseAge`. |
| 34 | `naming_consistency` | App | 2 | 2 | 1.00 | Enable ruff's `N` (pep8-naming) rule set. |
| 35 | `test_naming_conventions` | App | 1 | 1 | 1.00 | Document the `test_*.py` convention explicitly in AGENTS.md. |
| 36 | `structured_logging` | App | 2 | 2 | 1.00 | Add an explicit `log` block to the Caddyfile for JSON access logs. |
| 37 | `lint_config` | App | 1 | 2 | 0.50 | Add a Dockerfile linter (`hadolint`) covering `image/` and `deploy/caddy/`. |
| 38 | `flaky_test_detection` | App | 1 | 2 | 0.50 | Add `pytest-rerunfailures` with a documented quarantine marker. |
| 39 | `code_quality_metrics` | App | 1 | 2 | 0.50 | Low priority — Codacy already covers `webhook_receiver` adequately. |
| 40 | `log_scrubbing` | App | 1 | 2 | 0.50 | Low priority — `image/`/`deploy/caddy` don't handle app secrets directly. |

## 🔴 Low Priority / Reconsider — 9 items

| # | Criterion | Scope | Value | Effort | Score | Suggested Action |
|---|-----------|-------|:-:|:-:|:-:|---|
| 41 | `error_to_insight_pipeline` | App | 2 | 3 | 0.67 | Depends on `error_tracking_contextualized` (#12) landing first; revisit after. |
| 42 | `monorepo_tooling` | Repo | 2 | 4 | 0.50 | Reconsider only if the 3-service split grows further; Docker Compose is sufficient at this scale. |
| 43 | `unit_tests_exist` | App | 1 | 3 | 0.33 | Not meaningful for config-only `image/`/`deploy/caddy`; rely on their existing integration checks. |
| 44 | `unit_tests_runnable` | App | 1 | 3 | 0.33 | Same reasoning as #43. |
| 45 | `circuit_breakers` | App | 1 | 3 | 0.33 | `image/`/`deploy/caddy` make no outbound calls of their own; low relevance. |
| 46 | `feature_flag_infrastructure` | Repo | 1 | 4 | 0.25 | Not worth adopting a flag platform for a single self-hosted instance. |
| 47 | `profiling_instrumentation` | App | 1 | 4 | 0.25 | No observed performance problem; revisit if latency becomes an issue. |
| 48 | `product_analytics_instrumentation` | App | 1 | 4 | 0.25 | Not applicable — internal tool, no end users to analyze. |
| 49 | `progressive_rollout` | Repo | 1 | 5 | 0.20 | Not worth canary/ring-deployment infra for a single-instance internal service. |

---

## Suggested Sequencing

1. **Sprint 1 — Quick Wins #1–11** (Effort 1 each): mostly single-file additions (`dependabot.yml`, `CODEOWNERS`, PR/issue templates, `--cov-fail-under`, ruff rule toggles). Can likely all land in one PR.
2. **Sprint 2 — Quick Wins #12–15** + **Fill-Ins #24–31**: small tooling additions (Sentry, HEALTHCHECK, vulture/jscpd/deptry, `--durations`).
3. **Sprint 3+ — Strategic Investments #16–23**: plan individually; each is a small project (mypy rollout, tracing, DAST, mock LLM mode, metrics, alerting).
4. **Backlog — Fill-Ins #32–40**: pick up opportunistically alongside related work.
5. **Won't-fix (for now) — Low Priority #41–49**: revisit only if the underlying assumption changes (e.g. the repo grows beyond 3 services, or a real perf/PII need emerges).
