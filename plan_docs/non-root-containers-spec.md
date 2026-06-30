# Non-Root Container Execution

## Status
**Proposed — not yet implemented.** This is a planning/specification document only.

## Problem

All three service containers (`orchestratorservice`, `webhook-receiver`, `webhook-proxy`) run as
**`root` (UID 0)** inside the container. None of the Dockerfiles declare a `USER` directive, and
there is no `gosu`/`su-exec`/`runuser` privilege drop. Two concrete consequences:

1. **Host filesystem pollution.** The workspace is a host bind mount
   (`${WORKSPACE_DIR}:/workspace`). Every file a container creates — agent session code,
   `.beads/beads.db`, git worktrees, `.git/` objects, venvs — lands on the host owned by `root`.
   Operators cannot delete or modify this state without `sudo`. This is the immediate trigger:
   `<workspace-dir>/{backend,frontend,.git,.beads}` are all `root:root`
   (`drwxr-xr-x`), requiring `sudo find … -delete` to clean up a stale project.

2. **Security posture.** A process inside the container running as UID 0 has full root privileges
   over the bind-mounted host directory and any named volumes. A compromised or misbehaving agent
   session can `chown`/`rm`/write anywhere in the workspace tree with no UID boundary. Running as a
   non-root UID matching the host user is standard container hardening and eliminates the
   privilege boundary on the bind mount.

### Root cause (current state)

| Location | Root assumption |
|---|---|
| `Dockerfile` (orchestratorservice) | No `USER`. Installers write to `/root/.local/bin` (uv), `/root/.opencode/bin` (opencode), `/root/.config/opencode` (config). `ENV PATH="/root/.opencode/bin:${PATH}"` (dead code — binary already in `/usr/local/bin`). `RUN mkdir -p /workspace && chmod 755 /workspace`. Config-copy step targets `/root/.config/opencode`. `scripts/git-trust.sh` writes `safe.directory` to `/root/.gitconfig`. |
| `Dockerfile.webhook` | No `USER`. Same `/root/.local/bin` (uv), `/root/.opencode/bin` (opencode), `PATH` (same dead-code entry). `uv sync` creates `/app/.venv` as root. `--mount=type=cache,target=/root/.cache/uv` cache mount at root path. `scripts/git-trust.sh` writes to `/root/.gitconfig`. |
| `deploy/caddy/Dockerfile` | Uses upstream `caddy:2.10.0-alpine` which runs as root. Caddyfile binds `:80` (privileged port < 1024). |
| `scripts/docker-entrypoint.sh` | Already uses `$HOME` for `auth.json` (`${HOME:-/root}`) — no logic change needed, but `HOME` must resolve to the non-root home at runtime. |
| `compose*.yaml` | No `user:` directive on any service. |
| `webhook_receiver/` | No `/root`/`HOME`/`getuid` references in Python. Git operations (`workspace.py`) run as the container UID with no `safe.directory` config — will emit "dubious ownership" warnings as non-root. |

## Goals

1. All three containers run as a non-root user.
2. The non-root UID/GID is **configurable** and defaults to `1000:1000` to match the typical host
   user, so bind-mount files are owned by the operator — no `sudo` for cleanup.
3. No loss of function: opencode serve, beads loop, webhook dispatch, git push/PR, Caddy proxy/TLS
   all work as before.
4. Build remains reproducible and passes `validate.yml` (including the Docker build job).

## Non-Goals

- Switching base images (stays `debian:trixie-20260518-slim` + `caddy:2.10.0-alpine`).
- Rootless Docker / user-namespace remapping (host-level concern, out of scope).
- Changing the workspace multi-project model (covered by
  `plan_docs/multi-project-workspace-isolation.md`).
