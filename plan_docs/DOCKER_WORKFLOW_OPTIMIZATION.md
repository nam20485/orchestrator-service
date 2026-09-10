# Docker Publish Workflow — Optimization Options

Analysis of `.github/workflows/docker-publish.yml` and the four Dockerfiles
(`Dockerfile`, `Dockerfile.webhook`, `Dockerfile.beads`, `deploy/caddy/Dockerfile`)
to identify speed-up opportunities for the `Docker` publish pipeline.

---

## Current Pipeline Shape

```
beads-builder (Rust compile: br + bvr) ─┐
                                        ├─► build matrix (parallel):
                                        │     • orchestrator-service
                                        │     • orchestrator-webhook
                                        │     • orchestrator-caddy   ← unnecessary dependency
```

- `beads-builder` compiles the Rust binaries **once** and publishes a builder
  image (`ghcr.io/.../beads:sha-<sha>`).
- The `build` matrix (3 jobs) pulls that builder image via `build-contexts`
  and copies `br`/`bvr` into the final images (no Rust recompile).
- GHA cache (`type=gha, mode=max`) is already enabled per scope.
- Single platform (`linux/amd64`) — already minimal.
- 3 runtime images build in parallel — already good.

The dominant cost is the **Rust compilation** in `beads-builder` (cache miss),
followed by the **sequential tool installs** (Node, PowerShell, gh, uv, opencode)
in the main `Dockerfile`.

---

## Options — Ranked by Impact-to-Effort

| # | Option | Impact | Effort | Cost | Recommended |
|---|--------|--------|--------|------|-------------|
| 1 | Decouple caddy from beads-builder | High | Low | Free | **Yes** |
| 2 | Disable SBOM/provenance attestations | Medium | Trivial | Free | **Yes** |
| 3 | Shallow checkout (`fetch-depth: 1`) | Low-Med | Trivial | Free | **Yes** |
| 4 | Path-conditional beads-builder skip | Medium | Moderate | Free | **Yes** |
| 5 | Larger GitHub runners | High | Trivial | $$$ | Optional |
| 6 | Pre-built base image | High | High | $ | Later |
| 7 | Merge checkout/metadata/login steps | Low | Trivial | Free | **Yes** (cleanup) |

---

## Recommendations & Rationale

### 1. Decouple caddy from `beads-builder` — RECOMMENDED

**Why:** `deploy/caddy/Dockerfile` has **no `rust-builder` stage** — it's a pure
Alpine + Caddy image. Yet the `build` matrix job declares `needs: beads-builder`,
forcing caddy to wait for the entire Rust compilation (~5-15 min on cache miss)
before it can even start. Caddy's build is ~30s.

**How:** Move caddy out of the `build` matrix into its own job with no `needs`
dependency. It starts immediately on push and finishes while beads-builder is
still running.

**Expected gain:** Caddy image available ~5-15 min earlier. No change to the
critical-path time of the service/webhook images, but unblocks anything that
pulls caddy (e.g. a deploy step that gates on all three).

---

### 2. Disable SBOM/provenance attestations — RECOMMENDED

**Why:** `docker/build-push-action` v7 enables **provenance** and **SBOM**
attestations by default. Generating these adds metadata-creation overhead and
extra registry pushes per image. You already sign with cosign (the higher-value
supply-chain control). If you're not actively consuming the SBOM/provenance
artifacts, they're pure latency.

**How:** Add `provenance: false` and `sbom: false` to the `build-and-push` step
in both jobs.

**Expected gain:** ~10-20s per image (4 images = ~1 min total). Trivial to revert
if you later want attestation.

> **Caveat:** If your security policy requires provenance/SBOM for the cosign
> signature chain, keep them. Cosign signing still works without attestations.

---

### 3. Shallow checkout (`fetch-depth: 1`) — RECOMMENDED

**Why:** `actions/checkout` does a **full clone** by default (entire git
history). Docker builds never need history — only the working tree at the pushed
commit. For a repo with a long history, the full clone adds tens of seconds.

