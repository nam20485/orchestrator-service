# Log Noise Analysis — `gap-miner-v2-lima63-log.txt`

Analyzed `traces/gap-miner-v2-lima63-log.txt` (1832 lines) for lines that carry **zero diagnostic information** — pure framework boilerplate emitted identically on every tool call / config probe / filtered webhook. These are candidates to filter out for a readable run trace.

**Summary:** ~1340 of 1832 lines (~73%) are pure repetition from 6 noise categories below.

| # | Noise category | Lines | % of log | Filter (regex) | checkbox | feedback |
|---|----------------|------:|---------:|-----------------|:---|:---|
| 1 | Permission-always-allowed evaluations | 407 | 22% | `evaluated permission=.*action\.action=allow` |
| 2 | `tracking hash=` (unchanged workspace) | 263 | 14% | `message=tracking hash=` |
| 3 | `message=loop … step=N` counters | 141 | 8% | `message=loop .*step=` |
| 4 | `message=stream … modelID=` | 134 | 7% | `message=stream .*modelID=` |
| 5 | `"llm runtime selected"` (dup of #4) | 134 | 7% | `"llm runtime selected"` |
| 6 | `message=process … messageID=` | 133 | 7% | `message=process .*messageID=` |

Lower-volume, equally empty:

| # | Noise category | Lines | Filter (regex) |
|---|----------------|------:|-----------------|
| 7 | `touching file` internal tracking | 30 | `"touching file"` |
| 8 | Uvicorn access `POST /webhooks/github … 202` (dup of app log) | 26 | `POST /webhooks/github HTTP/1.1" 202` |
| 9 | `Filtered delivery_id` (non-dispatched webhook events) | 25 | `Filtered delivery_id` |
| 10 | Blank lines | 20 | `^$` |
| 11 | `message=loading path=` (config probe, mostly not-found) | 11 | `message=loading path=` |
| 12 | `created id=ses … permission="[…]"` (huge perm-JSON blob) | 11 | `created id=ses` |
| 13 | `resolved path` (cwd echo) | 18 | `"resolved path"` |
| 14 | `formatting file` (post-edit formatter) | 6 | `message=formatting` |

---

## Detail per category (with sample lines)
## Feedback

### 1. `evaluated permission=… action.action=allow` — 407 lines
Every tool invocation logs a permission-eval line, **always** ending in `allow`. The actual tool call is already shown elsewhere; this line adds nothing.
```
L123: ...message="evaluated permission=memory-graph_search_nodes pattern=* action.permission=* action.action=allow action.pattern=*
L132: ...message="evaluated permission=read pattern=AGENTS.md action.permission=read action.action=allow action.pattern=*
```
Filter: `message="evaluated permission=.*action\.action=allow`

### 2. `message=tracking hash=` — 263 lines
Re-emits the same git snapshot hash after every step; the hash rarely changes (only 5 distinct values across 263 lines).
```
L93:  ...message=tracking hash=d28910629f2af8ddd331b44acabe41cab0717105 cwd=...
L126: ...message=tracking hash=d1b7956f848d13f6d64a47dcf18b00207c8d2941 cwd=...
```
Filter: `message=tracking hash=`

### 3. `message=loop … step=N` — 141 lines
Bare loop-iteration counter; the surrounding `process`/`stream` lines mark the step already.
```
L86:  ...message=loop session.id=ses_0d7beca54ffe3ACvvcZv31Yc0u step=0
L127: ...message=loop session.id=ses_0d7beca54ffe3ACvvcZv31Yc0u step=1
```
Filter: `message=loop .*step=`
*Keep* `message="exiting loop"` lines — those are meaningful (session end).

### 4. `message=stream … modelID=` — 134 lines
Restates provider+model on every LLM call. Provider/model are stable within a session.
```
L97:  ...message=stream providerID=zai-coding-plan modelID=glm-4.7 session.id=... agent=orchestrator mode=all
L130: ...message=stream providerID=zai-coding-plan modelID=glm-4.7 ...
```
Filter: `message=stream .*modelID=`

### 5. `"llm runtime selected"` — 134 lines
**Exact duplicate of category 4** (always 134 == 134, emitted as a pair). Runtime is always `ai-sdk`. Redundant.
```
L91:  ...message="llm runtime selected" llm.runtime=ai-sdk llm.provider=zai-coding-plan llm.model=glm-5-turbo
L98:  ...message="llm runtime selected" llm.runtime=ai-sdk llm.provider=zai-coding-plan llm.model=glm-4.7
```
Filter: `"llm runtime selected"`

### 6. `message=process … messageID=` — 133 lines
Per-message ID churn; no payload, just an opaque message ID.
```
L96:  ...message=process session.id=ses_0d7beca54ffe3ACvvcZv31Yc0u messageID=msg_f28413ad7001NBaD5L8uMDuvIe
L129: ...message=process session.id=ses_0d7beca54ffe3ACvvcZv31Yc0u messageID=msg_f2841b0c2001f7AGIsNPC3UzJE
```
Filter: `message=process .*messageID=`

### 7. `"touching file"` — 30 lines
OpenCode's internal file-access bookkeeping (it "touches" a file before reading for snapshotting). The real `Read` action is shown by the `[opencode-err] → Read` line.
```
L134: ...message="touching file" file=/workspace/.../AGENTS.md
L458: ...message="touching file" file=/workspace/.../.opencode/commands/orchestrate-dynamic-workflow.md
```
Filter: `"touching file"`

### 8. Uvicorn access log `POST /webhooks/github … 202` — 26 lines
Duplication of the application-level `Webhook received` log line by the HTTP server.
```
L48:  INFO:     172.24.0.4:51490 - "POST /webhooks/github HTTP/1.1" 202 Accepted
L53:  INFO:     172.24.0.4:51490 - "POST /webhooks/github HTTP/1.1" 202 Accepted
```
Filter: `POST /webhooks/github HTTP/1.1" 202`

### 9. `Filtered delivery_id` — 25 lines
Webhook events that were intentionally not dispatched (e.g. `label`, `issue_comment`, `issues.opened`). Confirming a non-action is noise once filtering is understood.
```
L47:  ...Filtered delivery_id=81f44120... reason=event 'label' not dispatched (only issues)
L657: ...Filtered delivery_id=4c9e4600... reason=event 'issue_comment' not dispatched (only issues)
```
Filter: `Filtered delivery_id`

### 10. Blank lines — 20 lines
Interleaved empty lines from interleaved container output.
Filter: `^$`

### 11. `message=loading path=` — 11 lines
Config-file probing; most paths don't exist. Only the final hit (`opencode.json`) matters.
```
L36: ...message=loading path=/home/app/.config/opencode/config.json
L38: ...message=loading path=/home/app/.config/opencode/opencode.jsonc   # (not-found probes)
```
Filter: `message=loading path=`

### 12. `created id=ses … permission="[…]"` — 11 lines
Session-creation rows that embed a giant JSON permission blob; the useful bits (session id, title) are repeated compactly by the `[opencode-err] •/✓` task lines.
```
L80:  ...message=created id=ses_0d7beca54ffe3ACvvcZv31Yc0u ... title="New session - ..." permission="[{...}]"
L562: ...message=created id=ses_0d7bb2fa9ffewJX8eaYdcDUOW8 ... title="Post initial status update on issue #1" permission="[...]"
```
Filter: `created id=ses`

### 13. `"resolved path"` — 18 lines
cwd echo before each bash command (`arg=/workspace/… resolved=/workspace/…`) — identical on both sides, always.
```
L936: ...message="resolved path" arg=/workspace/nam20485-gap-miner-v2-lima63 resolved=/workspace/nam20485-gap-miner-v2-lima63
L938: ...message="resolved path" arg=/workspace/nam20485-gap-miner-v2-lima63 resolved=/workspace/nam20485-gap-miner-v2-lima63
```
Filter: `"resolved path"`

### 14. `message=formatting` — 6 lines
Post-edit auto-formatter pass; the edit itself is the signal.
```
L913: ...message=formatting file=/workspace/.../plan_docs/workflow-plan.md
```
Filter: `message=formatting`

---

## What to KEEP (high-signal lines)

These carry the real run narrative and must survive any filtering:
- `webhook_receiver.app` lines: `Webhook received`, `Project workspace`, `Prompt assembled`, `Accepted`, `Cloning repo`, `Dispatching orchestration run`, `Started orchestration run`
- `webhook_receiver.runner [opencode]` / `[opencode-err]` content lines (agent reasoning, tool calls `⚙`/`%`/`→`, task markers `•`/`✓`/`✗`, errors)
- `message="exiting loop"` (session termination)
- `docker-entrypoint`, `opencode server listening`, `BeadsLoop started`, `Uvicorn running` (startup)
- Any `level=ERROR`/`level=WARN` lines (none of the above filters target those levels)

## Suggested combined filter (drop-only)

A single grep `-v -E` to strip all boilerplate categories at once:
```
grep -v -E 'evaluated permission=.*action\.action=allow|message=tracking hash=|message=loop .*step=|message=stream .*modelID=|"llm runtime selected"|message=process .*messageID=|"touching file"|"resolved path"|message=loading path=|created id=ses|message=formatting|POST /webhooks/github HTTP/1.1" 202|Filtered delivery_id|^$' gap-miner-v2-lima63-log.txt
```
Applying this leaves the high-signal lines (agent reasoning, tool calls, webhook/app lifecycle, errors) and removes ~1340 boilerplate lines.
