# Non-Root Container Execution

## Status
**Implemented.** All phases complete; validation passing.

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
| Where the user is defined | **`app` user created in image** via build `ARG` (default UID/GID `1000:1000`); privilege drop via `gosu` in entrypoint (no `USER` directive) | Entrypoint starts as root for volume fixup, then `exec gosu app` drops privileges. Compose must **not** set a runtime `user:` — it would bypass the gosu drop. |
| User name | `app` (home `/home/app`) | Conventional, short, unambiguous. |
| UID/GID configurability | Build args `APP_UID` / `APP_GID` (default `1000`); host-UID mismatch handled by **rebuilding** with these args (not a runtime `user:`) | Build-time default matches common host user. A runtime Compose `user:` override is incompatible with the root→`gosu`/`su-exec` design: the pulled images bake `app` at UID 1000, so a different runtime UID cannot write the 1000-owned `/home/app`, `/app/.memory`. (`caddy` is a fixed system UID; its `/data`/`/config` are fixup-owned by its own entrypoint.) `APP_UID`/`APP_GID` configure the `app` user only. |
| Install paths | Repath all `/root/...` → `/home/app/...` (uv, opencode, config dirs) | Installers (`curl \| sh`) honor `$HOME`; `RUN` steps use `HOME=/home/app` prefix so installers write to `/home/app`. All steps run as root; `chown -R app:app /home/app` after installs. |
| `/app` ownership | `chown -R app:app /app` after `COPY`/`uv sync` | Runtime user must read config and (for webhook) write `.venv`/caches. |
| `/workspace` | No image change; works automatically when container UID == host UID | Bind mount is host-owned; matching UID gives seamless read/write with no `chown`. |
| `opencode-memory` named volume | Entrypoint `chown -R app:app /app/.memory` if owned by root (first-mount fixup) | Named volumes are root-owned on first attach; one-time recursive chown guarded by ownership check keeps it idempotent and cheap. |
| Git "dubious ownership" | `git config --global --add safe.directory '*'` for `app` user (webhook image) | `workspace.py` runs git on the bind-mounted `/workspace` whose owner (host UID) differs from historical expectations; `safe.directory` silences the warning without weakening security meaningfully for this trusted internal service. |
| Git author identity | Set `user.name`/`user.email` defaults for `app` (e.g. `orchestrator-bot`) unless overridden by env | Commits/`br` need an identity; provide a sane default, allow env override. |
| Caddy privileged port (`:80`) | **`cap_add: [CAP_NET_BIND_SERVICE]`** in compose (keep `:80`) | Minimal change; preserves the documented `:80`/`:443` model and ACME behavior. Alternative (remap to `:8080` internal) is a larger behavioral change and rejected. |
| Caddy data/config volumes | Entrypoint or image `chown` of `/data` and `/config` to `app` | Caddy must persist ACME state; volumes are root-owned on first mount. |
| Caddy user | Create a `caddy` user in `deploy/caddy/Dockerfile`; root entrypoint + `su-exec` drop (no `USER` directive) | The pinned `caddy:2.10.0-alpine` runs as root and ships **no** non-root user (`/etc/passwd` has only standard Alpine users; `/data`/`/config` are root-owned). The image creates a `caddy` system user, chowns the writable dirs, and grants `:80`/`:443` binding via a file capability (`setcap cap_net_bind_service=+ep /usr/bin/caddy`) plus compose `cap_add: CAP_NET_BIND_SERVICE`. A root entrypoint chowns first-mount named volumes, then drops to `caddy`. The other two images use `app`/`gosu`. |
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
# No runtime `user:` is set on any service. orchestratorservice/webhook-receiver bake
# `app` at UID 1000 and start as root so the gosu entrypoint can drop to `app`;
# webhook-proxy bakes a `caddy` system user (root entrypoint + su-exec drop). A Compose
# `user:` override would bypass the drop and break ownership on non-1000 hosts —
# customize the `app` UID by rebuilding with --build-arg APP_UID/APP_GID instead.

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
    # The auth write above created ~/.local/share/opencode as root; opencode (running
    # as app after the drop) must mkdir/write within it (e.g. repos/), so chown it back.
    chown -R app:app "${HOME}/.local/share/opencode" 2>/dev/null || true
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

