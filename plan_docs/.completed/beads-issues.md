# Beads Skill Execution — Post-Mortem Fixes

Implements all 5 items from `docs/beads-skill-execution-postmortem.md` "Recommended Fix Order."

## Beads Image Strategy

> **SUPERSEDED (2026-06-26):** The "inline-only" decision below was revisited to
> eliminate the 3× Rust recompile across `docker-publish.yml`. The new approach is
> a **published builder image + buildx `build-contexts` stage override** — see the
> "Builder-Override Strategy" section below. The original rationale is preserved
> for history.

**Original decision (now superseded):** Inline Rust builder (not published GHCR
image). `Dockerfile.beads` is the canonical reference recipe; both consuming
Dockerfiles duplicate the Rust builder stage verbatim. GHA layer caching keeps
rebuilds fast. This avoids the bootstrap problem where `validate.yml` PR builds
would fail before the beads image is published.

---

## Builder-Override Strategy (current)

**Decision:** Compile Rust (`br` + `bvr`) **exactly once** per pipeline run, in a
dedicated `beads-builder` job, and publish the result to GHCR
(`ghcr.io/<repo>/beads`). The runtime images (`Dockerfile`, `Dockerfile.webhook`)
**keep** a self-contained `rust-builder` stage as a fallback, but
`docker-publish.yml` overrides that stage via `buildx build-contexts` with the
published image so it is **not** rebuilt there.

```yaml
build-contexts: |
  rust-builder=docker-image://ghcr.io/<repo>/beads:latest
```

BuildKit then skips the local stage's `cargo install` and pulls the binaries from
the override image. The binary path (`/usr/local/cargo/bin/br`,
`/usr/local/cargo/bin/bvr`) is identical in the local stage and the published
image, so `COPY --from=rust-builder` resolves the same way either way.

**Why not remove the stage entirely (`COPY --from=<published>`)?** That was the
original plan, but it breaks `validate.yml`: PR builds don't push, so the
published `beads:latest` may not exist (bootstrap problem) or may be stale
relative to a PR's beads version. Keeping the local stage keeps PR validation,
local `docker build`, and `test-beads-versions-consistency.sh` working
unchanged — the override is applied **only** in the post-merge publish workflow.

**Impact:** Cold-cache Rust compiles drop from 3 (parallel, ~30 billed min) to 1
(~10 billed min). `Dockerfile.beads` now has a slim runtime final stage so the
published override image is ~100 MB (not the ~1.5 GB full toolchain), minimizing
the per-job pull cost.

### Empirical buildx findings (verified 2026-06-26)

These were tested with a real Docker daemon to avoid re-discovering them:

| Scenario | Works? | Notes |
|----------|:------:|-------|
| `build-contexts` stage override (default builder, local daemon image) | ✅ | Local stage skipped; binary from override. |
| `build-contexts` override, **docker-container driver**, **registry** image | ✅ | Pulls from registry (this is the `docker-publish.yml` path). |
| `build-contexts` override, **docker-container driver**, **local daemon** image | ❌ | Builder tries to pull from Docker Hub (`docker.io/library/<img>`), not the local daemon — fails with "pull access denied". |
| `oci-layout://` override under docker-container driver | ❓ | Export step failed in the test; not yet confirmed. Not relied upon. |

**Key constraint:** `setup-buildx-action` creates a **docker-container** driver
builder. Under that driver, `docker-image://` additional contexts are resolved
against **registries only**, never the local Docker daemon. Therefore
`validate.yml` (which builds beads locally with `load: true`, no push) **cannot**
reuse that local image via `build-contexts`. validate.yml must keep its
self-contained local stages.

**Option considered & rejected for validate.yml:** Pull the *published*
`beads:latest` from GHCR during PR builds. Mechanically possible, but (a) breaks
on the first-ever run / if `:latest` is missing, and (b) validates a PR against
potentially-stale binaries when the PR itself changes the beads version. PR
validation must be self-contained, so validate.yml is left un-optimized (its cold
cost is mitigated by GHA layer cache; warm runs are ~4 min).

---

## Planning Remediation (process)

The bootstrap constraint above was discovered **during implementation**, not
during planning — a process failure. Root causes and correctives:

