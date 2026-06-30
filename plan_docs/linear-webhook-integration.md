# Linear Issue-Tracking Integration for the Webhook Receiver

## Goal

Allow issue tracking to live in **Linear** instead of GitHub issues, while git
repositories, branches, and pull requests remain on **GitHub** (`gh`).
Refactor the webhook-receiver to be **provider-agnostic** via a
`Provider` / `EventAdapter` interface with two concrete adapters (`github`,
`linear`), so the ingress, verification, normalization, and write-back seams
are no longer GitHub-specific.

## Locked decisions

1. **Scope:** Issues (create/update/comment/label/close) move to Linear.
   Repo clone, branch, and PR operations stay on GitHub via `gh`.
2. **Abstraction:** Provider interface + two adapters (`github.py`, `linear.py`).
   Not a dynamic plugin registry — extensible by adding a third adapter class.
3. **Linear → repo mapping:** Linear `teamId → owner/repo` config table
   (`LINEAR_TEAM_REPOS`), with an optional Linear "Repository" custom field
   on the issue overriding per-issue. Deterministic and works at issue-creation
   time (Linear payloads carry no `clone_url`).
4. **Linear write-back auth:** Linear personal API key
   (`LINEAR_AGENT_API_KEY`) + a new `scripts/linear.ps1` wrapper that calls
   Linear GraphQL (`https://api.linear.app/graphql`) via curl. Mirrors how
   `GH_ORCHESTRATION_AGENT_TOKEN` feeds `gh`. Key read from env, never hardcoded.

## Architecture

```
Linear webhook POST /webhooks/linear
  → linear.py adapter: verify(hex HMAC-SHA256 + Linear-Signature, 60s webhookTimestamp)
  → normalize() → CanonicalEvent
  → resolve_repo(): LINEAR_TEAM_REPOS[teamId] OR issue "Repository" custom field (GraphQL, cached)
  → build_prompt() → orchestrator prompt (Linear EVENT_DATA)
  → dispatch_to_opencode (existing runner.py)
  → agent: linear.ps1 for issue writes (comment/label/close); gh for repo/PR ops

GitHub webhook POST /webhooks/github   (unchanged path, refactored behind adapter)
```

### Canonical event shape (provider-agnostic)

```python
@dataclass(frozen=True)
class CanonicalEvent:
    source: str                     # "github" | "linear"
    type: str                       # "issue" (normalized) | raw passthrough
    action: str                     # "create" | "update" | "remove" | "opened"...
    delivery_id: str
    sender: str
    issue: IssueRef                 # title, body, url, identifier, labels, raw
    repository: str | None          # "owner/repo" after resolve_repo, else None
    raw: dict                       # full original payload for the prompt
```

- Linear `Linear-Event: Issue` + `action: create|update|remove` → normalized.
- GitHub `issues` events → same shape (`opened/labeled/closed` kept as `action`).
- `resolve_repo` is adapter-specific; the dispatch path is identical afterward.

## Work items

### 1. Provider abstraction (`webhook_receiver/providers/`)
- `base.py`: `Provider` ABC with methods
  `verify(body: bytes, headers: dict, secret: str) -> bool`,
  `normalize(payload: dict, headers: dict) -> CanonicalEvent`,
  `resolve_repo(event: CanonicalEvent) -> str | None`,
  `build_prompt(event: CanonicalEvent, max_payload_chars: int) -> str`.
  Also a `PROVIDERS = {"github": GitHubProvider, "linear": LinearProvider}` registry.
- `github.py`: extract current GH logic from `github.py`, `app.py`
  (`verify_signature`, `_derive_project_slug`, `_safe_branch`, prompt assembly).
  Behavior unchanged for GH-issue webhooks.
- `linear.py`: Linear adapter (see below).

### 2. Linear adapter (`providers/linear.py`)
- **Verify:** HMAC-SHA256 of raw body with `LINEAR_WEBHOOK_SECRET`, compare to
  `Linear-Signature` header (hex, **no `sha256=` prefix**). Use
  `hmac.compare_digest` on raw bytes.
- **Replay protection:** reject if `abs(now_ms - payload.webhookTimestamp) > 60_000`.
- **Headers:** read `Linear-Delivery` (delivery_id), `Linear-Event` (type).
- **Normalize:** `data.title`, `data.description`/`body`, `data.url`,
  `data.identifier` (e.g. `LIN-123`), `data.teamId`, `data.labels`, `action`,
  `actor.name`. Map to `CanonicalEvent`.
- **resolve_repo:** look up `LINEAR_TEAM_REPOS[teamId]`; if absent or
  `LINEAR_USE_CUSTOM_FIELD=true`, query Linear GraphQL for the issue's
  "Repository" custom field. Cache team map in memory. Return `None` if
  unresolvable (adapter logs + emits a `webhook_ignored` event; no dispatch).

### 3. HTTP ingress (`app.py`)
- Add route `/webhooks/{provider}`; resolve adapter from `PROVIDERS`.
  Keep `/webhooks/github` working (provider path segment).
- Generic gate: body-size limit (`max_body_bytes`), optional allowed-events
  allowlist (`WEBHOOK_ALLOWED_EVENTS`, normalized per-provider).
- For each provider: verify → normalize → `resolve_repo` → derive project
  settings → `build_prompt` → `_safe_dispatch` (existing).
