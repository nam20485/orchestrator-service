# OpenCode 1.18.4 → 1.18.30 Gap Analysis & Pin Decision

**Date:** 2026-09-10
**Status:** CHECKPOINT — awaiting pin decision before implementation
**Context:** PR #49 cursor security review 5160501148 (HIGH: workspace-containment fail-open from `"permission": "allow"` + `--auto`). This report is the Phase 1 deliverable of the approved remediation plan.

## Executive summary

1. **The structured deny-only permission block restores containment on BOTH 1.18.4 and 1.18.30** — verified empirically in local and `--attach` mode, with the repo's full image config and real agent roster (orchestrator → developer specialist with frontmatter, and frontmatter-less `general-purpose`). Parent and subagent external writes fail fast with deny; in-workspace writes, MCP tools (memory-graph, sequential-thinking), and task delegation all keep working; zero `message=asking` events; zero hangs.
2. **The golf38 deadlock does NOT reproduce on 1.18.4 today**, with either the era config (`"*": "deny", "/tmp/**": "allow"` + `mcp_tool`) or the deny-only block. The postmortem's "external_directory rules are inert for task subagents in v1.18.4" mechanism claim is **not confirmed** — in today's probes, global-config rules (both allow and deny) propagate to task subagents. The July deadlock's precise trigger remains unexplained (same version tag, different outcome; see §4).
3. **Zero drift detected in our entire dependency surface** across the 26-release range: `run`/`serve` CLI flags identical (byte-diff of `--help` output), server-log line formats identical (session-created/`parentID=`/`permission=`/slog envelope all match the `webhook_receiver` watchdog/runner regexes), `auth.json` format identical, installer layout identical (`~/.opencode/bin/opencode`), full image config (`default_agent`, `instructions`, agent variants, remote MCP, npx MCP servers) loads and runs cleanly on 1.18.30.
4. **1.18.20+ contains direct upstream fixes for the golf38 failure class** — most notably *"Answer permission requests triggered by subagents during `opencode run`"* (v1.18.20, 2026-08-21) plus resumable subagent failures and broader network-error retries. This is the concrete, non-hygiene argument for upgrading: even an unforeseen `ask` from a subagent in a headless run gets answered instead of hanging.

**Recommendation: Option A — pin 1.18.30** (details in §6).

## 2. Probe methodology

All probes ran on this host (2026-09-10) with isolated `$HOME`s, so the daily-driver 1.18.29 install and real config were untouched:

- `/tmp/ocprobe/home1184` + `/tmp/ocprobe/home130` — bare installs via the official installer (`--version 1.18.4` / `1.18.30`, `--no-modify-path`), shared user `auth.json` (zai-coding-plan API key, `{"type": "api"}` — same format `scripts/docker-entrypoint.sh` writes).
- `/tmp/ocprobe/full1184` + `/tmp/ocprobe/full130` — full copy of the repo's `image/.opencode/` tree into `~/.config/opencode/` (validates the real config loads), with the `"permission"` key swapped for the candidate block.
- Candidate block (deny-only, no `/tmp` carve-out, no `--auto` on any run):

```jsonc
"permission": {
  "*": "allow",
  "external_directory": { "*": "deny" }
}
```

- Attach-mode topology mirrors production: `opencode serve` (127.0.0.1:14199/14200, `OPENCODE_SERVER_PASSWORD`, `--print-logs --log-level INFO`) + `opencode run --attach http://… --dir <ws> --agent orchestrator --model zai-coding-plan/glm-5.3-flash`.

## 3. Probe results matrix

| # | Topology | Config | Version | Parent external write | Subagent external write | Asks | Exit |
|---|----------|--------|---------|----------------------|------------------------|------|------|
| 1 | local `run`, minimal cfg | deny-only | 1.18.30 | DENIED fail-fast | DENIED fail-fast (built-in subagent) | 0 | 0 |
| 2 | local `run`, minimal cfg | deny-only | 1.18.4 | DENIED fail-fast | DENIED fail-fast | 0 | 0 |
| 3 | `--attach`, full image cfg, `orchestrator` → `developer` (frontmatter deny) | deny-only | 1.18.30 | DENIED | DENIED | 0 | 0 |
| 4 | `--attach`, full image cfg, same delegation | deny-only | 1.18.4 | DENIED | DENIED | 0 | 0 |
| 5 | `--attach`, full image cfg, `orchestrator` → `general-purpose` (no frontmatter) | deny-only | 1.18.4 | n/a | DENIED | 0 | 0 |
| 6 | `--attach`, **golf38-era cfg** (`"*": "deny", "/tmp/**": "allow"` + `mcp_tool` allows) | era | 1.18.4 | `/tmp` write **ALLOWED** | `/tmp` write **ALLOWED** | 0 | 0 |

Deny evidence (probes 1–5): tool error *"The user has specified a rule which prevents you from using this specific tool call."* with `permission=external_directory` evaluation lines in the logs; external files never created; in-workspace files (`orch-inside.txt`, `dev-inside.txt`, `inside.txt`, `sub-inside.txt`) created; `permission=task`, `permission=memory`, `permission=sequential`, `permission=edit`, `permission=read` evaluations all allowed under `"*": "allow"` (MCP + delegation unaffected).