1. **Did not read this ADR during planning.** The old "Beads Image Strategy"
   decision and its bootstrap rationale live in this very file, but planning
   analyzed only `docker-publish.yml` + the Dockerfiles + run timings. A grep for
   the artifact being changed against `plan_docs/` would have surfaced it.
   - **Corrective:** When changing a build artifact, enumerate **every consumer**
     first — grep the Dockerfiles/keywords across *all* workflows and read the
     relevant ADR before writing the plan.

2. **Did not cross-check `validate.yml`.** Planning never asked "what else builds
     these same Dockerfiles?" — the question that exposes the constraint.
   - **Corrective:** For any shared build recipe, explicitly list each CI context
     that consumes it (publish vs. PR-validation vs. local) and how each resolves
     its dependencies.

3. **Treated the buildx mechanism as assumed.** The plan asserted `build-contexts`
     would work without verifying the docker-container driver's image-resolution
     semantics (local-daemon images fail; registry images work).
   - **Corrective:** Empirically verify any non-trivial toolchain mechanism (or
     mark it explicitly as an *unverified assumption*) before requesting plan
     approval. Read-only planning mode does not prevent reasoning about driver
     semantics or designing a verification step.

---

## Task 1: Add `br` to orchestratorservice image (F2)

### 1a. Create `Dockerfile.beads` (canonical reference)

New file at repo root. Contains the Rust builder stage that compiles `br` (v0.2.15) and `bvr` (v0.2.1) from source using nightly toolchain. This file is the single source of truth — both consuming Dockerfiles mirror its builder stage.

```dockerfile
# Canonical Beads ecosystem builder (br + bvr).
# This file is the single source of truth for beads versions.
# Both Dockerfile and Dockerfile.webhook mirror this stage as `rust-builder`.
#
# beads_rust 0.2.15 (via the `asupersync` dependency) uses `#![feature]`,
# which requires the nightly toolchain.
FROM rust:1.95-slim-bookworm AS rust-builder
RUN apt-get update && apt-get install -y --no-install-recommends git pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
RUN rustup toolchain install nightly && rustup default nightly
# Pinned to immutable commit SHAs for reproducibility; version comment tracks
# the upstream tag (v0.2.15 / v0.2.1).
RUN cargo install --git https://github.com/Dicklesworthstone/beads_rust.git \
      --rev d9f8d7083dee46d04a8e4741c5f535eb7fcabc97 --locked beads_rust
RUN cargo install --git https://github.com/Dicklesworthstone/beads_viewer_rust.git \
      --rev e4506f63214d32c8bcac4f29479a9b80cb932a6a --locked beads_viewer_rust
```

### 1b. Add Rust builder stage to `Dockerfile` (orchestratorservice)

Add a multi-stage Rust builder at the top of `Dockerfile` (before the `FROM debian:trixie-20260518-slim` line), mirroring `Dockerfile.beads` exactly. After the existing `apt-get` / tool install steps, add:

```dockerfile
COPY --from=rust-builder /usr/local/cargo/bin/br /usr/local/bin/br
```

Only `br` is needed (not `bvr`) in the orchestratorservice image.

### 1c. Update `Dockerfile.webhook` to mirror `Dockerfile.beads`

The existing Rust builder stage in `Dockerfile.webhook` (lines 5-12) already matches `Dockerfile.beads`. Verify it stays in sync. No changes needed if already identical.

### 1d. Add beads build to `validate.yml` build job

Add a "Build beads image" step to the `build` job in `.github/workflows/validate.yml`:

```yaml
- name: Build beads image
  uses: docker/build-push-action@v7
  with:
    context: .
    file: ./Dockerfile.beads
    tags: beads:ci
    push: false
    load: true
    cache-from: type=gha,scope=beads
    cache-to: type=gha,mode=max,scope=beads
```

### 1e. Add beads to `docker-publish.yml` matrix

Add a matrix entry for the beads image:

```yaml
- name: beads
  dockerfile: ./Dockerfile.beads
  context: .
  image_suffix: /beads