- Keep workspace clone/sync logic; it now consumes `event.repository`
  (resolved `owner/repo`) plus a GitHub clone URL built from the resolved repo
  (since Linear gives none): clone via `https://github.com/{owner}/{repo}.git`
  authenticated by `GH_ORCHESTRATION_AGENT_TOKEN`. Document that this requires
  the resolved GitHub repo to be accessible by that token.

### 4. Config (`config.py`)
- Add fields:
  - `linear_webhook_secret: str` (from `LINEAR_WEBHOOK_SECRET`; optional — Linear
    path disabled if unset).
  - `linear_api_key: str | None` (from `LINEAR_API_KEY` / `LINEAR_AGENT_API_KEY`).
  - `linear_api_url: str` (default `https://api.linear.app/graphql`).
  - `linear_team_repos: dict[str,str]` (parse `LINEAR_TEAM_REPOS` JSON).
  - `linear_use_custom_field: bool` (default false).
- Keep `OS_WEBHOOK_SECRET` for GH. `Settings.from_env` must not hard-fail when
  `OS_WEBHOOK_SECRET` is unset but `LINEAR_WEBHOOK_SECRET` is set (at least one
  provider secret required).

### 5. Prompt template (`orchestration_prompt.jinja2.md`)
- Linear adapter emits Linear-shaped `EVENT_DATA`; add Linear clauses using
  `linear.ps1` for issue writes (comment/label/close) and keep `gh` for
  repo/branch/PR ops. Retain existing GH-issue clauses for non-migrated repos.
- Provider-aware `postStatusUpdate`: route to `linear.ps1 comment` when
  `source=linear`, else `gh issue comment`.

### 6. Write-back helper (`scripts/linear.ps1`)
- Thin wrapper mirroring `scripts/prompt.ps1` ergonomics:
  `comment`, `label`, `close` subcommands via GraphQL mutations
  (`commentCreate`, `issueLabel`, `issueUpdate`).
- Reads `LINEAR_API_KEY` from env (never hardcoded). URL resolves like other
  scripts (`LINEAR_API_URL` env or default).
- No auto-install; `curl`/`jq` are already in the image.

### 7. Compose / entrypoint / proxy
- `compose.yaml`: pass `LINEAR_WEBHOOK_SECRET`, `LINEAR_API_KEY`,
  `LINEAR_TEAM_REPOS`, `LINEAR_AGENT_API_KEY` into the receiver container;
  expose `LINEAR_AGENT_API_KEY` (plain env, not `auth.json`) to the
  orchestrator container so agent processes can call Linear.
- `docker-entrypoint.sh`: optionally validate Linear env presence and log the
  enabled providers at startup. Do NOT write Linear keys into `auth.json`.
- Caddy `webhook-proxy`: route `/webhooks/linear` alongside `/webhooks/github`
  (same upstream :8080). No TLS change beyond existing `compose.https.yaml`.
- Optional source-IP allowlist for Linear's published egress IPs.

### 8. Tests
- `providers/linear.py`: signature verification (valid/invalid/missing header),
  `webhookTimestamp` replay (fresh/stale), normalization fixtures under
  `test/fixtures/linear/` using `FAKE-KEY-FOR-TESTING-…` only.
- `resolve_repo`: team-map hit/miss, custom-field override (mocked GraphQL).
- Provider dispatch in `app.py`: `/webhooks/linear` 202 path; verify+401 path;
  unresolvable-repo no-dispatch path.
- Linear simulator template (optional, gated like the GH simulator).
- Update `scripts/validate.ps1 -All` coverage; no real API keys in fixtures.

## Risks / failure modes

- **No clone_url in Linear:** resolved GitHub repo may be inaccessible to
  `GH_ORCHESTRATION_AGENT_TOKEN` → clone fails. Mitigation: log + dispatch
  against existing workspace state (existing `_safe_dispatch` behavior).
- **Team→repo ambiguity:** one team maps to one repo. Multi-repo teams must use
  the custom-field override or split teams. Documented.
- **Replay window:** 60s; clock skew between receiver and Linear egress.
- **Key lifecycle:** long-lived Linear personal API key — rotate via env, never
  commit. Pre-commit secret scan must include new fixtures.
- **Linear webhook retry:** non-200/<5s response triggers up to 3 retries
  (1m/1h/6h); ensure the handler returns 202 fast (existing background-task
  pattern already does).

## Out of scope (follow-ons)

- BeadsLoop ↔ Linear linkage (beads stay repo/PR-based; closing a Linear issue
  on bead completion is a later task).
- Linear OAuth app / multi-workspace authorization.
- Migrating in-flight GitHub issues to Linear.
- A Linear CLI binary (wrapper uses curl only).

## Validation plan

1. Unit: `uv run pytest tests/ -q` covering linear adapter verify/normalize/
   resolve and provider routing.
2. `pwsh -NoProfile -File ./scripts/validate.ps1 -All` (lint + scan + test).
3. Manual: send a signed Linear `Issue` create payload via curl to
   `/webhooks/linear`; confirm 202, workspace clone from resolved repo,
   orchestrator dispatch, and a `linear.ps1 comment` round-trip against a test
   Linear team.
4. Secret scan clean for all new fixtures/scripts.
