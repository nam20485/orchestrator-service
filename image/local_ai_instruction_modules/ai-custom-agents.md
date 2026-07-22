# LLM Client Subagents

This project uses a shared set of 12 specialist subagents for targeted delegation and automation-first workflows. The same agent names and roles are deployed across all compatible LLM coding clients.

## Source and Deployment

The canonical agent definitions originate from Claude Code format in `nam20485/accp-generator/.source/agents/`. They are converted to each client's native format and deployed user-wide. Conversion reports live in `accp-generator/.source/reports/`.

### Client Deployment Locations

| Client                 | Format                                           | Location                                   |
| ---------------------- | ------------------------------------------------ | ------------------------------------------ |
| VS Code GitHub Copilot | `.agent.md`                                      | `%APPDATA%/Code - Insiders/User/agents/`   |
| Kilo Code              | `custom_modes.yaml` + per-mode `instructions.md` | `~/.kilo/`                                 |
| Codex CLI              | `.prompt.md` + `SKILL.md`                        | `~/.codex/prompts/` and `~/.codex/skills/` |
| OpenCode               | References `AGENTS.md`                           | `opencode.json` in project root            |

Agent names and roles are consistent across all clients. Each client resolves agents from its own location, but the canonical role definitions are listed below.

## Usage

1. Open your LLM client's agent panel (e.g., `@agent-name` in Copilot, `/agents` in others).
2. Select the appropriate specialist agent for the task.
3. Use the **orchestrator** to plan, delegate to specialists, and approve work.
4. Delegate research tasks to the **researcher** agent.

## Agent Index

### Core

- orchestrator — Plans, delegates, approves; avoids direct implementation.
- researcher — Uses configured research tooling to produce citation-rich briefs.
- code-reviewer — Reviews diffs for correctness, security, performance, and style.

### Build & Quality

- qa-test-engineer — Designs and runs tests; validates green builds.
- developer — Generalist for small, scoped tasks across any layer.

### Planning

- planner — Breaks work into tasks with acceptance criteria.

### Specialized

- github-expert — GitHub platform features, APIs, and integrations.
- security-expert — Threat modeling, secrets hygiene, dependency risk.
- debugger — Repro steps, minimal failing tests, fix validation.
- documentation-expert — Writes developer and user docs, quickstarts, and runbooks.
- agent-instructions-expert — Retrieves and inserts guidance from the canonical agent-instructions repository.
- odbplusplus-expert — ODB++ specification and OdbDesign codebase specialist.

## References

- [nam20485/accp-generator](https://github.com/nam20485/accp-generator) — canonical source agents and conversion reports
- [nam20485/agent-instructions](https://github.com/nam20485/agent-instructions) — canonical instruction modules
