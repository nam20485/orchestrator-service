# CI Workflow Optimization — Build-Once-Reuse Spec

> **Status:** DRAFT — awaiting approval before implementation.
> **Date:** 2026-06-29
> **Author:** Analysis derived from `plan_docs/ci-workflow-performance-analysis.md` (evidence-based, measured timings).
> **Approval required before any workflow or Dockerfile changes.**

---

## 1. Overview

### Problem statement

Three workflows build the same four Docker images, with the expensive Rust compilation (`cargo install` of `beads_rust` + `beads_viewer_rust` on the nightly toolchain, ~8–10 min) repeated redundantly:

| Consumer | Builds images? | Rust compiled? | Docker cache? | Trigger |
|---|---|---|---|---|
| `validate.yml` (build job) | Yes (4) | Yes (fallback stage) | `type=gha` | every push + PR |
| `trivy.yml` | Yes (4) | Yes (fallback stage) | **none** | every push + PR |
| `docker-publish.yml` | Yes (4) | Once (beads-builder), reused via `build-contexts` | `type=gha` | merge to main/dev + tags |

Measured impact (last 10 runs):
- **trivy: ~13–14 min on every push and PR** — the slowest workflow, root cause is a cacheless `docker build` that recompiles Rust 3× per run.
- **docker-publish: ~12 min** — dominated by the serial `beads-builder` job (~11 min).
- **validate: ~1.5–2.5 min** — fast today but fragile (depends on GHA cache survival across 8 scopes sharing a 10 GB limit).

### Goal

Eliminate redundant Rust compilation across workflows by reusing the **already-published** beads builder image, add the caching/concurrency hygiene that trivy and validate are missing, and do so **without new infrastructure** and **without blowing the private-repo GitHub Packages storage quota**.

### Non-goals

- Changing what trivy scans (still CRITICAL,HIGH severity on the same images).
- Changing the publish/sign pipeline semantics in docker-publish (cosign, GHCR tags).
- Multi-platform builds (stays `linux/amd64`).
- Replacing GHA runners with self-hosted runners.
- Phase 3 (full workflow consolidation) is **optional and deferred** — see §6.

---

## 2. Current state (baseline)

### Image inventory

| Image | Dockerfile | Has Rust stage? | Published to GHCR? |
|---|---|---|---|
| orchestrator-service | `Dockerfile` | Yes (`br` only) | yes (`ghcr.io/<repo>`) |
| orchestrator-webhook | `Dockerfile.webhook` | Yes (`br` + `bvr`) | yes (`ghcr.io/<repo>/webhook`) |
| orchestrator-caddy | `deploy/caddy/Dockerfile` | No | yes (`ghcr.io/<repo>/caddy`) |
| beads (builder) | `Dockerfile.beads` | Yes (`br` + `bvr`) | yes (`ghcr.io/<repo>/beads`) |

### Existing reuse pattern (the foundation)

`docker-publish.yml` already compiles Rust once and reuses it:
- `beads-builder` job builds `Dockerfile.beads`, pushes `ghcr.io/<repo>/beads:sha-<sha>` + `:development-latest` / `:main-latest`.
- Runtime jobs override the `rust-builder` stage via:
  ```yaml
  build-contexts: |
    rust-builder=docker-image://ghcr.io/${{ env.BEADS_IMAGE }}:sha-${{ github.sha }}
  ```
- This is why runtime jobs take ~1 min instead of ~10 min.

**The gap:** `validate.yml` and `trivy.yml` do not use this override — they fall back to the self-contained `rust-builder` stage and recompile Rust every run.

### Baseline timings (measured)

| Workflow / job | Baseline | Source run |
|---|---|---|
| trivy (whole workflow) | ~13–14 min | `28354478634` |
| trivy Scan orchestrator-webhook | ~13m12s | `28354478634` |
| trivy Scan orchestrator-caddy (no Rust) | ~1m05s | `28354478634` |
| docker-publish beads-builder | ~11m23s | `28299720201` |
| validate build job | ~1m35s | `28354478630` |

---

## 3. Target architecture

```
                         docker-publish.yml (on merge to main/dev)
                                  │
                          beads-builder job
                          (compiles Rust ONCE)
                                  │
                                  ▼
                   ghcr.io/<repo>/beads:{sha-<sha>, <branch>-latest}
                                  │
            ┌─────────────────────┼──────────────────────┐
            ▼                      ▼                      ▼
     validate.yml              trivy.yml           docker-publish runtime jobs
   build-contexts:           build-contexts:      build-contexts:
   rust-builder=             rust-builder=        rust-builder=
   ...beads:<branch>-latest  ...beads:<branch>-latest   ...beads:sha-<sha>
   (no Rust compile)         (no Rust compile)    (no Rust compile — already done)
```