**How:** Add `fetch-depth: 1` to all four `actions/checkout` steps.

**Expected gain:** 10-60s depending on repo size and history depth. Zero risk.

---

### 4. Path-conditional beads-builder skip — RECOMMENDED

**Why:** `beads-builder` (the slow Rust compile) only needs to run when
`Dockerfile.beads` changes (the file pins the beads commit SHAs). Currently it
runs on **every** push that touches *any* image-relevant path (e.g. a change to
`scripts/prompt.ps1` or `webhook_receiver/**` recompiles Rust for nothing).

**How:** Add a path-filter job (using `dorny/paths-filter` or `git diff`) that
gates `beads-builder` behind `if: needs.changes.outputs.beads == 'true'`. When
the beads builder is skipped, the `build` matrix must fall back to the
**branch-latest** beads image instead of `sha-<sha>` (which won't exist).

**Expected gain:** Eliminates the ~5-15 min Rust compile on pushes that don't
touch `Dockerfile.beads` (the common case).

> **Complexity note:** This requires a fallback image tag in `build-contexts`
> and a small refactor of the `needs` chain. See Implementation Plan §4.

---

### 5. Larger GitHub runners — OPTIONAL (costs money)

**Why:** The Rust compile is CPU-bound. GitHub's default `ubuntu-24.04` runner
is 2-core. Larger runners (4, 8, 16, 32-core) cut compile time proportionally.

| Runner | Cores | Relative speedup | Note |
|--------|-------|------------------|------|
| `ubuntu-24.04` (default) | 2 | 1x | current |
| `ubuntu-24.04-4core` | 4 | ~2x | |
| `ubuntu-24.04-8core` | 8 | ~3-4x | best for Rust |
| `ubuntu-24.04-16core` | 16 | diminishing | |

**How:** Change `runs-on: ubuntu-24.04` → `runs-on: ubuntu-24.04-8core` on the
`beads-builder` job (the bottleneck). Runtime image jobs don't need it.

**Expected gain:** Rust compile 3-4x faster on cache miss.
**Cost:** Per-minute billing for larger runners (requires GitHub billing setup).

---

### 6. Pre-built base image — LATER (high effort)

**Why:** The main `Dockerfile` sequentially installs Node, PowerShell, gh, uv,
and opencode from upstream URLs (~3-5 min, partially network-bound). These
versions rarely change but rebuild on every image-affecting push (GHA cache
helps, but cache misses from trixie base bumps reset everything).

**How:** Create a periodic base image (e.g. weekly via schedule) that bakes in
all the tooling, then have the main Dockerfile `FROM` that base image and only
`COPY` app code + the br binary.

**Expected gain:** Runtime image builds drop to seconds (app code + uv sync).
**Effort:** New Dockerfile.base, new workflow job, base-image versioning.
**Recommendation:** Defer until the cheaper options are exhausted.

---

### 7. Merge redundant per-job steps — RECOMMENDED (cleanup)

**Why:** Both jobs repeat `Set Branch Name`, `Extract Docker metadata`,
checkout, buildx setup, and login as near-identical steps. Caddy doesn't need
`build-contexts` or `Set Branch Name`. This is a maintainability/cleanliness
gain more than a speed gain, but reduces runner overhead slightly.

**How:** Use a reusable composite action (`.github/actions/build-and-push`) for
the shared steps, parameterized by image suffix and dockerfile.

**Expected gain:** ~marginal speed; significant DRY/consistency.

---

## Implementation Plan

### Phase 1 — Quick wins (no dependency changes)

**Commit 1: Shallow checkout + disable attestations**
- File: `.github/workflows/docker-publish.yml`
- Add `fetch-depth: 1` to all four `actions/checkout@...` steps (lines 46, 127).
- Add `provenance: false` and `sbom: false` to **both** `build-and-push` steps
  (lines 80-89 and 163-180).
- Verify: push to `development`, confirm images still build + sign; check run
  duration drops slightly.

### Phase 2 — Decouple caddy (high value)

**Commit 2: Split caddy into independent job**
- File: `.github/workflows/docker-publish.yml`
- Remove `orchestrator-caddy` from the `build` matrix (delete the 3 lines at
  120-123).
- Add a new `caddy` job:
  ```yaml
  caddy:
    name: Publish orchestrator-caddy
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      packages: write
      id-token: write
    steps:
      - uses: actions/checkout@...   # with fetch-depth: 1
      - uses: docker/setup-buildx-action@...
      - if: github.event_name != 'pull_request'
        uses: docker/login-action@...
      - id: meta
        uses: docker/metadata-action@...
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}/caddy
          tags: |
            type=raw,value=${{ env.BRANCH_NAME }}-${{ github.run_number }}
            type=raw,value=${{ env.BRANCH_NAME }}-latest
      - id: build-and-push
        uses: docker/build-push-action@...
        with:
          context: ./deploy/caddy
          file: ./deploy/caddy/Dockerfile
          push: ${{ github.event_name != 'pull_request' }}
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          provenance: false
          sbom: false
          cache-from: type=gha,scope=orchestrator-caddy
          cache-to: type=gha,mode=max,scope=orchestrator-caddy
          platforms: linux/amd64
      - if: ${{ github.event_name != 'pull_request' }}
        uses: ./.github/actions/cosign-sign-retry
        with:
          tags: ${{ steps.meta.outputs.tags }}
          digest: ${{ steps.build-and-push.outputs.digest }}
  ```
- Note: the caddy job needs the `BRANCH_NAME` env (add the `Set Branch Name`
  step, or use `${{ github.ref_name }}` inline).
- Verify: caddy job starts immediately and finishes ~5-15 min before the others.

### Phase 3 — Conditional beads-builder (medium complexity)

**Commit 3: Skip beads-builder when Dockerfile.beads unchanged**
- Add a `detect-changes` job before `beads-builder`:
  ```yaml
  detect-changes:
    runs-on: ubuntu-24.04
    outputs:
      beads: ${{ steps.filter.outputs.beads }}
    steps:
      - uses: actions/checkout@...
      - uses: dorny/paths-filter@v3
        id: filter
        with:
          filters: |
            beads:
              - 'Dockerfile.beads'
  ```
- Gate `beads-builder` with `needs: detect-changes` and
  `if: needs.detect-changes.outputs.beads == 'true'`.
- In the `build` matrix, change the builder image reference to fall back when
  the builder was skipped:
  ```yaml
  build-contexts: |
    rust-builder=docker-image://${{ env.BEADS_IMAGE }}@${{ needs.beads-builder.outputs.digest || format('{0}:{1}-latest', env.BEADS_IMAGE, github.ref_name) }}
  ```
  (or simpler: use `${{ env.BRANCH_NAME }}-latest` as the fallback tag).
- **Risk:** If the beads builder was skipped but no `branch-latest` beads image
  exists yet (first run on a new branch), the runtime build fails. Mitigate by
  ensuring `beads-builder` always runs the **first time** on a branch, or by
  using the local fallback stage (omit `build-contexts` when builder skipped).
- Verify: push a webhook-only change, confirm beads-builder is skipped and
  runtime images use the cached builder.

### Phase 4 — Optional enhancements

- **Commit 4 (optional):** Upgrade `beads-builder` runner to
  `ubuntu-24.04-8core` for faster Rust compiles.
- **Commit 5 (later):** Introduce `Dockerfile.base` + weekly base-image build.

---

## Expected Outcome After Phases 1-2

| Metric | Before | After |
|--------|--------|-------|
| Caddy available | ~15 min (waits for Rust) | ~1 min (immediate) |
| Attestation overhead | ~20s × 4 images | 0 |
| Checkout time | full clone | shallow clone |
| Service/webhook critical path | unchanged | unchanged |
| Total wall-clock to all-green | ~15-20 min | ~10-15 min (caddy unblocked) |

Phase 3 further eliminates the Rust compile (~5-15 min) on non-beads pushes,
bringing those runs to ~5-8 min total.
