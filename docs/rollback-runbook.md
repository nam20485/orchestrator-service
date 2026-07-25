# Rollback Runbook

## Background

`docker-publish.yml` publishes three images (`orchestratorservice`,
`orchestrator-service/webhook`, `orchestrator-service/caddy`) to GHCR on every
push. Each build gets two immutable tags in addition to the mutable
`<branch>-latest` tag that `compose.yaml` tracks by default:

- `<branch>-<run_number>` (e.g. `main-123`)
- `sha-<commit>` (e.g. `sha-900c82bb4ffe...`)

Because `compose.yaml` always pulls `:${IMAGE_REF:-main}-latest`, there is no
built-in way to run an older, known-good build — a bad `main-latest` push
overwrites the only pinned reference. `compose.rollback.yaml` and
`scripts/rollback.ps1` exist to make "go back to the last good build" a
one-command operation.

## Identify the last-good build

List recent successful publishes for a branch:

```pwsh
pwsh -File ./scripts/rollback.ps1 -Branch main
```

This prints recent `docker-publish.yml` runs with their `<branch>-<run_number>`
tag, commit SHA, and title, e.g.:

```
Recent successful docker-publish.yml runs on 'main':
  main-123   (sha-900c82bb4ffe)   2026-07-24T19:30:25Z   fix opencode boolean flag argument leak
  main-122   (sha-4c0595e5ba47)   2026-07-24T18:08:32Z   switch default model to glm-5 at high variant
```

Cross-reference with `gh run list --workflow=docker-publish.yml` or the
GitHub Actions UI, and with `git log`, to confirm which run predates the
regression.

## Roll back

```pwsh
pwsh -File ./scripts/rollback.ps1 -Tag main-122
```

This brings the stack up with all three services pinned to `main-122` via:

```pwsh
$env:IMAGE_TAG = 'main-122'
docker compose -f compose.yaml -f compose.rollback.yaml up -d
```

Use `-DryRun` to print the resolved command without executing it, and
`-Tag sha-<commit>` to pin by commit instead of run number.

## Verify

```bash
docker compose ps
curl -fsS http://localhost/health   # via webhook-proxy, or :8080 directly on the host running webhook-receiver
docker inspect --format '{{.State.Health.Status}}' <container>
```

Confirm the running image tag matches the rollback target:

```bash
docker compose images
```

## Roll forward

Once a fix is merged and a new build publishes, remove the pin by bringing
the stack up against `compose.yaml` alone (no `compose.rollback.yaml`
overlay), which resumes tracking `<branch>-latest`:

```pwsh
docker compose -f compose.yaml up -d
```