Rust is compiled **exactly once** (in docker-publish, on merge). All other workflows pull the published `br`/`bvr` binaries via `build-contexts` and never invoke `cargo install`.

---

## 4. Requirements

### Functional requirements

| ID | Requirement |
|---|---|
| FR-1 | trivy must not compile Rust (`cargo install` must not appear in trivy job logs) after Phase 2. |
| FR-2 | validate must not compile Rust on a cache miss after Phase 2 (must pull published beads builder). |
| FR-3 | trivy must use `docker/build-push-action` with `cache-from/to: type=gha` (no bare `docker build`). |
| FR-4 | trivy and validate must declare a `concurrency` group with `cancel-in-progress: true`. |
| FR-5 | trivy and validate must use path filters to skip when no image-relevant files changed. |
| FR-6 | All three Rust-bearing Dockerfiles must use BuildKit cache mounts (`--mount=type=cache`) for `cargo install`, `apt-get`, and `uv sync`. |
| FR-7 | The published beads builder image must remain the single source of truth for `br`/`bvr` versions (no version drift). |
| FR-8 | If a PR modifies `Dockerfile.beads` (bumps the beads rev), the consumers must fall back to the self-contained `rust-builder` stage for that PR so the change is actually tested. |
| FR-9 | trivy must continue to produce SARIF artifacts and (best-effort) upload to the Security tab. |
| FR-10 | docker-publish publish + cosign signing behavior must be unchanged. |

### Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | No new paid infrastructure. No new GHCR storage beyond what docker-publish already publishes. |
| NFR-2 | No increase in GitHub Actions minutes for the common case (doc/test-only pushes must cost less, not more). |
| NFR-3 | All changes must pass `pwsh -NoProfile -File ./scripts/validate.ps1 -All` locally and in CI. |
| NFR-4 | No real secrets, API keys, or tokens introduced into committed files. |
| NFR-5 | Pinned action SHAs maintained (no floating `@v5`/`@main` except where already present and out of scope). |

---

## 5. Phased implementation plan

### Phase 1 — Caching, concurrency, and path-filter hygiene (no architecture change)

**Objective:** Add the missing cache to trivy, stop redundant runs, and add BuildKit cargo cache mounts. No change to *what* gets built — only *how fast*.

**Target:** trivy ~13 min → ~2–3 min.

| Task | File | Change |
|---|---|---|
| 1.1 | `.github/workflows/trivy.yml` | Add `concurrency: { group: trivy-${{ github.ref }}, cancel-in-progress: true }`. |
| 1.2 | `.github/workflows/trivy.yml` | Add `paths` filter on `push`/`pull_request` triggers: `Dockerfile`, `Dockerfile.webhook`, `Dockerfile.beads`, `deploy/caddy/**`, `image/**`, `scripts/docker-entrypoint.sh`, `scripts/prompt.ps1`, `webhook_receiver/**`, `pyproject.toml`, `uv.lock`, `.github/workflows/trivy.yml`. |
| 1.3 | `.github/workflows/trivy.yml` | Replace the bare `docker build` step with `docker/setup-buildx-action` + `docker/build-push-action` (`load: true`, `push: false`, `cache-from: type=gha,scope=trivy-<name>`, `cache-to: type=gha,mode=max,scope=trivy-<name>`). |
| 1.4 | `.github/workflows/validate.yml` | Add `concurrency: { group: validate-${{ github.ref }}, cancel-in-progress: true }`. |
| 1.5 | `Dockerfile`, `Dockerfile.webhook`, `Dockerfile.beads` | Add `# syntax=docker/dockerfile:1.7` header. Add `--mount=type=cache,target=/var/cache/apt,sharing=locked` to `apt-get` steps and `--mount=type=cache,target=/usr/local/cargo/registry` to `cargo install` steps. |
| 1.6 | `Dockerfile.webhook` | Add `--mount=type=cache,target=/root/.cache/uv` to the `uv sync` step. |

**Phase 1 acceptance criteria:**
- AC-1.1: A doc-only push (e.g. `README.md` change) does not trigger trivy or validate (verified via `gh run list`).
- AC-1.2: Two rapid pushes to the same branch result in the first trivy/validate run being cancelled (status `cancelled`).
- AC-1.3: trivy median wall-clock < 4 min over 5 consecutive runs (measured via `gh run list --workflow=trivy.yml`).
- AC-1.4: `cargo install` cache mount is effective — second run shows cache hits (BuildKit "CACHED" on cargo layers when inputs unchanged).
- AC-1.5: `pwsh -NoProfile -File ./scripts/validate.ps1 -All` passes locally.

---

### Phase 2 — Reuse the published beads builder everywhere (the structural fix)