- Multi-arch support (image remains amd64-only).
- `Dockerfile.beads` changes — build-only image consumed via `COPY --from`; never runs as a service, so non-root is unnecessary.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Where the user is defined | **`app` user created in image** via build `ARG` (default UID/GID `1000:1000`); privilege drop via `gosu` in entrypoint (no `USER` directive) | Entrypoint starts as root for volume fixup, then `exec gosu app` drops privileges. Compose `user:` override remains available for host-UID mismatch. |
| User name | `app` (home `/home/app`) | Conventional, short, unambiguous. |
| UID/GID configurability | Build args `APP_UID` / `APP_GID` (default `1000`); compose can override at runtime via `user:` | Build-time default matches common host user; runtime override handles hosts where the operator UID ≠ 1000. |
| Install paths | Repath all `/root/...` → `/home/app/...` (uv, opencode, config dirs) | Installers (`curl \| sh`) honor `$HOME`; `RUN` steps use `HOME=/home/app` prefix so installers write to `/home/app`. All steps run as root; `chown -R app:app /home/app` after installs. |
| `/app` ownership | `chown -R app:app /app` after `COPY`/`uv sync` | Runtime user must read config and (for webhook) write `.venv`/caches. |
| `/workspace` | No image change; works automatically when container UID == host UID | Bind mount is host-owned; matching UID gives seamless read/write with no `chown`. |
| `opencode-memory` named volume | Entrypoint `chown -R app:app /app/.memory` if owned by root (first-mount fixup) | Named volumes are root-owned on first attach; one-time recursive chown guarded by ownership check keeps it idempotent and cheap. |
| Git "dubious ownership" | `git config --global --add safe.directory '*'` for `app` user (webhook image) | `workspace.py` runs git on the bind-mounted `/workspace` whose owner (host UID) differs from historical expectations; `safe.directory` silences the warning without weakening security meaningfully for this trusted internal service. |
| Git author identity | Set `user.name`/`user.email` defaults for `app` (e.g. `orchestrator-bot`) unless overridden by env | Commits/`br` need an identity; provide a sane default, allow env override. |
| Caddy privileged port (`:80`) | **`cap_add: [CAP_NET_BIND_SERVICE]`** in compose (keep `:80`) | Minimal change; preserves the documented `:80`/`:443` model and ACME behavior. Alternative (remap to `:8080` internal) is a larger behavioral change and rejected. |
| Caddy data/config volumes | Entrypoint or image `chown` of `/data` and `/config` to `app` | Caddy must persist ACME state; volumes are root-owned on first mount. |
| Caddy user | Bake `USER caddy` into `deploy/caddy/Dockerfile` (extend upstream) | The upstream `caddy:2.10.0-alpine` image already ships a non-root `caddy` user and the binary is built to use it; reusing it avoids recreating ownership for `/data`/`/config`. The other two images still use `USER app`. |
| Backward compatibility | Existing root-owned workspace files remain deletable only via `sudo` (one-time cleanup); new files are operator-owned | Cannot retroactively reown host files from inside a non-root container. Document the one-time `sudo chown -R $UID:$GID $WORKSPACE_DIR` migration. |

## Target State

### User & paths (orchestratorservice + webhook images)

```
Container starts as root; entrypoint drops to app via gosu (no USER in Dockerfile)
HOME=/home/app
PATH — no custom entries needed (all binaries in /usr/local/bin)

/home/app/
  .local/bin/uv, uvx          (was /root/.local/bin; not on PATH — /usr/local/bin copy is authoritative)
  .opencode/bin/opencode      (was /root/.opencode/bin; not on PATH — /usr/local/bin copy is authoritative)
  .config/opencode/           (was /root/.config/opencode — global config tree)
  .local/share/opencode/auth.json   (written by entrypoint at runtime)
  .gitconfig                  (safe.directory + identity, webhook image only)

/app/                         (chown -R app:app)
  image config (orchestratorservice)
  .venv/                      (webhook; uv sync as app)
  .memory/                    (orchestratorservice; named volume, entrypoint chown)

/workspace/                   (bind mount; no chown — UID match handles ownership)
```

### Compose

```yaml
# Added to each service (or rely on baked USER; compose override for host mismatch):
user: "${APP_UID:-1000}:${APP_GID:-1000}"

# webhook-proxy only:
cap_add:
  - CAP_NET_BIND_SERVICE
```

## Task Breakdown

### Phase 1 — `orchestratorservice` image (`Dockerfile`)

1. Add build args near the top of the final stage:
   ```dockerfile
   ARG APP_UID=1000
   ARG APP_GID=1000
   ```