```

---

## Task 2: Fix ID-format bug in `plan-to-beads/SKILL.md` (F3)

File: `image/.opencode/skills/plan-to-beads/SKILL.md`

### 2a. Fix `<cli_reference>` section

Replace:

```
Bead IDs are printed as `br-<hex>` (e.g., `br-a1b2c3`). Capture them with `| grep -oP 'br-[a-f0-9]+'`.
```

With:

```
Bead IDs are printed after `Created` (e.g., `Created workspace-a1b2c3: Title`). Capture them with `| grep -oP 'Created \K[^:]+'`.
```

### 2b. Fix `<example_script>` section

Replace all 3 occurrences of `grep -oP 'br-[a-f0-9]+'` with `grep -oP 'Created \K[^:]+'`:

- Line with `EPIC_FOUNDATION=$(br create ... | grep -oP 'br-[a-f0-9]+')`
- Line with `TASK_DB=$(br create ... | grep -oP 'br-[a-f0-9]+')`
- Line with `TASK_API=$(br create ... | grep -oP 'br-[a-f0-9]+')`

---

## Task 3: Add delegation instruction to `plan-to-beads/SKILL.md` (F1)

File: `image/.opencode/skills/plan-to-beads/SKILL.md`

### 3a. Add execution delegation section

After the `<instructions>` section's Step 5, add a new section:

```markdown
### Execution Model

You (the orchestrator) do **not** have a `bash` tool. Your role is to:
1. Read and analyze the plan document
2. Generate the complete bash script
3. Delegate script execution to the `developer` agent via the Task tool
4. Verify the developer reports success (all beads created, dependencies linked, sync clean)

Do NOT attempt to execute bash commands yourself. Do NOT use `playwright` or any other tool as a substitute for bash.
```

### 3b. Update `<prerequisites>` section

Replace the current prerequisites with:

```markdown
<prerequisites>
The `br` CLI must be installed in the execution environment. The `developer` agent (which has `bash`) will verify this. If missing, install via:

    cargo install --git https://github.com/Dicklesworthstone/beads_rust.git --tag v0.2.15 beads_rust
</prerequisites>
```

---

## Task 4: Add sync verification to `plan-to-beads/SKILL.md` (F4)

File: `image/.opencode/skills/plan-to-beads/SKILL.md`

### 4a. Update Step 5 to verify sync success

Replace:

```bash
br sync --flush-only
git add .beads/
git commit -m "Add beads DAG from application plan"
```

With:

```bash
br sync --flush-only || { echo "ERROR: br sync failed"; exit 1; }
br sync --status | grep -q "In sync" || { echo "ERROR: beads not in sync"; exit 1; }
git add .beads/
git commit -m "Add beads DAG from application plan"
```

### 4b. Update example_script similarly

Add the sync verification lines to the example script's final section.

---

## Task 5: Switch `/workspace` to bind mount (Decision C)

### 5a. Update `compose.yaml`

Replace the `opencode-workspace` named volume with a bind mount in both services:

**orchestratorservice:**

```yaml
volumes:
  - opencode-memory:/app/.memory
  - ${WORKSPACE_DIR:?WORKSPACE_DIR is required}:/workspace
```

**webhook-receiver:**

```yaml
volumes:
  - ${WORKSPACE_DIR:?WORKSPACE_DIR is required}:/workspace
