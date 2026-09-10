# Production Kubernetes Deployment Plan

## Goal

Migrate `orchestrator-service` from single-host Docker Compose to a production-grade Kubernetes deployment with horizontal scaling, externalized state, and GitOps-based continuous deployment.

## Decisions Resolved

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Shared `/workspace` state | **Per-bead ephemeral workspaces** | Services become stateless; enables horizontal scaling |
| Beads DAG storage | **Postgres** (implementation TBD) | Standard prod choice; enables concurrent access |
| EventStore storage | **Redis** | Fast pub/sub for SSE; ephemeral live streams |
| IaC tool | **Pulumi (Python)** | Matches repo's Python stack; programmable infrastructure |
| Production target | **Hybrid: VPS k8s + managed datastores** | Cheap compute (Hetzner ~€5/mo) + durable managed state (Neon/Upstash) |
| Templating | **Kustomize** | Simpler than Helm; fits 3-environment use case |
| GitOps | **ArgoCD + 3-repo structure** | Standard GitOps pattern; separates app code from infrastructure |
| Environments | **dev / staging / production** | Branch-based promotion (`dev` → `staging` → `production`) |
| Local dev Postgres/Redis | **Managed services (multiplex on env tag)** | Identical to prod; just different connection strings |

## Open Questions

### Beads DAG Persistence (High Priority)

**Problem**: Beads DAG state currently lives in SQLite (`.beads/beads.db`). For prod-readiness, this must move to Postgres. However:

- Forking `beads_rust` to add Postgres support is unmaintainable.
- Bypassing `br` CLI and querying Postgres directly loses valuable dependency graph operations.

**Constraint**: Find a solution that keeps the `beads_rust` ecosystem intact (including `br` CLI operations) while externalizing persistence to Postgres.

#### Functional Requirements

The solution must support the following `br` and `bvr` CLI operations currently used in production:

**Read operations** (called by `BeadsLoop`):

1. `bvr --robot-next --format json` — Graph-aware bead selection using PageRank, betweenness centrality, and blocker ratio to identify the highest-impact unblocked task
2. `bvr --robot-overview --format json` — Compact project snapshot (total beads, ready count, blocked count, etc.) for idle-state logging
3. `br ready --json` — List all open, unblocked beads (fallback when `bvr` is unavailable)
4. `br show <bead_id> --json` — Query bead status after agent completes (used to verify closure)

**Write operations** (called by agents via prompt):
5. `br close <bead_id>` — Mark bead as closed after successful completion
6. `br sync --flush-only` — Sync DAG state (called during `/plan-to-beads` skill)

**Environment configuration**:

- `BD_DB` environment variable points to the beads database (currently `.beads/beads.db`)

#### Non-Functional Requirements

1. **Concurrency**: Multiple pods may query beads simultaneously (read-heavy workload). Write operations (`br close`) are less frequent but must be atomic.
2. **Consistency**: Bead status must be immediately visible after `br close` (no eventual consistency delays that would cause duplicate processing).
3. **Durability**: Beads DAG state must survive pod restarts and cluster failures.
4. **Performance**: `bvr --robot-next` must complete in <1s for DAGs with up to 1000 beads (graph algorithms are CPU-intensive).
5. **Compatibility**: Must work with existing `br` and `bvr` binaries (no recompilation or forking).

#### Integration Points

- **BeadsLoop** (`webhook_receiver/beads_loop.py`) calls `br`/`bvr` via subprocess with `cwd=beads_workspace_root` and `BD_DB` env var
- **Agents** (spawned by BeadsLoop) call `br close` in their workspace
- **Dashboard** queries beads state via `br list --json` and `br ready --json`

#### Possible Directions (to be researched)

1. **Postgres-compatible SQLite wrapper** (e.g., `pgsqlite`, `sqlite3-pg`) — Translate SQLite queries to Postgres at runtime
2. **Beads DAG state sync service** — Sidecar or external process that syncs SQLite to Postgres (bidirectional)
3. **Managed beads service** — Third-party service that abstracts persistence (if one exists)
4. **Postgres FDW (Foreign Data Wrapper)** — Expose Postgres tables as SQLite-compatible views
5. **Other community solutions** for distributed DAG state management

**Status**: Open. User will research and select a solution that fits naturally without "shoe-horning."

## Architecture

### Current State (Single-Host Compose)

