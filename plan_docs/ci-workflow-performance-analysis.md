# CI Workflow Performance Analysis

> **Status:** Analysis complete (evidence-based, measured run timings).
> **Date:** 2026-06-29
> **Scope:** All GitHub Actions workflows in `.github/workflows/`.
> **Method:** Real run durations pulled via `gh run list` / `gh run view` from the last 10 runs of each workflow, cross-referenced against workflow YAML and Dockerfile source. No estimates — all durations are measured.

---

## Evidence Base (Measured Run Timings)

| Workflow | Trigger | Typical duration | Runs on |
|---|---|---|---|
| **trivy** | push + PR | **~13–14 min** | every push & PR |
| **docker-publish** | push (main/dev) + tags | **~12 min** | merges to main/dev |
| **validate** | push + PR | ~1.5–2.5 min | every push & PR |
| dependency-review | PR | ~30 s | PRs |
| droid / droid-review / opencode | comments/PRs | on-demand | event-driven |

### Source data points

**trivy.yml** (last 10 runs, all ~13 min):
- `28354478634` (PR): 07:01:30 → 07:14:46 = ~13m16s
- `28354476507` (push): 07:01:27 → 07:14:34 = ~13m07s
- `28353778588` (PR): 06:45:29 → 06:59:14 = ~13m45s
- `28351767699` (PR): 05:58:11 → 06:12:03 = ~13m52s

**docker-publish.yml** (runs that actually build):
- `28299720201` (push): 19:40:31 → 19:53:08 = ~12m37s
- `28271272116` (push): 23:37:47 → 23:51:57 = ~14m10s
- `27910208463` (push): 16:17:26 → 16:27:46 = ~10m20s

**validate.yml** (last runs, ~1.5–2.5 min):
- `28354478630` (PR): 07:01:30 → 07:03:09 = ~1m39s
- `28357213575` (PR): 07:57:41 → 07:59:34 = ~1m53s

---

## 1. Longest / Slowest Operations and Why

### #1 — `trivy.yml`: ~13 min on **every** push and PR (the worst offender)

Per-job breakdown (run `28354478634`, PR):

| Matrix job | Duration |
|---|---|
| Scan orchestrator-webhook | **~13m12s** |
| Scan beads | **~12m24s** |
| Scan orchestrator-service | **~10m10s** |
| Scan orchestrator-caddy | ~1m05s |

**Why it's slow — two compounding problems:**

1. **No Docker cache at all.** The build step is a bare `docker build` with zero cache flags:

   ```yaml
   - name: Build image
     run: docker build -t ${{ matrix.name }}:${{ github.sha }} -f ${{ matrix.dockerfile }} ${{ matrix.context }}
   ```

   Compare to `validate.yml`, which uses `cache-from: type=gha`. Trivy rebuilds **every layer from scratch on every run.**

2. **3 of 4 images compile Rust from source.** `Dockerfile`, `Dockerfile.webhook`, and `Dockerfile.beads` each contain a self-contained `rust-builder` stage that:
   - installs the **nightly** toolchain via rustup (`beads_rust` needs `#![feature]`),
   - runs `cargo install --git ... beads_rust` (compiles all transitive deps),
   - webhook/beads also compile `beads_viewer_rust`.

   That Rust compile alone is ~8–10 min. The caddy job proves it: no Rust stage → **1 minute flat.**

3. **No `concurrency:` group and no path filters** → every push to any branch and every PR trigger a full 4-matrix rebuild, even for doc-only changes. Stacked commits can launch multiple overlapping 13-min runs.

> **Root cause (verified):** uncached `docker build` × Rust-from-source compilation, repeated 3× per run, on every push/PR.

### #2 — `docker-publish.yml` `beads-builder` job: ~11 min (serial bottleneck)

Per-job breakdown (run `28299720201`, push to development):

| Job | Duration | Note |
|---|---|---|
| Build & publish beads builder | **~11m23s** | compiles Rust (br + bvr) |
| Publish orchestrator-service | ~1m04s | runs *after* beads-builder |
| Publish orchestrator-webhook | ~1m06s | runs *after* beads-builder |
| Publish orchestrator-caddy | ~0m32s | runs *after* beads-builder |

**Why:** The `beads-builder` job compiles Rust once (`nightly` + `cargo install` × 2). The 3 runtime jobs are gated behind it (`needs: beads-builder`) and then run in parallel — but they can't start until the ~11 min Rust compile finishes. The `build-contexts` override (pulling the published beads image instead of recompiling) is **already a good optimization** — without it, each runtime job would add another ~10 min of Rust compilation.

> The wall-clock is `beads-builder (11m) + max(runtime jobs ~1m) ≈ 12m`. The Rust compile is the single critical-path item.

### #3 — `validate.yml` `build` job: ~1.5 min (already optimized, but fragile)