2. Create the group and user **before** any install step that writes to `$HOME`:
   ```dockerfile
   RUN groupadd -g "${APP_GID}" app \
    && useradd -l -u "${APP_UID}" -g "${APP_GID}" -m -d /home/app app
   ```
3. Set `ENV HOME=/home/app`. **Remove** the existing `ENV PATH="/root/.opencode/bin:${PATH}"`
   line entirely — it is dead code (the `opencode` binary is already copied to
   `/usr/local/bin/opencode`, which is on the default PATH). No replacement PATH entry needed.
4. All `RUN` steps execute as root (no `USER app` in the Dockerfile — the gosu entrypoint
   handles privilege drop at runtime; see step 8). Set `HOME=/home/app` for installer
   `RUN` commands so `curl | sh` scripts write to `/home/app` instead of `/root`:
   ```dockerfile
   RUN HOME=/home/app curl -LsSf https://astral.sh/uv/0.10.9/install.sh | sh
   ```
   The existing Dockerfile **already** copies `uv`/`uvx` to `/usr/local/bin` (lines 86-88) —
   keep this pattern, just update the source path from `/root/.local/bin/` to
   `/home/app/.local/bin/`. Same for opencode. After all installs:
   ```dockerfile
   RUN chown -R app:app /home/app
   ```
5. Replace the config-copy target:
   ```dockerfile
   RUN rm -rf /home/app/.config/opencode \
    && mkdir -p /home/app/.config/opencode \
    && cp -r /app/.opencode/. /home/app/.config/opencode/ \
    && rm -rf /app/.opencode \
    && chown -R app:app /home/app/.config/opencode /app
   ```
6. `/workspace`: keep `mkdir -p /workspace && chmod 755 /workspace` (root creates; runtime user
   writes via UID match on the bind mount).
7. **Do not** declare `USER app` in the Dockerfile. The container starts as root; the
   entrypoint drops privileges via `gosu` (see step 8). This is the single entrypoint
   model — there is no alternative.
8. **Entrypoint privilege drop (decided: gosu model).** Install `gosu` in the image
   (`apt-get install -y gosu`). The entrypoint (`scripts/docker-entrypoint.sh`) starts as
   root, performs the first-mount memory-volume `chown`, then drops to `app` via `gosu`
   before `exec`-ing the server:
   ```sh
   MEM_DIR="/app/.memory"
   if [ -d "$MEM_DIR" ] && [ "$(stat -c %u "$MEM_DIR")" = "0" ]; then
     chown -R app:app "$MEM_DIR" 2>/dev/null || true
   fi
   exec gosu app "$@"
   ```
   The Dockerfile has **no** `USER` directive — `gosu` is the sole privilege-drop mechanism.
   The ownership check makes the `chown` a no-op on subsequent starts (idempotent).

### Phase 2 — `webhook-receiver` image (`Dockerfile.webhook`)

1. Same `APP_UID`/`APP_GID` args + `groupadd`/`useradd` + `ENV HOME=/home/app`. **Remove** the
   dead `ENV PATH="/root/.opencode/bin:${PATH}"` line (same as orchestratorservice — binary is
   in `/usr/local/bin`).
2. Repath uv (`/home/app/.local`) and opencode (`/home/app/.opencode`) installers using
   `HOME=/home/app` prefix on `RUN` commands (same pattern as Phase 1 step 4); copy binaries
   to `/usr/local/bin`.
3. Update the `uv sync` cache mount target from `/root/.cache/uv` to `/home/app/.cache/uv`:
   ```dockerfile
   RUN --mount=type=cache,target=/home/app/.cache/uv \
       HOME=/home/app uv sync --frozen --no-dev
   ```
   Then `chown -R app:app /app` so `.venv` is writable at runtime.