1. The pinned `caddy:2.10.0-alpine` image runs as root and ships **no** non-root user
   (verified: `/etc/passwd` contains only standard Alpine users and `Config.User` is empty),
   and `/data`/`/config` are root-owned. Create the user, own the writable dirs, grant the
   privileged-port capability as a **file capability** (so it survives the `su-exec` drop,
   which would otherwise strip `CAP_NET_BIND_SERVICE`), and use a root entrypoint that fixes
   up first-mount named volumes before dropping to `caddy`:
   ```dockerfile
   FROM caddy:2.10.0-alpine@sha256:ae4458638da8e1a91aafffb231c5f8778e964bca650c8a8cb23a7e8ac557aa3c
   COPY Caddyfile /etc/caddy/Caddyfile
   COPY caddy-entrypoint.sh /caddy-entrypoint.sh
   RUN apk add --no-cache su-exec libcap \
    && addgroup -S caddy && adduser -S -D -H -G caddy caddy \
    && chmod +x /caddy-entrypoint.sh \
    && chown -R caddy:caddy /data /config /srv /etc/caddy \
    && setcap cap_net_bind_service=+ep /usr/bin/caddy
   ENTRYPOINT ["/caddy-entrypoint.sh"]
   CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
   ```
   No `USER` directive — the root entrypoint chowns `caddy_data`/`caddy_config` (idempotent,
   and handles volumes that pre-date the non-root switch) then `exec su-exec caddy "$@"`.
   Compose `cap_add: CAP_NET_BIND_SERVICE` sets the bounding set; the file capability makes
   it effective for the non-root `caddy` process.

### Phase 4 — Compose

1. **Do not** set a runtime `user:` on any service. The `orchestratorservice` and
   `webhook-receiver` images have no `USER` directive on purpose — they start as root so the
   entrypoint can `chown` first-mount named volumes and then drop privileges via `gosu app`. A
   Compose `user: "${APP_UID:-...}"` override starts the container as a bare numeric UID that
   bypasses gosu and (because the `app`/`caddy` users and their files are baked at build time)
   cannot write `/home/app`, `/app/.memory`, `/data`, or `/config` on any host whose UID ≠ 1000.
   The correct way to run as a non-1000 UID is to **rebuild** with
   `--build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)` (see `compose.build.yaml`).
2. Add to `webhook-proxy` only:
   ```yaml
   cap_add:
     - CAP_NET_BIND_SERVICE
   ```
3. No change to volume declarations; ownership handled by image/entrypoint.
4. `compose.build.yaml` — **no changes needed** (build overlay only adds `build:` context and
   `pull_policy: never`; rebuild with `--build-arg APP_UID`/`APP_GID` for host-UID mismatch).
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
5. Update `test/test-compose-config.sh` if `cap_add` affects the config assertion (no `user:`
   is set on any service — see Phase 4).
6. Update `scripts/validate.ps1` test list if a new script is added.

### Phase 6 — Documentation

1. **README.md** — add "Non-root execution" note: default UID 1000, how to customize the UID
   (**rebuild** with `APP_UID`/`APP_GID` build args — not a runtime `user:`), the one-time host
   migration (`sudo chown -R $UID:$GID $WORKSPACE_DIR` for pre-existing root-owned files), and
   the Caddy `CAP_NET_BIND_SERVICE` requirement.
2. **docs/deployment-compose.md** — document the rebuild-based `APP_UID`/`APP_GID` override and
   the cap requirement.
3. **AGENTS.md** — update "Learned Workspace Facts": containers run as non-root `app` (UID 1000)
   via gosu entrypoint; workspace files are operator-owned; note the one-time migration. Also
   **remove** the stale claim that `docker-entrypoint.sh` exports `OPENCODE_CONFIG` and
   `OPENCODE_CONFIG_DIR` — the entrypoint does not set these variables; `opencode serve`
   auto-loads config from `~/.config/opencode` (the global config dir).
4. **GEMINI.md** — mirror the AGENTS.md workspace-fact update.

## Edge Cases & Risks