**Objective:** Extend docker-publish's `build-contexts` override to trivy and validate so Rust is never compiled outside docker-publish.

**Target:** trivy ~2–3 min → ~1–2 min; validate robust against GHA cache eviction.

| Task | File | Change |
|---|---|---|
| 2.1 | `.github/workflows/trivy.yml` | Add `build-contexts: rust-builder=docker-image://ghcr.io/${{ github.repository }}/beads:${{ env.BEADS_TAG }}` to the build step for the 3 Rust-bearing images. Define `BEADS_TAG` (see 2.3). |
| 2.2 | `.github/workflows/validate.yml` | Same `build-contexts` override on the orchestratorservice, webhook, and beads build steps. |
| 2.3 | Both workflows | Compute `BEADS_TAG`: for push to main/dev use `sha-${{ github.sha }}` if available, else fall back to `<branch>-latest`. For PRs use `<base_branch>-latest` (the last published stable builder). |
| 2.4 | Both workflows | Add a `dorny/paths-filter` (or `git diff`) step: if `Dockerfile.beads` changed in this commit/PR, set `USE_FALLBACK_BUILDER=true` and omit the `build-contexts` override (FR-8) so the self-contained stage tests the new rev. |
| 2.5 | Both workflows | Add GHCR login (`docker/login-action`) so the builder pull is authenticated (private repo). Use `GITHUB_TOKEN` (read-only `packages: read`). |
| 2.6 | `Dockerfile`, `Dockerfile.webhook` | Add a comment documenting that the `rust-builder` stage is a fallback only exercised when `build-contexts` is absent (PRs touching `Dockerfile.beads`, or local `docker build`). |

**Phase 2 acceptance criteria:**
- AC-2.1: trivy job logs for orchestrator-service/webhook/beads contain **no** `cargo install` line (Rust not compiled). Verified via `gh run view <id> --log | grep "cargo install"` returns nothing.
- AC-2.2: trivy median wall-clock < 2 min over 5 consecutive runs.
- AC-2.3: validate build job < 2 min even when the GHA cache is cold (simulate by changing cache scope key once).
- AC-2.4: A PR that modifies `Dockerfile.beads` (bumps beads rev) falls back to the self-contained stage and the new rev is compiled and tested (FR-8). Verified: `cargo install` appears in logs for that PR only.
- AC-2.5: `br --version` inside the built images reports the same version as the published beads builder (no drift). Verified via a smoke-test step or `test/test-beads-versions-consistency.sh`.
- AC-2.6: `pwsh -NoProfile -File ./scripts/validate.ps1 -All` passes locally.

---

### Phase 3 — Optional: consolidate validate + trivy into a single CI workflow (deferred)

**Objective:** Eliminate the last residual duplication (validate and trivy each rebuild the non-Rust layers for the same commit) by merging them into one `ci.yml` with a single `build` matrix consumed by `test`/`lint`/`trivy` jobs.

**Status:** DEFERRED. Only pursue if, after Phases 1+2 are in production for ≥ 2 weeks, measured data shows the residual ~30s duplication materially impacts developer feedback time.

**Deferred because:**
- Changes required-status-check names (breaks branch protection config until updated).
- Changes the GitHub Checks UI and failure-isolation ergonomics.
- Saving is ~30s/run after Phases 1+2 — poor complexity/ROI.

**If pursued, the approach:**
| Task | Change |
|---|---|
| 3.1 | Create `.github/workflows/ci.yml` with a `build` matrix job (4 images, gha cache, beads-builder reuse from Phase 2). |
| 3.2 | Add `lint`, `test`, `scan`, `trivy` jobs with `needs: build`, each loading the relevant image (via `actions/download-artifact` of a `docker save` tarball, or pulling a `ci-<sha>` GHCR tag). |
| 3.3 | Delete `validate.yml` and `trivy.yml`; update branch protection required checks. |
| 3.4 | Add a scheduled (`cron`) tag-pruning job if using `ci-<sha>` GHCR tags. |

---

## 6. Validation plan

### Per-phase CI measurement (mandatory, evidence-based)

After each phase merges, run these commands and record output in the PR description:

```bash
# 1. Confirm workflows triggered/skipped correctly
gh run list --workflow=trivy.yml --limit 5
gh run list --workflow=validate.yml --limit 5

# 2. Measure wall-clock and per-job timing (5-run median)
gh run view <run-id> --json jobs | jq -r '.jobs[] | "\(.name)\t\(.startedAt) -> \(.completedAt)"'

# 3. Confirm no Rust compilation in trivy/validate (Phase 2)
gh run view <trivy-run-id> --log | grep -c "cargo install"   # expect 0

# 4. Confirm beads version consistency
pwsh -NoProfile -File ./test/test-beads-versions-consistency.sh
```