4. Git configuration: `scripts/git-trust.sh` already writes `safe.directory` to
   `$HOME/.gitconfig` (which is `/home/app/.gitconfig` after `ENV HOME=/home/app`). Add git
   identity defaults (runs as root with `HOME=/home/app`):
   ```dockerfile
   RUN HOME=/home/app git config --global user.name "orchestrator-bot" \
    && HOME=/home/app git config --global user.email "bot@orchestrator.local" \
    && chown app:app /home/app/.gitconfig
   ```
   Bake **fixed** identity defaults (no `${VAR}` expansion here — that would freeze the build-time
   value into `~/.gitconfig`, not honor runtime overrides). For per-run author identity, set the
   standard git env vars (`GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`,
   `GIT_COMMITTER_EMAIL`) in the runtime environment (compose) or in a startup script that writes
   `~/.gitconfig` from the current env; git honors these over the baked config at commit time.
   (`safe.directory '*'` covers the bind-mounted `/workspace`.)
5. `WORKDIR /app` + `chown -R app:app /app`.
6. **Do not** declare `USER app` — same gosu entrypoint model as orchestratorservice.
   (The webhook-receiver entrypoint is simpler — no volume fixup needed — but uses the same
   `exec gosu app "$@"` pattern for consistency.)

### Phase 3 — `webhook-proxy` image (`deploy/caddy/Dockerfile`)

1. Extend upstream caddy:
   ```dockerfile
   FROM caddy:2.10.0-alpine@sha256:ae4458638da8e1a91aafffb231c5f8778e964bca650c8a8cb23a7e8ac557aa3c
   COPY Caddyfile /etc/caddy/Caddyfile
   # caddy image already provides a non-root-capable entrypoint; declare USER and
   # rely on CAP_NET_BIND_SERVICE (compose) for :80/:443.
   USER caddy
   ```
   Upstream `caddy:2.10.0-alpine` ships a `caddy` user at UID 1000 with `/data` and `/config`
   owned by `caddy:caddy` — no additional chown or wrapper needed.
2. *(Removed — verified: upstream caddy user can write `/data`/`/config` out of the box.)*

### Phase 4 — Compose

1. Add to **all three** services in `compose.yaml` and `compose.development.yaml`:
   ```yaml
   user: "${APP_UID:-1000}:${APP_GID:-1000}"
   ```
2. Add to `webhook-proxy` only:
   ```yaml
   cap_add:
     - CAP_NET_BIND_SERVICE
   ```
3. No change to volume declarations; ownership handled by image/entrypoint.
4. `compose.build.yaml` — **no changes needed** (build overlay only adds `build:` context and
   `pull_policy: never`; inherits `user:`/`cap_add:` from base service definitions).
5. `compose.https.yaml` — **no changes needed** (port overlay only adds `443:443`; inherits
   `cap_add:` from base `webhook-proxy` service definition).

### Phase 5 — Tests

1. **New** `test/test-docker-user.sh` (bash, alongside existing `test/test-*.sh`):
   - Build image; `docker run --rm <image> id -u` → exits with UID `1000` (or `APP_UID`).
   - `docker run --rm <image> sh -c 'touch /workspace/x && stat -c %u /workspace/x'` with a bind
     mount → file UID equals the run UID (proves no forced root).
   - `docker run --rm <image> sh -c 'opencode --version && br --version'` → binaries on PATH.
2. **New** pytest: assert `git config --global --get safe.directory` returns `*` in webhook image
   (or assert via a container exec in CI).
3. **New** test: `auth.json` write path — verify entrypoint writes
   `/home/app/.local/share/opencode/auth.json` (not `/root/...`) and the file is readable by
   `opencode serve` running as `app`.
4. **New** test: named volume first-mount fixup — simulate a fresh `opencode-memory` volume
   (root-owned empty dir), start the container, verify `/app/.memory` is `chown`-ed to `app:app`
   by the entrypoint.
5. Update `test/test-compose-config.sh` if `user:`/`cap_add` affect the config assertion.
6. Update `scripts/validate.ps1` test list if a new script is added.

### Phase 6 — Documentation

1. **README.md** — add "Non-root execution" note: default UID 1000, how to override
   (`APP_UID`/`APP_GID`), the one-time host migration (`sudo chown -R $UID:$GID $WORKSPACE_DIR`
   for pre-existing root-owned files), and the Caddy `CAP_NET_BIND_SERVICE` requirement.