```
┌─────────────────────────────────────────────────────────┐
│ Single Host (Docker Compose)                            │
│                                                         │
│  ┌──────────────────┐  ┌──────────────────┐            │
│  │ orchestrator     │  │ webhook-receiver │            │
│  │ service          │  │                  │            │
│  │ - OpenCode       │  │ - BeadsLoop      │            │
│  │ - Agent sessions │  │ - EventStore     │            │
│  │                  │  │ - Dashboard      │            │
│  └────────┬─────────┘  └────────┬─────────┘            │
│           │                     │                       │
│           └──────────┬──────────┘                       │
│                      │                                  │
│              ┌───────▼────────┐                         │
│              │ /workspace     │  (shared bind mount)    │
│              │ - .beads/      │  (SQLite DAG state)     │
│              │ - agent work   │                         │
│              └────────────────┘                         │
│                                                         │
│  ┌──────────────────┐                                   │
│  │ webhook-proxy    │  (Caddy)                          │
│  │ - TLS edge       │                                   │
│  └──────────────────┘                                   │
└─────────────────────────────────────────────────────────┘
```

### Target State (Kubernetes + Managed Services)

```
┌─────────────────────────────────────────────────────────┐
│ Kubernetes Cluster (k3s on VPS)                         │
│                                                         │
│  ┌──────────────────┐  ┌──────────────────┐            │
│  │ orchestrator     │  │ webhook-receiver │            │
│  │ service (N pods) │  │ (N pods)         │            │
│  │ - Stateless      │  │ - Stateless      │            │
│  │ - Ephemeral WS   │  │ - Ephemeral WS   │            │
│  └────────┬─────────┘  └────────┬─────────┘            │
│           │                     │                       │
│           └──────────┬──────────┘                       │
│                      │                                  │
│  ┌──────────────────┐                                   │
│  │ Ingress          │  (nginx + cert-manager)           │
│  │ - TLS edge       │                                   │
│  └──────────────────┘                                   │
└─────────────────────────────────────────────────────────┘
                      │
                      │ (external connections)
                      │
        ┌─────────────┼─────────────┐
        │             │             │
   ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
   │ Postgres│   │  Redis  │   │  GHCR   │
   │ (Neon)  │   │(Upstash)│   │ (images)│
   │         │   │         │   │         │
   │ - Beads │   │ - Events│   │ - App   │
   │   DAG   │   │ - SSE   │   │   imgs  │
   └─────────┘   └─────────┘   └─────────┘
```

## Refactoring Required

### 1. Stateless Services (High Priority)

**Current**: Services share `/workspace` bind mount; agent sessions and `.beads/` live on disk.

**Target**: Services are stateless; each bead gets an ephemeral workspace.

**Changes**:

- **BeadsLoop**: When picking up a bead, clone the target repo into an ephemeral workspace (e.g., `/tmp/bead-<id>`), work, push, close, cleanup.
- **Agent sessions**: Each agent gets its own ephemeral workspace; no shared state between pods.
- **Remove `/workspace` bind mount**: Services no longer need persistent storage.

**Files to modify**:

- `webhook_receiver/beads_loop.py` — refactor to use ephemeral workspaces
- `webhook_receiver/runner.py` — update `dispatch_to_opencode` to use ephemeral workspaces
- `compose.yaml` / `compose.development.yaml` — remove `/workspace` volume mounts (keep for local dev only)

### 2. Externalize EventStore (Medium Priority)

**Current**: `EventStore` is in-process memory in `webhook-receiver`.

**Target**: `EventStore` lives in Redis (managed service).

**Changes**:

- **Replace in-memory EventStore with Redis client**: Use `redis-py` to publish/subscribe to events.
- **Update SSE endpoint**: Subscribe to Redis pub/sub channel instead of in-process queue.
- **Update event emission**: Publish to Redis instead of in-process queue.

**Files to modify**:

- `webhook_receiver/event_store.py` — refactor to use Redis
- `webhook_receiver/dashboard.py` — update SSE endpoint to subscribe to Redis

### 3. Externalize Beads DAG State (High Priority, Implementation TBD)

**Current**: Beads DAG state lives in SQLite file under `.beads/` (shared bind mount).

**Target**: Beads DAG state lives in Postgres (managed service).

**Status**: Open question. User will research a solution that keeps the `beads_rust` ecosystem intact while externalizing persistence to Postgres.

**Placeholder changes** (to be refined once solution is selected):

- Update `BeadsLoop` to use the new persistence mechanism
- Add Postgres connection configuration
- Schema migrations (if applicable)

### 4. Add Health Checks and Readiness Probes (Low Priority)

**Current**: `/health` endpoint exists but no k8s probes.

**Target**: k8s liveness/readiness probes.

**Changes**:

- Add `livenessProbe` and `readinessProbe` to k8s Deployment manifests.
- Ensure `/health` returns 200 only when the service is ready (e.g., Postgres/Redis connections are healthy).

## 3-Repo Structure

### Repo 1: `orchestrator-service` (App Repo)

**Purpose**: Application code, Dockerfiles, CI (GitHub Actions).