| Edge case | Handling |
|---|---|
| Host operator UID ≠ 1000 | **Rebuild** the images with build args (`--build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)` via `compose.build.yaml`) so the `app` user is re-baked to match the host. A runtime Compose `user:` override does **not** work: the pulled images bake `app` at UID 1000, so a different runtime UID cannot write its files. (`caddy` is a fixed system UID and does not touch the workspace bind mount.) Documented. |
| Pre-existing root-owned workspace files | One-time host migration: `sudo chown -R $(id -u):$(id -g) $WORKSPACE_DIR`. Cannot be done from inside a non-root container. Documented in README. |
| Named volume first-mount root ownership (`opencode-memory`, caddy data/config) | Entrypoint `chown` guarded by ownership check (idempotent). |
| Caddy cannot bind `:80` as non-root | `cap_add: CAP_NET_BIND_SERVICE` (compose, bounding set) **plus** a file capability `setcap cap_net_bind_service=+ep /usr/bin/caddy` (build) so the non-root `caddy` process actually holds the cap after the `su-exec` drop (a privilege drop otherwise strips it). Fallback documented: bind `:8080` internal + remap. |
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
4. After rebuilding with `--build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)`, a full beads
   cycle writes files to `$WORKSPACE_DIR` owned by the host operator (verifiable via `ls -l` — no
   `root` owner, no `sudo` needed to delete).
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
3. **Manual — ownership:** rebuild and bring up `compose.development.yaml` with
   `--build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)`; trigger a beads cycle; confirm
   `$WORKSPACE_DIR` files are operator-owned and deletable without `sudo`.
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
| `deploy/caddy/Dockerfile` | Create `caddy` user + `setcap` file cap + root `caddy-entrypoint.sh` (`su-exec` drop); the upstream image has no caddy user |
| `deploy/caddy/caddy-entrypoint.sh` | **New** — root first-mount `chown` of `/data`/`/config` (idempotent), then `exec su-exec caddy "$@"` |
| `scripts/docker-entrypoint.sh` | Add gosu privilege drop + idempotent `opencode-memory` volume `chown` (ownership-guarded) + `chown` of the runtime-created `~/.local/share/opencode` tree back to `app` (auth write runs as root; opencode needs to `mkdir`/write within it after the drop) |
| `scripts/git-trust.sh` | No script change needed (already uses `$HOME`); invoked with `HOME=/home/app` in Dockerfiles |
| `compose.yaml` | `cap_add: CAP_NET_BIND_SERVICE` on `webhook-proxy` (no `user:` — see Phase 4) |
| `compose.development.yaml` | Same as `compose.yaml` |
| `test/test-docker-user.sh` | **New** — UID/PATH/bind-mount ownership assertions |
| `test/test-compose-config.sh` | Update if `cap_add` affects assertions |
| `scripts/validate.ps1` | Register new test script (if needed) |
| `README.md` | Non-root execution section + one-time migration |
| `docs/deployment-compose.md` | Rebuild-based `APP_UID`/`APP_GID` override + cap requirement |
| `AGENTS.md` | "Learned Workspace Facts" update |
| `GEMINI.md` | Mirror AGENTS.md workspace-fact update |

## Resolved Questions

All questions resolved before implementation:

1. **Entrypoint privilege model → gosu root entrypoint.** Container starts as root; entrypoint
   chowns first-mount volumes then `exec gosu app "$@"`. Adds `gosu` as a dependency (small,
   standard, available in Debian repos). No `USER` directive in either Dockerfile.
2. **Upstream caddy user → re-verified, the original claim was FALSE.**
   `caddy:2.10.0-alpine@sha256:ae4458638da8e1a91aafffb231c5f8778e964bca650c8a8cb23a7e8ac557aa3c`
   runs as root and ships **no** non-root user (`/etc/passwd` has only standard Alpine users;
   `Config.User` is empty); `/data`/`/config` are root-owned. The image therefore creates the
   `caddy` user itself, chowns the writable dirs, uses a root entrypoint (`su-exec` drop) for
   first-mount volume fixup, and grants `:80`/`:443` via a file capability on `/usr/bin/caddy`
   (compose `cap_add` sets the bounding set; the file cap makes it effective for non-root).
3. **`APP_UID` default → 1000 confirmed.** Matches the primary operator's UID. Host-UID mismatch
   is handled by **rebuilding** with `--build-arg APP_UID`/`APP_GID`; a runtime Compose `user:`
   override is **not** used (it bypasses the gosu drop and is incompatible with the baked UID 1000
   file ownership — see Phase 4).
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