```

Remove `opencode-workspace` from the top-level `volumes:` block.

### 5b. Update `compose.development.yaml`

Same changes as 5a (bind mount + remove named volume).

### 5c. Update `scripts/prompt.ps1`

Before the `opencode run` invocation, add workspace directory creation:

```powershell
if ($env:WORKSPACE_DIR -and $Workspace.StartsWith('/workspace/')) {
    $relativePath = $Workspace.Substring('/workspace/'.Length)
    $hostPath = Join-Path $env:WORKSPACE_DIR $relativePath
    if (-not (Test-Path -LiteralPath $hostPath)) {
        New-Item -ItemType Directory -Force -Path $hostPath | Out-Null
    }
}
```

### 5d. Update `test/test-compose-config.sh`

Add `WORKSPACE_DIR` to the exported test environment variables:

```bash
export WORKSPACE_DIR="/tmp/test-workspace-$$"
```

### 5e. Update `AGENTS.md`

Update the "Learned Workspace Facts" section to reflect the bind mount change (remove references to named volume `opencode-workspace`, document `WORKSPACE_DIR` requirement).

---

## Task 6: Validation

### 6a. Run full validation

```bash
pwsh -NoProfile -File ./scripts/validate.ps1 -All
```

Fix any failures and re-run until clean.

### 6b. Verify Docker builds

```bash
docker build -f Dockerfile.beads -t beads:test .
docker build -f Dockerfile -t orchestratorservice:test .
docker build -f Dockerfile.webhook -t webhook:test .
```

### 6c. Verify compose config

```bash
WORKSPACE_DIR=/tmp/test OPENCODE_SERVER_PASSWORD=test docker compose -f compose.yaml config --quiet
```

---

## Files Changed

| File | Change |
|------|--------|
| `Dockerfile.beads` | **New** — canonical beads builder recipe |
| `Dockerfile` | Add Rust builder stage + `COPY --from=rust-builder br` |
| `Dockerfile.webhook` | Verify Rust builder matches `Dockerfile.beads` (no change expected) |
| `image/.opencode/skills/plan-to-beads/SKILL.md` | Fix grep patterns, add delegation + sync verify |
| `compose.yaml` | Named volume → bind mount |
| `compose.development.yaml` | Named volume → bind mount |
| `scripts/prompt.ps1` | Add host-side `mkdir -p` for workspace subdirs |
| `test/test-compose-config.sh` | Export `WORKSPACE_DIR` |
| `.github/workflows/validate.yml` | Add beads build step |
| `.github/workflows/docker-publish.yml` | Add beads matrix entry |
| `AGENTS.md` | Update workspace facts |

## Risks

- **Rust compile time in CI:** Both `Dockerfile` and `Dockerfile.webhook` compile `br` independently. GHA layer caching (`cache-from: type=gha`) mitigates this after the first build. If compile time becomes a problem, revisit the published image approach later.
- **`WORKSPACE_DIR` required:** Users running `docker compose up` must now set `WORKSPACE_DIR`. This is intentional — it forces explicit workspace configuration. Document in README if needed.
- **`core.*` crash dumps:** The postmortem notes ~3GB of core dumps in `/workspace`. These should be cleaned separately (out of scope for this plan).

---

## Appendix: Future Phase — Deterministic `plan-to-beads`

**Status:** Out of scope for current plan. Tackle after Tasks 1–6 are implemented and validated.

### Problem

The current `plan-to-beads` skill is **entirely model-driven with zero determinism**:

| Step | Who decides | Deterministic? |
|------|-------------|:--------------:|
| Parse the plan document | LLM | No |
| Decompose into tasks/epics | LLM | No |
| Assign priorities | LLM | No |
| Determine dependencies | LLM | No |
| Generate the bash script | LLM | No |

**Consequence:** Running `/plan-to-beads` twice on the same `application_plan.md` may produce different DAGs — different task counts, different dependency edges, different priorities. There is no schema validation, no formulaic extraction, and no automated check that the generated graph matches the plan's intent.

### Proposed Approach

1. **Structured plan format.** Define a machine-readable plan schema (YAML or JSON) with explicit fields:

   ```yaml
   phases:
     - name: "Phase 1: Foundation"
       priority: 1
       tasks:
         - id: db-schema
           title: "Configure PostgreSQL Schema"
           description: "..."
           acceptance_criteria: [...]
           validation: ["uv run pytest tests/test_db.py -v"]
           depends_on: []
         - id: api-scaffold
           title: "Scaffold FastAPI Endpoints"
           depends_on: [db-schema]
   ```

2. **Deterministic parser.** A script (Python or bash) that reads the structured plan and emits `br` commands — no LLM in the loop. Same input always produces the same DAG.

3. **Validation layer.** The parser verifies:
   - All `depends_on` references resolve to existing task IDs
   - No circular dependencies
   - Every task has at least one acceptance criterion
   - Priority ordering is consistent with dependency ordering

4. **Skill becomes a thin wrapper.** The `plan-to-beads` skill would:
   - Read the structured plan file
   - Invoke the deterministic parser
   - Delegate execution to the `developer` agent (same as current plan)
   - Verify sync and commit

### Migration Path

- **Phase A (current plan):** Ship the model-driven skill with the fixes in Tasks 1–6.
- **Phase B:** Define the structured plan schema; update `/perfect-idea` to emit it alongside the existing Markdown plan.
- **Phase C:** Build the deterministic parser; add it as an alternative execution path in the skill.
- **Phase D:** Deprecate the free-form Markdown plan format; require structured plans for `/plan-to-beads`.

### Open Questions (for Phase B+)

- Should the structured plan be a superset of the Markdown plan (embedded YAML frontmatter) or a separate file?
- How to handle plans that don't fit the schema (research spikes, exploratory tasks)?
- Should the parser be a standalone CLI tool (`br import --from-plan plan.yaml`) or a skill-bundled script?