Probe 6 is the golf38 reproduction attempt: the exact era config on the same version tag now lets parent AND subagent write `/tmp` (the allow propagates; no ask, no hang). **The July deadlock did not reproduce.**

## 4. What this means for the golf38 postmortem

`traces/gap-miner-v2-golf38-run-report.md` (2026-07-24) concluded that `external_directory` config and agent-frontmatter rules are *inert* for task-spawned subagents in v1.18.4 (opencode #30527 cluster), making `"permission": "allow"` + `--auto` the only workable headless configuration. Today's matrix contradicts the mechanism claim: on the same tag, global allows and denies both reach subagents, frontmatter or not, local or attach mode.

Possible explanations for the divergence (unresolved, none confirmable from the artifacts we kept):

- The July image's actual runtime state differed from what the postmortem verified as "baked" (e.g., config parse silently dropping the whole block — the era block's `mcp_tool` key is not a valid permission key today).
- An argv interaction with the then-passed `--dangerously-skip-permissions true` pair (unknown flag + value).
- A transient server state patched within the 1.18.4 tag's binary distribution window.

**Decision impact:** containment does not depend on resolving this mystery — the deny-only block holds on both versions in every topology we can construct. But the mystery is exactly why the upgrade still matters: v1.18.20's *"Answer permission requests triggered by subagents during `opencode run`"* removes the entire hang class regardless of trigger, and the watchdog `permission_deadlock` kill (60 s grace) stays as the last line of defense.

## 5. Dependency-surface gap table (1.18.4 → 1.18.30)

| Surface | Evidence | Verdict |
|---|---|---|
| `opencode run` flags (`--attach --dir --model --agent --thinking --auto --format --print-logs --log-level --variant`) | token diff of `--help` output: **zero added/removed** | ✅ no change |
| `opencode serve` flags (`--hostname --port --log-level --print-logs`) | token diff of `--help`: **zero added/removed** | ✅ no change |
| Server log lines parsed by `watchdog.py` (`created id=ses_…`, `directory=`, `parentID=`, `message=asking`, `permission=<type>`, `version=<x>`) | byte-identical formats in both server logs | ✅ no change |
| Client glyph stream / slog envelope parsed by `run_stream.py` / `runner.py` | same shapes in both probe err-streams | ✅ no change |
| `auth.json` format (`{"<provider>": {"type": "api", "key": …}}`) | identical; entrypoint script unchanged | ✅ no change |
| Installer layout (`$HOME/.opencode/bin/opencode`) | both installs landed there | ✅ no change |
| Global config auto-load (`~/.config/opencode/opencode.json`) | 1.18.30 serve log: `loading path=…opencode.json` | ✅ no change |
| Full image config schema (`default_agent`, `instructions`, agent `model`/`variant` pins, local npx + remote `type: "remote"` MCP) | full tree loaded + MCP servers ran (memory writes, sequential-thinking) on 1.18.30 with Node 20 | ✅ no change |
| Release notes 1.18.5→1.18.30, permission/schema/breaking keywords | only v1.18.20 subagent-permission fixes + an unrelated Desktop-only line | ✅ no breaking changes found |

Relevant upstream highlights in range: **v1.18.20** — answer subagent permission asks during `opencode run`; surface failed subagent tool calls with resumable `task_id`; surface resumable subagent failures; retry `finish_reason: network_error` and more network-error variants (the golf38 `AI_APICallError` symptom class). **v1.18.5 shipped 2026-07-24** — the day after golf38; i.e., 1.18.4 was the then-current release and the whole range postdates the incident.

## 6. Pin options (decision required)

| Option | Pin | Pros | Cons |
|---|---|---|---|
| **A (recommended)** | `1.18.30` | Latest stable; subagent-ask answering + resumable subagent failures + network retries; zero drift found in our surface; longest runway before next bump | Largest jump (26 releases) — mitigated by the full-surface matrix above and the Phase 5 live smoke |
| B | `1.18.20` | Minimal release containing the subagent permission-ask fixes; 16 fewer releases of delta | Still needs identical validation effort; older; no upside found since surface drift is zero anyway |
| C | keep `1.18.4` | No image change at all | Containment still works (deny-only block), but none of the deadlock-class fixes; stays 3 months stale; contradicts the upgrade intent you already approved |

All three options include the same Phase 2 config change (deny-only block) and `--auto` removal — those are version-independent per the matrix.

## 7. Artifacts

- Probe binaries/configs/logs: `/tmp/ocprobe/` (ephemeral; `serve130.log`, `serve1184b.log`, `attach130-*`, `attach1184-*`, `iso1184-*`, `repro1184-*`, `run-help-*`, `serve-help-*`, `releases.txt`)
- Upstream references: [opencode #30527](https://github.com/anomalyco/opencode/issues/30527) (closed 2026-06-08), [PR #14583](https://github.com/anomalyco/opencode/pull/14583) (`--dangerously-skip-permissions`, closed unmerged 2026-02-21), [permissions docs](https://opencode.ai/docs/permissions) (`--auto` enforces explicit denies; `external_directory` defaults to `ask`; subagents inherit parent deny rules)