2. **docs/deployment-compose.md** — document `APP_UID`/`APP_GID` and the cap requirement.
3. **AGENTS.md** — update "Learned Workspace Facts": containers run as non-root `app` (UID 1000)
   via gosu entrypoint; workspace files are operator-owned; note the one-time migration. Also
   **remove** the stale claim that `docker-entrypoint.sh` exports `OPENCODE_CONFIG` and
   `OPENCODE_CONFIG_DIR` — the entrypoint does not set these variables; `opencode serve`
   auto-loads config from `~/.config/opencode` (the global config dir).
4. **GEMINI.md** — mirror the AGENTS.md workspace-fact update.

## Edge Cases & Risks

| Edge case | Handling |
|---|---|
| Host operator UID ≠ 1000 | Override at runtime: `APP_UID=$(id -u) APP_GID=$(id -g)` in shell/env, or rebuild with build args. Documented. |
| Pre-existing root-owned workspace files | One-time host migration: `sudo chown -R $(id -u):$(id -g) $WORKSPACE_DIR`. Cannot be done from inside a non-root container. Documented in README. |
| Named volume first-mount root ownership (`opencode-memory`, caddy data/config) | Entrypoint `chown` guarded by ownership check (idempotent). |
| Caddy cannot bind `:80` as non-root | `cap_add: CAP_NET_BIND_SERVICE` in compose. Fallback documented: bind `:8080` internal + remap. |
| opencode config not found under new HOME | Config-copy step retargeted to `/home/app/.config/opencode`; `opencode serve` auto-loads global config from `~/.config/opencode`. Verify in Phase 5 tests. |
| uv/opencode installers fail as non-root | Install as `app` to `/home/app`, copy binaries to `/usr/local/bin` as root. Binaries are world-executable. |
| Git "dubious ownership" on `/workspace` | `git config --global --add safe.directory '*'` for `app` (webhook image). |
| Git commits fail (no identity) | Default `user.name`/`user.email` baked; env override (`GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`). |
| Entrypoint privilege model | **Decided: root entrypoint + gosu.** Container starts as root (no `USER` in Dockerfile); entrypoint performs first-mount volume `chown` (guarded by ownership check), then `exec gosu app "$@"` drops to non-root before running the server. |
| SSH key access for git push | If SSH keys are bind-mounted or injected for git push over SSH, they must be owned by UID `APP_UID` with mode 600. Document operator responsibility for key file permissions. SSH agent forwarding (`SSH_AUTH_SOCK`) works regardless of container UID. |
| `br`/`bvr` require root | None expected — pure userspace Rust binaries. Verify in Phase 5. |
| Build reproducibility / `validate.yml` build job | All changes are deterministic; pinned versions unchanged. CI build job must pass. |

## Acceptance Criteria

1. `docker run --rm ghcr.io/nam20485/orchestrator-service:dev id -u` prints `1000` (non-root).
2. `docker run --rm ghcr.io/nam20485/orchestrator-service/webhook:dev id -u` prints `1000`.
3. `docker run --rm ghcr.io/nam20485/orchestrator-service/caddy:dev id -u` prints the caddy user's
   UID (non-zero).
4. With `APP_UID=$(id -u)`, a full beads cycle writes files to `$WORKSPACE_DIR` owned by the host
   operator (verifiable via `ls -l` — no `root` owner, no `sudo` needed to delete).
5. `opencode serve` starts and loads global config from `/home/app/.config/opencode` (no
   "config not found" errors in logs).
6. Webhook receiver performs `git clone`/`worktree`/`push`/`gh pr create` as `app` without
   "dubious ownership" or identity errors.
7. Caddy proxies `:80` → `webhook-receiver:8080` successfully as non-root (with
   `CAP_NET_BIND_SERVICE`).
8. `pwsh -NoProfile -File ./scripts/validate.ps1 -All` passes clean (lint, scan, test).
9. CI `validate.yml` (including the Docker `build` job) is green.

## Validation Plan

1. **Local validation:** `pwsh -NoProfile -File ./scripts/validate.ps1 -All` — must pass clean.
2. **New bash tests:** `test/test-docker-user.sh` (UID assertions, PATH binaries, bind-mount
   ownership) — added to the test suite.