**Contents**:

- `webhook_receiver/` — FastAPI app
- `image/` — OpenCode server config
- `Dockerfile`, `Dockerfile.webhook`, `Dockerfile.beads`
- `.github/workflows/docker-publish.yml` — builds images, pushes to GHCR
- `compose.yaml`, `compose.development.yaml` — local dev (kept for backward compatibility)

**CI flow**:

1. Push to `dev`/`staging`/`production` branch → GitHub Actions builds image → pushes to GHCR with tag `<branch>-<sha>`.
2. GitHub Actions updates the image tag in `orchestrator-k8s` repo (Repo 2) via PR or direct commit.

### Repo 2: `orchestrator-k8s` (K8s Config Repo)

**Purpose**: Kustomize manifests for the application.

**Structure**:

```
orchestrator-k8s/
├── base/
│   ├── kustomization.yaml
│   ├── deployment.yaml          # orchestratorservice + webhook-receiver
│   ├── service.yaml
│   ├── ingress.yaml
│   ├── configmap.yaml           # non-sensitive config
│   └── secret.yaml              # placeholder (sealed-secrets or external-secrets)
├── overlays/
│   ├── dev/
│   │   ├── kustomization.yaml
│   │   ├── replica-count.yaml   # 1 replica
│   │   └── image-tag.yaml       # updated by CI
│   ├── staging/
│   │   ├── kustomization.yaml
│   │   ├── replica-count.yaml   # 2 replicas
│   │   └── image-tag.yaml
│   └── production/
│       ├── kustomization.yaml
│       ├── replica-count.yaml   # 3 replicas
│       └── image-tag.yaml
└── README.md
```

**Key manifests**:

