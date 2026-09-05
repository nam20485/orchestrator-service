# Run Report — gap-miner-v2-yankee82 (Permission Deadlock → Watchdog Abort)

**Repo:** `nam20485/gap-miner-v2-yankee82`
**Run ID:** `89445213`
**Session:** `ses_06a612d8affeTtUgYiKwEHBNdT`
**Trigger:** issue body `/gh-issue-tracking-init` (direct-body dispatch)
**Window:** 2026-07-24 19:34:01 → 19:36:29 (aborted by watchdog, ~2.5 min)
**Outcome:** ❌ **INCOMPLETE — aborted by watchdog.** Posted 1 status comment, read the plan + skill assets, was about to delegate to `github-expert` subagent. No issues/board/milestones created.

---

## TL;DR — One failure mode, one latent landmine

| # | Defect | Class | Fatal this run? |
|---|--------|-------|-----------------|
| 1 | **Bash command with non-safe-listed patterns → unanswerable `ask`** → permission deadlock | headless permission | **YES** (caused the abort) |
| 2 | `small_model: google/gemini-3.5-flash` (dead, no API key) → `AI_LoadAPIKeyError` on every title dispatch | project-overrides-global | No (non-fatal, same class as #1) |

The watchdog performed **correctly** — it detected the unanswered `ask` (ask_age=60s, elapsed=150s) and aborted the session via `POST /session/{id}/abort` (status 200). The bug is **upstream**: the cloned repo's config carries headless-hostile settings that the post-clone normalization fix did not yet cover.

---

## Definitive root cause (with evidence)

### The aborting bash command (orchestrator log, 19:35:23)

```
$ cd /workspace/nam20485-gap-miner-v2-yankee82 && git status && echo "---BRANCH---" \
  && git rev-parse --abbrev-ref HEAD && echo "---REMOTE---" && git remote get-url origin \
  && echo "---REPO---" && gh repo view --json nameWithOwner,defaultBranchRef ...
```

Permission evaluation (orchestratorservice logs):

```
pattern="git status"                 → action.pattern="git status*"  action=allow   # opencode built-in safe default
pattern="gh issue comment ..."       → action.pattern="gh issue*"    action=allow   # built-in safe default
pattern="echo \"---BRANCH---\""      → action.pattern="*"            action=ask     # ← catch-all default
pattern="git rev-parse --abbrev-ref" → action.pattern="*"            action=ask
pattern="git remote get-url origin"  → action.pattern="*"            action=ask
pattern="gh repo view ..."           → action.pattern="*"            action=ask
→ message="asking id=per_f95a0126c001AzbO4q3bd11YvQ permission=bash"   # raised, no one to answer
```

### Why bash defaulted to `ask` (config provenance)

| Layer | File | `permission` | Effect on `bash` |
|-------|------|--------------|------------------|
| Global (image) | `/home/app/.config/opencode/opencode.json` | `"allow"` (string — blanket) | would allow all |
| **Project (overrides)** | `.opencode/opencode.jsonc` | `{ "websearch": "allow" }` (**object form**) | **narrows → only opencode built-in safe commands allowed; everything else `ask`** |
| Agent | `.opencode/agents/orchestrator.md` | (no `bash` key) | inherits config default |

opencode merges configs with **project taking precedence**. The project's `permission` **object form replaces** the global blanket string. Under object form, tools not explicitly listed fall to the default action `ask`. `bash` is not listed, so only opencode's built-in read-only safe-command allowlist (`git status*`, `git log*`, `gh issue view*`, …) is permitted; every other bash pattern (bare `echo`, `git rev-parse`, `gh repo view`, and crucially `pwsh *.ps1`) evaluates to `ask`.

### Why `ask` is fatal in this environment

Headless dispatch runs with no human to answer an interactive permission prompt. The `ask` blocks forever → the session deadlocks. The watchdog then trips:

```
[watchdog] PERMISSION DEADLOCK unanswered ask ask_age=60s grace=60s
  detail='message=asking id=per_f95a0126c001AzbO4q3bd11YvQ permission=bash' elapsed=150s — terminating
[watchdog] server session aborted url=.../session/ses_06a612d8affeTtUgYiKwEHBNdT/abort status=200
[orchestratorservice] message=process ... error=Aborted
```

### Latent defect #2 (non-fatal, same class)

```
project .opencode/opencode.jsonc line 7:  "small_model": "google/gemini-3.5-flash"
global  opencode.json                :      "small_model": "zai-coding-plan/glm-4.5-air"
```
Project overrides global. No `GOOGLE_GENERATIVE_AI_API_KEY` in the container → every title-agent dispatch logs:
```
stream error ... modelID=gemini-3.5-flash agent=title
  AI_LoadAPIKeyError: Google Generative AI API key is missing.
```
This is the **identical defect class** (project-level config correct for interactive use, wrong for headless runtime) and confirms the override mechanism.

### Why the existing fix did not apply

`scripts/apply-headless-permissions.ps1` (workflow-launch2, commit `2962e38`) was committed AFTER this workspace was cloned (repo files dated 12:33, fix landed ~19:36). The repo therefore still carries the template's interactive-only config: `{ "websearch": "allow" }` + dead `small_model`. **It was never normalized.**

---

## Solutions (options, recommendation, why)

### Option A — Extend post-clone normalization (RECOMMENDED for permission; permanent)
Make `apply-headless-permissions.ps1` rewrite the project `.opencode/opencode.jsonc` AND agent frontmatter for orchestrator-targeted repos:
- `permission` → `"allow"` (string) **or remove the key** so the global blanket allow inherits.
- Every agent `.opencode/agents/*.md` frontmatter: `edit`/`external_directory`/`webfetch`/`websearch`/`lsp`/`bash` → `allow` (agent-level wins over config, so these are a separate landmine).
- Also normalize `small_model` → `zai-coding-plan/glm-4.5-air`.
- Add a **one-time migration pass** over already-cloned repos in the workspace (fixes yankee82 + any siblings).

**Why:** established project direction (`permission_widening_post_clone_child_script`). Keeps the shared template safe for non-orchestrator repos (which need interactive `ask` safety) while guaranteeing orchestrator repos are headless-safe. Agent-frontmatter coverage is required because opencode resolves agent-level permission above config-level.

### Option B — Fix `small_model` in the template directly (RECOMMENDED for the model; permanent)
Replace `google/gemini-3.5-flash` with `zai-coding-plan/glm-4.5-air` in `intel-agency/agent-context` source (template repo).

**Why:** established decision `model_fix_in_template_not_post_clone`. A working `small_model` is correct for ALL repos (interactive + headless), so it belongs in the template, not a post-clone shim. (Permission blanket-allow does NOT belong in the template — it would break interactive safety for non-orchestrator repos.)

### Option C — Inject permission via the dispatch session-create call
The session already injects deny rules at create time. Add a `bash → allow` rule there.

**Why not:** the session-create permission channel is meant for deny rules; relying on it for allow is fragile and bypasses the config that every other layer reads. Lower recommendation.

### Option D — Immediate mitigation for the current repo (RECOMMENDED now, to unblock)
Edit `/home/nam20485/orchestrator-workspace/nam20485-gap-miner-v2-yankee82/.opencode/opencode.jsonc`:
- `"permission": { "websearch": "allow" }` → `"permission": "allow"`
- `"small_model": "google/gemini-3.5-flash"` → `"small_model": "zai-coding-plan/glm-4.5-air"`

And set `bash`/`edit`/`external_directory`/`webfetch`/`websearch`/`lsp` → `allow` in the project agent frontmatter. Then re-trigger issue #1.

**Why:** unblocks the next run in seconds while A+B are implemented.

### Recommendation (sequenced)
1. **D now** — unblock yankee82.
2. **B** — fix `small_model` in the template (permanent, all future clones).
3. **A** — extend `apply-headless-permissions.ps1` to (a) set permission+agent allow, (b) normalize `small_model`, (c) migrate existing workspace repos.

> Note on the watchdog: **no change needed.** The abort was correct, intended behavior (commit `c651fee`). The fix targets the upstream deadlock cause, not the (working) detector.

---

## Timeline (annotated)

| Time (UTC) | Event |
|------------|-------|
| 19:33:13 | orchestratorservice (re)started, loads global config |
| 19:34:01 | session created `ses_06a612d8affe…` on `/workspace/nam20485-gap-miner-v2-yankee82` |
| 19:34:02 | **title agent**: `stream modelID=gemini-3.5-flash` → `AI_LoadAPIKeyError` (defect #2, non-fatal) |
| 19:34:05 | orchestrator loop begins (`agent=orchestrator`, glm-5) |
| 19:35:14 | step 6: posts status comment via `gh issue comment` (allowed by `gh issue*`) |
| 19:35:23 | **step 7: combined bash command raises `ask`** (`echo`, `git rev-parse`, `gh repo view` → `ask`) — `per_f95a0126c…` |
| 19:35:28–19:36:12 | orchestrator continues reading plan/skill assets (read=allow) while the bash ask hangs |
| 19:36:29 | **watchdog: PERMISSION DEADLOCK** (ask_age=60s, elapsed=150s) → `POST /session/{id}/abort` → 200 |
| 19:36:29 | `message=process … error=Aborted` |

## Evidence sources
- `docker compose logs orchestratorservice` (run 89445213)
- `docker compose logs webhook-receiver` (watchdog)
- project config: `${WORKSPACE_DIR}/nam20485-gap-miner-v2-yankee82/.opencode/opencode.jsonc`
- global config: `/home/app/.config/opencode/opencode.json` (container)
- agent frontmatter: `${WORKSPACE_DIR}/nam20485-gap-miner-v2-yankee82/.opencode/agents/orchestrator.md`