3. **Manual — ownership:** bring up `compose.development.yaml` with
   `APP_UID=$(id -u) APP_GID=$(id -g)`; trigger a beads cycle; confirm `$WORKSPACE_DIR` files are
   operator-owned and deletable without `sudo`.
4. **Manual — function:** webhook dispatch → opencode run; `/perfect-idea` → `/plan-to-beads` →
   BeadsLoop processes a bead → `br close` → push → PR. All as non-root.
5. **Manual — Caddy:** `curl http://localhost/health` (or webhook endpoint) through the proxy.
6. **Migration check:** on a host with pre-existing root-owned workspace files, run the documented
   one-time `sudo chown -R` and confirm subsequent runs produce operator-owned files.
7. **CI:** `gh run list --workflow=validate.yml --limit 5` → green, including the `build` job.

## Affected Files

| File | Change |
|---|---|
| `Dockerfile` | Add `APP_UID`/`APP_GID` args, create `app` user, repath `/root`→`/home/app`, gosu entrypoint, config-copy retarget, remove dead `PATH` env |
| `Dockerfile.webhook` | Same user/repath; `uv sync` cache mount repathed; git identity defaults; gosu entrypoint; remove dead `PATH` env |
| `deploy/caddy/Dockerfile` | `USER caddy` (upstream UID 1000, volumes writable — verified) |
| `scripts/docker-entrypoint.sh` | Add gosu privilege drop + idempotent `opencode-memory` volume `chown` (ownership-guarded) |
| `scripts/git-trust.sh` | No script change needed (already uses `$HOME`); invoked with `HOME=/home/app` in Dockerfiles |
| `compose.yaml` | `user:` on all services; `cap_add: CAP_NET_BIND_SERVICE` on `webhook-proxy` |
| `compose.development.yaml` | Same as `compose.yaml` |
| `test/test-docker-user.sh` | **New** — UID/PATH/bind-mount ownership assertions |
| `test/test-compose-config.sh` | Update if `user:`/`cap_add` affect assertions |
| `scripts/validate.ps1` | Register new test script (if needed) |
| `README.md` | Non-root execution section + one-time migration |
| `docs/deployment-compose.md` | `APP_UID`/`APP_GID` + cap requirement |
| `AGENTS.md` | "Learned Workspace Facts" update |
| `GEMINI.md` | Mirror AGENTS.md workspace-fact update |

## Resolved Questions

All questions resolved before implementation:

1. **Entrypoint privilege model → gosu root entrypoint.** Container starts as root; entrypoint
   chowns first-mount volumes then `exec gosu app "$@"`. Adds `gosu` as a dependency (small,
   standard, available in Debian repos). No `USER` directive in either Dockerfile.
2. **Upstream caddy user → verified.** `caddy:2.10.0-alpine` ships a `caddy` user at UID 1000.
   `/data` and `/config` are owned by `caddy:caddy` in the upstream image. No additional chown
   or wrapper needed.
3. **`APP_UID` default → 1000 confirmed.** Matches the primary operator's UID. Runtime override
   via compose `user:` directive available for hosts with different UIDs.
4. **`br`/`bvr` as non-root → no issues.** Pure userspace Rust binaries with no root
   assumptions. Beads database (`beads.db`) and lockfiles in `/workspace/<slug>/.beads/` are
   created by the runtime user (UID match on bind mount).

## Rollback

If the non-root container causes issues after deployment, revert to the previous image tag
(e.g. `ghcr.io/nam20485/orchestrator-service:main-latest` from before the change). No data
migration is needed: files created by the non-root container are operator-owned on the host
and remain accessible. Pre-existing root-owned files from before the migration are unaffected
by rollback.

## Estimated Effort

**~2–3 days** of focused work: the Dockerfile repathing is mechanical but touches two
Dockerfiles end-to-end, the gosu entrypoint addition needs careful testing (especially the
first-mount volume chown), and the bulk of effort is integration testing (build + a full beads
cycle + webhook dispatch + Caddy proxy, all as non-root) plus the one-time-migration
documentation and CI iteration. Well-bounded; no architectural changes.
