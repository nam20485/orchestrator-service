---
name: maestro architecture analysis
overview: Create a repo-visible markdown analysis of viable maestro/supervisor architecture options, grounded in the existing supervisor spec and current webhook/OpenCode service topology.
todos:
  - id: draft-doc
    content: Draft `plan_docs/maestro_architecture_options.md` with option comparison, recommendation, and phased rollout.
    status: completed
  - id: review-doc
    content: Read the drafted document for consistency with the existing supervisor spec and current repo architecture.
    status: completed
  - id: validate-markdown
    content: Run any lightweight existing markdown/document sanity check if available and appropriate.
    status: completed
isProject: false
---

# Maestro Architecture Analysis Plan

I will create a new markdown document under [`plan_docs/`](plan_docs/) that analyzes implementation options for the dual orchestrator architecture described in [`plan_docs/orchestration_supervisor.md`](plan_docs/orchestration_supervisor.md).

## Proposed Document

- Target file: [`plan_docs/maestro_architecture_options.md`](plan_docs/maestro_architecture_options.md)
- Purpose: Compare viable implementation paths for the maestro/orchestrator-supervisor architecture and make a clear recommendation.

## Content Structure

- Context and goals: summarize the maestro pattern, current stack, and why supervision is needed.
- Evaluation criteria: reliability, network feasibility, implementation complexity, security, state management, cost, and fit with the existing webhook/OpenCode stack.
- Options to analyze:
  - Same-job or same-compose dual OpenCode servers.
  - Always-on maestro with direct callback to orchestrators.
  - Always-on maestro with orchestrator polling for directives.
  - GitHub-native hop model using `workflow_dispatch` or issue labels instead of direct server callbacks.
  - Minimal first step: structured status reporting and fail-open observability without automated retry.
- For each option: describe the architecture, pros, cons, risks, and implementation impact.
- Recommendation: favor an always-on maestro plus orchestrator polling as the target architecture, with a phased rollout that starts with structured status reports and fail-open behavior.
- Rationale: direct callback is the clean conceptual model but is fragile across containers, NAT, GitHub Actions runners, and future multi-repo operation; polling keeps all connectivity outbound from orchestrators and fits the existing webhook service.
- Proposed phases: documentation/status schema, maestro service, directive polling, persistence, security hardening.

## Key Existing Anchors

- [`compose.yaml`](compose.yaml) currently has one OpenCode service and a webhook receiver/proxy.
- [`webhook_receiver/app.py`](webhook_receiver/app.py) is a thin asynchronous webhook intake path.
- [`webhook_receiver/runner.py`](webhook_receiver/runner.py) and [`scripts/prompt.ps1`](scripts/prompt.ps1) are natural places for dispatch/status integration.
- [`webhook_receiver/orchestration_prompt.jinja2.md`](webhook_receiver/orchestration_prompt.jinja2.md) is where a final structured supervisor report would be specified.
- [`image/opencode.json`](image/opencode.json) and `image/.opencode/agents/` are where a future `maestro` agent would likely be defined.

## Validation

After creating the markdown file, I will read it back for coherence and run a lightweight markdown-focused sanity check if an existing repo validation command is appropriate and non-invasive.