| Job | Duration |
|---|---|
| build (4 images, sequential) | ~1m35s |
| test | ~31s |
| lint | ~21s |
| scan | ~6s |

**Why it's fast:** uses `cache-from: type=gha` with per-image scopes, so most runs hit the cache. **But** it builds 4 images **sequentially in one job**, and 3 include the Rust fallback stage. It's fast only as long as the GHA cache survives. The repo now uses **8 cache scopes** (4 in validate + 4 in docker-publish) sharing the **10 GB GHA cache limit** with `mode=max` (all layers) — the large Rust layers make eviction likely, which would spike this to ~10+ min on a cache miss.

### Honorable mentions (small, but wasteful)

- **`validate.yml` lint job** downloads actionlint via `curl | bash` on every run (~10–15s) instead of caching it.
- **`validate.yml`** has **no `concurrency:` group** → rapid pushes launch overlapping runs.
- **`trivy.yml`** uploads SARIF with `continue-on-error: true` (GHAS not enabled) — the scan runs but the upload silently fails every time; the valuable output is only the artifact.

---

## 2. Options for Speeding Up the Slow Operations

### For `trivy.yml` (biggest win: ~13 min → ~2–3 min)

| Option | Effort | Expected gain | Notes |
|---|---|---|---|
| **A. Add GHA cache to the build step** | Low | ~10 min | Switch from `docker build` to `docker/build-push-action` with `cache-from/cache-to: type=gha` (same pattern validate.yml already uses). Cache hits skip the Rust compile entirely. |
| **B. Scan a pre-built image instead of rebuilding** | Medium | ~12 min | Don't build in trivy at all. Have `validate.yml`'s build job (or docker-publish) push to GHCR with a `ci-` tag, then trivy scans `image-ref: ghcr.io/...:ci-<sha>`. Eliminates duplicate builds entirely. |
| **C. Add path filters** | Low | ~13 min (skip entirely) | Only run trivy when `Dockerfile*`, `image/**`, `deploy/caddy/**`, `pyproject.toml`, `uv.lock` change. Doc/test-only pushes skip the whole workflow. |
| **D. Add `concurrency` + `cancel-in-progress`** | Low | saves redundant runs | `group: trivy-${{ github.ref }}`, cancel-in-progress. Stops stacked-commit thrash. |
| **E. Drop the Rust images from trivy matrix; scan published prod images** | Medium | ~10 min | Scan `ghcr.io/.../beads`, `ghcr.io/.../orchestrator-service` etc. directly. Trivy becomes a pure scan, no build. |

**Recommended combo:** C (path filters) + D (concurrency) + B or E (scan pre-built images). This takes trivy from ~13 min to **under 1 min** on most runs.

### For `docker-publish.yml` `beads-builder` (~11 min)

| Option | Effort | Expected gain | Notes |
|---|---|---|---|
| **F. Cache the cargo/git registry** | Low | ~2–4 min | Add `RUN --mount=type=cache,target=/usr/local/cargo/registry` (BuildKit cache mount) so `cargo install` reuses compiled deps. Requires `# syntax=docker/dockerfile:1` header. |
| **G. Skip beads-builder when Rust inputs unchanged** | Medium | ~11 min (skip) | Add a `paths` filter or a `dorny/paths-filter` step: only rebuild beads when `Dockerfile.beads` or the pinned beads rev changes. Reuse the last published `sha-<prev>` image otherwise. |
| **H. Pre-compile beads binaries in a separate job, cache as artifact** | Medium | ~3–5 min | Build `br`/`bvr` once in a dedicated job with cargo cache, export binaries as a workflow artifact, `COPY` into images. Decouples Rust from Docker layer caching. |
| **I. Use a dedicated Rust builder base image** | Medium | ~1–2 min | Publish a `rust-nightly-with-deps` base to GHCR so the `rustup toolchain install nightly` + apt step is pre-baked. |

**Recommended combo:** F (cargo cache mount) is the quickest win. G (skip when unchanged) eliminates the job entirely on most merges.

### For `validate.yml` build job (~1.5 min, harden against cache misses)

| Option | Effort | Expected gain | Notes |
|---|---|---|---|
| **J. Parallelize the 4 image builds into a matrix** | Low | ~1 min | Currently 4 sequential `docker/build-push-action` steps in one job. A matrix runs them concurrently (4 runners). |
| **K. Share the Rust builder across validate + trivy + publish** | Medium | eliminates redundancy | See section 3. |
| **L. Add `concurrency` group** | Low | saves redundant runs | `group: validate-${{ github.ref }}`, cancel-in-progress. |

---

## 3. Overall Improvement Options (Cross-Cutting)

### Highest-impact: build each image once, reuse everywhere

Right now the same images get built **up to 3 times** per push/PR:

| Consumer | Builds images? | Cache? |
|---|---|---|
| `validate.yml` build job | Yes (4 images) | gha cache |
| `trivy.yml` | Yes (4 images) | **none** |
| `docker-publish.yml` | Yes (4 images) | gha cache |

**The single biggest structural fix:** make image-building a **shared, cached, single-source** step, then have all consumers reference the built image.

**Option 1 — Artifact passing (no registry needed):**

```
build-images job (matrix, gha cache, cargo cache mount)
   ↓ docker save | zstd → actions/cache or upload-artifact
validate.build    → skip (or just smoke-test the loaded image)
trivy             → docker load the artifact, scan it
docker-publish    → tag & push the already-built image
```

**Option 2 — GHCR ephemeral tags (cleaner, recommended):**

- One `build` workflow builds all 4 images on every push/PR, pushes to GHCR as `ci-<sha>`, with gha cache + cargo cache mount.
- `trivy` scans `ghcr.io/...:ci-<sha>` (no build step at all).
- `docker-publish` (on merge) re-tags `ci-<sha>` → `branch-latest` / `sha-<sha>` and signs. No rebuild.
- A scheduled job prunes `ci-*` tags older than N days (`gh api` / `actions/delete-package-versions`).

This collapses 3× builds → 1× build. Trivy drops from ~13 min to **<1 min**.

### Caching improvements

| Technique | Where | Benefit |
|---|---|---|
| **Cargo cache mount** (`--mount=type=cache`) | All Rust-builder stages | Reuses compiled crates; biggest single Dockerfile win. Needs `# syntax=docker/dockerfile:1.7`. |
| **`Swatinem/rust-cache` action** | If you extract Rust build to a job | Purpose-built cargo cache for CI. |
| **uv cache** (already enabled via `setup-uv enable-cache`) | lint/test jobs | Already done. |
| **actionlint binary cache** | validate lint job | Replace `curl \| bash` with `awalsh128/cache-apt-pkgs-action` or a `actions/cache` on `~/actionlint`. Saves ~10s/run. |
| **Consolidate GHA cache scopes** | all | 8 scopes risk eviction. Either reduce (shared scope with key suffix) or move large layers (Rust) to cargo cache mounts so they don't bloat the gha cache. |

### Concurrency & trigger hygiene

| Technique | Workflows missing it | Benefit |
|---|---|---|
| `concurrency: {group, cancel-in-progress: true}` | **validate**, **trivy** | Cancels superseded runs on rapid pushes. droid-review already has it. |
| Path filters | **trivy**, **validate** (partial) | Skip entirely on doc/test-only changes. docker-publish already has them. |
| Run trivy on a schedule (e.g. nightly) instead of every PR | trivy | Vulnerability scanning doesn't need to block every commit; nightly + on-Dockerfile-change is sufficient for most teams. |

### Dockerfile-level optimizations

1. **Pin `# syntax=docker/dockerfile:1.7`** and add `--mount=type=cache` to `cargo install`, `apt-get`, and `uv sync` steps. This is the highest-ROI Dockerfile change — keeps heavy compilation out of committed layers and out of the GHA cache.
2. **Merge the 3 identical `rust-builder` stages.** `Dockerfile`, `Dockerfile.webhook`, and `Dockerfile.beads` each duplicate the same `rustup nightly` + `cargo install beads_rust` block. The `build-contexts` override in docker-publish already centralizes this for prod; extend the same pattern to validate and trivy so the fallback stage is never exercised in CI.
3. **Combine `apt-get update`/`install` `RUN` layers** in `Dockerfile` (lines 26, 48, 60 each call `apt-get update` separately) — fewer layers, smaller cache footprint.

---

## Summary: Ranked by Impact

| Rank | Change | Effort | Time saved per run |
|---|---|---|---|
| 1 | **trivy: scan pre-built images instead of rebuilding** (no-cache `docker build` is the root cause) | Medium | **~12 min** |
| 2 | **trivy: add path filters + concurrency/cancel** | Low | **~13 min** (skip entirely on most pushes) |
| 3 | **Cargo `--mount=type=cache`** in all Rust-builder stages | Low | **~3–5 min** per Rust build |
| 4 | **Single shared build → reuse across validate/trivy/publish** | Medium | eliminates 2× duplicate builds |
| 5 | **beads-builder: skip when Rust inputs unchanged** | Medium | **~11 min** (skip on most merges) |
| 6 | **validate: concurrency group + parallelize build matrix** | Low | ~1 min + fewer redundant runs |
| 7 | **actionlint cache** | Low | ~10 s |

The **#1 issue is unambiguous and verified**: `trivy.yml` runs a cacheless `docker build` that recompiles Rust from source 3× on every push and PR. Fixing that alone (options 1+2 above) removes the single largest source of CI time in this repo.