- **Deployment**: `orchestratorservice` and `webhook-receiver` (separate Deployments, or combined into one if they're tightly coupled).
- **Service**: ClusterIP services for internal communication.
- **Ingress**: nginx ingress with TLS (cert-manager for Let's Encrypt).
- **ConfigMap**: Non-sensitive config (e.g., `BEADS_POLL_INTERVAL`, `BEADS_MAX_RETRIES`).
- **Secret**: Sensitive config (e.g., `OPENCODE_SERVER_PASSWORD`, `OS_WEBHOOK_SECRET`, `DASHBOARD_TOKEN`, provider API keys). Use external-secrets operator to manage secrets in Git.

### Repo 3: `orchestrator-argocd` (ArgoCD Config Repo)

**Purpose**: ArgoCD "app of apps" pattern.

**Structure**:

```
orchestrator-argocd/
├── apps/
│   ├── dev.yaml                 # ArgoCD Application for dev environment
│   ├── staging.yaml             # ArgoCD Application for staging environment
│   └── production.yaml          # ArgoCD Application for production environment
└── README.md
```

**Example `apps/dev.yaml`**:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: orchestrator-dev
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/nam20485/orchestrator-k8s
    targetRevision: dev
    path: overlays/dev
  destination:
    server: https://kubernetes.default.svc
    namespace: orchestrator-dev
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

**GitOps flow**:

1. Developer pushes code to `orchestrator-service` → CI builds image → pushes to GHCR.
2. CI updates `orchestrator-k8s/overlays/dev/image-tag.yaml` with the new image tag → commits to `dev` branch.
3. ArgoCD detects the change in `orchestrator-k8s` (dev branch) → syncs the dev environment.
4. For promotion to staging: merge `dev` → `staging` in `orchestrator-k8s` (or manual ArgoCD sync).
5. For promotion to production: merge `staging` → `production` in `orchestrator-k8s` (or manual ArgoCD sync).

## Deployment Architecture

### Infrastructure (Hybrid)

| Component | Provider | Cost (est.) | Notes |
|-----------|----------|-------------|-------|
| **Kubernetes cluster** | Hetzner CX22 VPS | ~€5/mo | k3s on Debian; 2 vCPU, 4GB RAM |
| **Postgres** | Neon (free tier) | $0 | 0.5GB storage, 190 compute hours/mo |
| **Redis** | Upstash (free tier) | $0 | 10K commands/day, 256MB storage |
| **TLS certs** | Let's Encrypt | $0 | cert-manager in k8s |
| **Image registry** | GHCR | $0 | GitHub Container Registry |

**Total**: ~€5/mo for dev/staging. Production might need paid tiers (Neon/Upstash) for higher limits.

### Local Dev Environment

- **Debian VM** (local or cloud) running k3s.
- **Pulumi stack**: `Pulumi.dev.yaml` points to managed Postgres/Redis (Neon/Upstash free tiers).
- **ArgoCD**: Deploy ArgoCD on the local k3s cluster; point it at the `dev` branch of `orchestrator-k8s`.
- **Workflow**: Same as production, but on a local cluster.

## Phased Rollout

### Phase 1: Refactor for Statelessness

**Goal**: Make services stateless; externalize state.

**Tasks**:

1. Refactor `BeadsLoop` to use ephemeral workspaces (clone → work → push → close → cleanup).
2. Migrate `EventStore` to Redis.
3. Add health checks and readiness probes.
4. Update local dev setup (compose files) to use Postgres/Redis (e.g., via `docker-compose` services).
5. **Beads DAG persistence**: Research and implement a solution (open question).

**Validation**:

- Run `scripts/validate.ps1 -All` (lint, scan, test).
- Test locally with `docker compose` + Postgres/Redis containers.
- Verify BeadsLoop works with ephemeral workspaces.

### Phase 2: Set Up K8s Infrastructure

**Goal**: Deploy k3s on VPS; set up ArgoCD; create k8s manifests.

**Tasks**:

1. Provision Hetzner VPS; install k3s.
2. Install ArgoCD on the cluster.
3. Create `orchestrator-k8s` repo with Kustomize manifests.
4. Create `orchestrator-argocd` repo with ArgoCD `Application` resources.
5. Set up managed Postgres (Neon) and Redis (Upstash) for dev environment.
6. Deploy dev environment via ArgoCD.

**Validation**:

- Verify ArgoCD syncs the dev environment.
- Verify services are running and healthy.
- Verify dashboard is accessible via ingress.

### Phase 3: CI/CD Integration

**Goal**: Automate image builds and deployments.

**Tasks**:

1. Update `.github/workflows/docker-publish.yml` to tag images with `<branch>-<sha>`.
2. Add a CI step to update `orchestrator-k8s/overlays/dev/image-tag.yaml` after a successful build.
3. Test the full flow: push to `dev` → CI builds image → updates k8s config → ArgoCD syncs.

**Validation**:

- Push a test commit to `dev` branch.
- Verify CI builds the image and updates the k8s config.
- Verify ArgoCD deploys the new image.

### Phase 4: Staging and Production

**Goal**: Set up staging and production environments; implement branch-based promotion.

**Tasks**:

1. Set up managed Postgres/Redis for staging and production (paid tiers if needed).
2. Create `staging` and `production` branches in `orchestrator-k8s`.
3. Configure ArgoCD `Application` resources for staging and production.
4. Test promotion: merge `dev` → `staging` → `production`.

**Validation**:

- Verify staging and production environments are deployed.
- Test promotion flow (merge branches, verify ArgoCD syncs).

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| **Beads DAG persistence solution is complex** | Research phase; user will select a solution that fits naturally. |
| **Managed Postgres/Redis free tiers have limits** | Monitor usage; upgrade to paid tiers if needed. |
| **ArgoCD sync failures** | Set up alerts for ArgoCD sync failures; use `selfHeal: true` to auto-recover. |
| **Secrets management in Git** | Use external-secrets operator to encrypt secrets in Git. |
| **k3s on VPS is less reliable than managed k8s** | Use a managed k8s provider (GKE/EKS) for production if reliability is critical. |

## Validation Plan

### Phase 1 Validation

- [ ] `scripts/validate.ps1 -All` passes (lint, scan, test)
- [ ] Local `docker compose` works with Postgres/Redis
- [ ] BeadsLoop picks up beads, works, pushes, closes (ephemeral workspaces)
- [ ] EventStore publishes/subscribes via Redis
- [ ] `/health` returns 200 when Postgres/Redis are healthy

### Phase 2 Validation

- [ ] k3s cluster is running on VPS
- [ ] ArgoCD is installed and accessible
- [ ] `orchestrator-k8s` repo has Kustomize manifests
- [ ] `orchestrator-argocd` repo has ArgoCD `Application` resources
- [ ] Dev environment is deployed via ArgoCD
- [ ] Dashboard is accessible via ingress

### Phase 3 Validation

- [ ] CI builds images on push to `dev` branch
- [ ] CI updates `orchestrator-k8s/overlays/dev/image-tag.yaml`
- [ ] ArgoCD detects the change and syncs
- [ ] New image is deployed

### Phase 4 Validation

- [ ] Staging environment is deployed
- [ ] Production environment is deployed
- [ ] Promotion flow works (merge `dev` → `staging` → `production`)
- [ ] ArgoCD syncs each environment

## Success Criteria

- Services are stateless and can scale horizontally.
- Beads DAG state is externalized to Postgres (implementation TBD).
- EventStore is externalized to Redis.
- Deployments are automated via GitOps (ArgoCD).
- Branch-based promotion works (dev → staging → production).
- Local dev environment mirrors production (k3s + managed Postgres/Redis).
- All validation checks pass.