### Local validation (before every commit)

```bash
pwsh -NoProfile -File ./scripts/validate.ps1 -All
```

Must pass clean (lint + scan + test). Secret scan must be clean (no real keys).

### Image correctness smoke test

After Phase 2, verify the built images still contain working `br`/`bvr` from the published builder:
```bash
docker build -t test-svc -f Dockerfile . \
  --build-context rust-builder=docker-image://ghcr.io/<repo>/beads:development-latest
docker run --rm test-svc br --version
docker run --rm test-svc bvr --version   # webhook image only
```

### Regression gates

| Gate | Command | Must be |
|---|---|---|
| Local validate | `./scripts/validate.ps1 -All` | pass |
| Secret scan | `./scripts/validate.ps1 -Scan` | clean |
| Beads version consistency | `./test/test-beads-versions-consistency.sh` | pass |
| Compose config | `./test/test-compose-config.sh` | pass |
| CI validate workflow | `gh run list --workflow=validate.yml --limit 1` | `success` |
| CI trivy workflow | `gh run list --workflow=trivy.yml --limit 1` | `success` |
| CI docker-publish | `gh run list --workflow=docker-publish.yml --limit 1` | `success` |

---

## 7. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Published beads builder is stale on a PR that bumps the beads rev | Medium | High (tests wrong binary) | FR-8 / task 2.4: `paths-filter` falls back to self-contained stage when `Dockerfile.beads` changes. |
| GHA cache eviction (10 GB / 8 scopes) causes cold builds | Medium | Medium | Phase 1 cargo cache mounts shrink Rust layers in cache; Phase 2 removes dependency on cache for Rust entirely (pulls published builder). |
| GHCR pull fails (private repo auth, rate limit) | Low | High (build fails) | Task 2.5: authenticated `docker/login-action` with `GITHUB_TOKEN`. The `rust-builder` fallback stage remains as a safety net. |
| BuildKit cache mount (`--mount=type=cache`) not supported | Low | Low | Requires BuildKit (default on GHA runners with buildx). Add `# syntax=docker/dockerfile:1.7` header to enable. |
| Path filters too aggressive — skip a needed scan | Low | Medium | Include `.github/workflows/trivy.yml` in the filter list so workflow changes always trigger. Monitor for missed scans. |
| Phase 2 `build-contexts` override breaks caddy (no rust-builder stage) | Low | Low | docker-publish already proves this is harmless — unused `build-contexts` entries are ignored for stages that don't exist. |
| Concurrency cancel-in-progress cancels a run mid-scan | Low | Low | Only cancels superseded runs on the same ref; the latest commit's run always completes. |

---

## 8. Rollback plan

Each phase is independently revertible:

| Phase | Rollback action |
|---|---|
| Phase 1 | `git revert` the merge commit. No data/state dependencies — workflows return to baseline behavior immediately. |
| Phase 2 | Remove the `build-contexts` override and `BEADS_TAG` logic. The self-contained `rust-builder` fallback stage remains in all Dockerfiles and takes over (slower, but correct). No image republish needed. |
| Phase 3 | Restore `validate.yml` and `trivy.yml` from git history; delete `ci.yml`; restore branch protection required checks. |

The `rust-builder` fallback stage in `Dockerfile`, `Dockerfile.webhook`, and `Dockerfile.beads` is **never removed** — it is the safety net that makes Phase 2 zero-risk to roll back.

---

## 9. Out of scope

- Self-hosted runners / larger runner sizes.
- Multi-platform (`linux/arm64`) builds.
- Changing trivy severity thresholds or scan targets.
- Replacing GHA cache with an external cache backend (BuildKit remote, S3).
- Optimizing the `beads-builder` job itself beyond cargo cache mounts (e.g. splitting `br` and `bvr` into parallel jobs) — tracked separately if beads-builder remains a bottleneck after Phase 1.
- The `droid`, `droid-review`, `opencode`, and `dependency-review` workflows (event-driven, not duplication sources).

---

## 10. Expected outcome

| Metric | Baseline | After Phase 1 | After Phase 2 |
|---|---|---|---|
| trivy wall-clock (median) | ~13 min | ~2–3 min | ~1–2 min |
| trivy Rust compilations per run | 3 | 3 (cached) | **0** |
| validate build job (cache miss) | ~10 min | ~3–4 min | ~2 min |
| Duplicate image builds per push/PR | 2 (validate + trivy) | 2 (cached) | 2 (no Rust) |
| New infrastructure | — | none | none |

**Net:** ~11 min saved per push/PR (trivy alone), zero new infrastructure, reuses a pattern the repo already runs in production.
