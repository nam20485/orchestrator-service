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
   `/home/nam20485/orchestrator-workspace/{backend,frontend,.git,.beads}` are all `root:root`
   (`drwxr-xr-x`), requiring `sudo find … -delete` to clean up a stale project.

2. **Security posture.** A process inside the container running as UID 0 has full root privileges
   over the bind-mounted host directory and any named volumes. A compromised or misbehaving agent
   session can `chown`/`rm`/write anywhere in the workspace tree with no UID boundary. Running as a
   non-root UID matching the host user is standard container hardening and eliminates the
   privilege boundary on the bind mount.

### Root cause (current state)

| Location | Root assumption |
|---|---|
| `Dockerfile` (orchestratorservice) | No `USER`. Installers write to `/root/.local/bin` (uv), `/root/.opencode/bin` (opencode), `/root/.config/opencode` (config). `ENV PATH="/root/.opencode/bin:${PATH}"`. `RUN mkdir -p /workspace && chmod 755 /workspace`. Config-copy step targets `/root/.config/opencode`. |
| `Dockerfile.webhook` | No `USER`. Same `/root/.local/bin` (uv), `/root/.opencode/bin` (opencode), `PATH`. `uv sync` creates `/app/.venv` as root. |
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

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Where the user is defined | **Baked into each image** via `USER` + build `ARG` (default UID/GID `1000:1000`) | Deterministic; image is non-root by default without relying on compose. Compose `user:` override remains available for host-UID mismatch. |
| User name | `app` (home `/home/app`) | Conventional, short, unambiguous. |
| UID/GID configurability | Build args `APP_UID` / `APP_GID` (default `1000`); compose can override at runtime via `user:` | Build-time default matches common host user; runtime override handles hosts where the operator UID ≠ 1000. |
| Install paths | Repath all `/root/...` → `/home/app/...` (uv, opencode, config dirs, `PATH`) | Installers (`curl \| sh`) honor `$HOME`; running the install `RUN` steps as `USER app` with `HOME=/home/app` writes to the right place without post-hoc `chown`. |
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
USER app (UID/GID = APP_UID:APP_GID, default 1000:1000)
HOME=/home/app
PATH includes /home/app/.opencode/bin

/home/app/
  .local/bin/uv, uvx          (was /root/.local/bin)
  .opencode/bin/opencode      (was /root/.opencode/bin)
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
3. Set `ENV HOME=/home/app` and `ENV PATH="/home/app/.opencode/bin:${PATH}"` (replace the
   `/root/.opencode/bin` entry).
4. Reorder so the `uv`, `opencode`, and config-copy `RUN` steps execute as `USER app` (so
   `curl | sh` installers write to `/home/app`). For steps that must run as root (apt installs,
   `/usr/local/bin` copies, `/etc/...`), keep them before the `USER app` switch or use
   `chown`/`cp` into system paths.
   - uv installer: install to `/home/app/.local` (run as `app`), then symlink/copy `uv`,`uvx` to
     `/usr/local/bin` as root (world-executable) so PATH is robust.
   - opencode installer: same pattern — install as `app`, copy binary to `/usr/local/bin/opencode`.
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
7. Declare `USER app` before `ENTRYPOINT`/`CMD`. Note: if the entrypoint must perform the
   runtime volume `chown` below, do **not** set `USER app` here — instead start as root and drop
   privileges inside the entrypoint (see step 8). The two options are mutually exclusive; pick one
   entrypoint model and apply it consistently to both images.
8. Entrypoint (`scripts/docker-entrypoint.sh`): the entrypoint **must start as root** to perform the
   first-mount memory-volume fixup, then drop to `app` before `exec`-ing the server. Install `gosu`
   in the image and structure the entrypoint as:
   ```sh
   MEM_DIR="/app/.memory"
   if [ -d "$MEM_DIR" ] && [ "$(stat -c %u "$MEM_DIR")" = "0" ]; then
     chown -R app:app "$MEM_DIR" 2>/dev/null || true
   fi
   exec gosu app "$@"
   ```
   Do **not** set `USER app` in the Dockerfile when using this root entrypoint — the `gosu` drop is
   what makes the process non-root. (Guarded by a root-ownership check so the `chown` is a no-op on
   subsequent starts.) If you instead keep `USER app` baked in and skip the runtime `chown`, the
   operator must pre-`chown` the named volume once on the host — document that as the alternative.

### Phase 2 — `webhook-receiver` image (`Dockerfile.webhook`)

1. Same `APP_UID`/`APP_GID` args + `groupadd`/`useradd` + `ENV HOME=/home/app` + `PATH` fix.
2. Repath uv (`/home/app/.local`) and opencode (`/home/app/.opencode`) installers; copy binaries to
   `/usr/local/bin`.
3. `uv sync --frozen --no-dev` must run as `app` (or `chown -R app:app /app` after) so `.venv` is
   writable/usable at runtime.
4. Add git configuration for the `app` user:
   ```dockerfile
   USER app
   RUN git config --global --add safe.directory '*' \
    && git config --global user.name "orchestrator-bot" \
    && git config --global user.email "bot@orchestrator.local"
   ```
   Bake **fixed** identity defaults (no `${VAR}` expansion here — that would freeze the build-time
   value into `~/.gitconfig`, not honor runtime overrides). For per-run author identity, set the
   standard git env vars (`GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`,
   `GIT_COMMITTER_EMAIL`) in the runtime environment (compose) or in a startup script that writes
   `~/.gitconfig` from the current env; git honors these over the baked config at commit time.
   (`safe.directory '*'` covers the bind-mounted `/workspace`.)
5. `WORKDIR /app` + `chown -R app:app /app`.
6. Declare `USER app` before `CMD`.

### Phase 3 — `webhook-proxy` image (`deploy/caddy/Dockerfile`)

1. Extend upstream caddy:
   ```dockerfile
   FROM caddy:2.10.0-alpine@sha256:ae4458638da8e1a91aafffb231c5f8778e964bca650c8a8cb23a7e8ac557aa3c
   COPY Caddyfile /etc/caddy/Caddyfile
   # caddy image already provides a non-root-capable entrypoint; declare USER and
   # rely on CAP_NET_BIND_SERVICE (compose) for :80/:443.
   USER caddy
   ```
   (Upstream `caddy` image ships a `caddy` user; verify UID and that `/data`,`/config` are
   writable by it — see Open Questions.)
2. If upstream `caddy` user cannot write `/data`/`/config`, add an init `chown` via a small
   entrypoint wrapper or `COPY --chown=caddy:caddy`.

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

### Phase 5 — Tests

1. **New** `test/test-docker-user.sh` (bash, alongside existing `test/test-*.sh`):
   - Build image; `docker run --rm <image> id -u` → exits with UID `1000` (or `APP_UID`).
   - `docker run --rm <image> sh -c 'touch /workspace/x && stat -c %u /workspace/x'` with a bind
     mount → file UID equals the run UID (proves no forced root).
   - `docker run --rm <image> sh -c 'opencode --version && br --version'` → binaries on PATH.
2. **New** pytest: assert `git config --global --get safe.directory` returns `*` in webhook image
   (or assert via a container exec in CI).
3. Update `test/test-compose-config.sh` if `user:`/`cap_add` affect the config assertion.
4. Update `scripts/validate.ps1` test list if a new script is added.

### Phase 6 — Documentation

1. **README.md** — add "Non-root execution" note: default UID 1000, how to override
   (`APP_UID`/`APP_GID`), the one-time host migration (`sudo chown -R $UID:$GID $WORKSPACE_DIR`
   for pre-existing root-owned files), and the Caddy `CAP_NET_BIND_SERVICE` requirement.
2. **docs/deployment-compose.md** — document `APP_UID`/`APP_GID` and the cap requirement.
3. **AGENTS.md** — update "Learned Workspace Facts": containers run as non-root `app` (UID 1000);
   workspace files are operator-owned; note the one-time migration.
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
| Entrypoint needs root for volume chown but `USER app` is set | Decision: entrypoint runs as `app`; volume fixup uses ownership check and is a no-op when already correct. If first-mount chown is required as root, use a tiny root entrypoint that chowns then `exec gosu app "$@"` — see Open Questions (prefer the simpler no-extra-dep path if volumes are pre-chowned in the image). |
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
| `Dockerfile` | Add `APP_UID`/`APP_GID` args, create `app` user, repath `/root`→`/home/app`, `USER app`, config-copy retarget |
| `Dockerfile.webhook` | Same user/repath; `uv sync` as `app`; git `safe.directory` + identity; `USER app` |
| `deploy/caddy/Dockerfile` | `USER caddy` (verify upstream UID/writable volumes) |
| `scripts/docker-entrypoint.sh` | Idempotent `opencode-memory` volume `chown` (ownership-guarded) |
| `compose.yaml` | `user:` on all services; `cap_add: CAP_NET_BIND_SERVICE` on `webhook-proxy` |
| `compose.development.yaml` | Same as `compose.yaml` |
| `test/test-docker-user.sh` | **New** — UID/PATH/bind-mount ownership assertions |
| `test/test-compose-config.sh` | Update if `user:`/`cap_add` affect assertions |
| `scripts/validate.ps1` | Register new test script (if needed) |
| `README.md` | Non-root execution section + one-time migration |
| `docs/deployment-compose.md` | `APP_UID`/`APP_GID` + cap requirement |
| `AGENTS.md` | "Learned Workspace Facts" update |
| `GEMINI.md` | Mirror AGENTS.md workspace-fact update |

## Open Questions (resolve before implementation)

1. **Entrypoint privilege model.** Does the memory-volume `chown` need to run as root on first
   mount? Options: (a) keep entrypoint as `app` and pre-`chown` the volume path in the image build
   (works only if the path exists at build time — named volumes mount at runtime, so no); (b) tiny
   root entrypoint that chowns then `exec gosu app "$@"` (adds a `gosu` dependency); (c) accept
   that the MCP memory server may fail on very first start until a one-time host `chown` is done.
   **Recommendation:** option (b) with `gosu` (small, standard) — cleanest UX. Confirm during
   implementation.
2. **Upstream caddy user UID & writable volumes.** Verify the `caddy:2.10.0-alpine` `caddy` user
   UID and that `/data`/`/config` are writable by it; if not, add a `COPY --chown` or wrapper.
3. **`APP_UID` default.** Confirm `1000` matches the deployment host(s); if the primary operator
   uses a different UID, adjust the default or rely on the runtime override.
4. **`br`/`bvr` as non-root.** Expected fine (userspace binaries), but verify no root assumption in
   the beads runtime (e.g. lockfile perms in `/workspace/<slug>/.beads/`).

## Estimated Effort

**~1–2 days** of focused work: the repathing is mechanical but touches two Dockerfiles end-to-end,
the Caddy port/user wrinkle needs verification, and the bulk of effort is testing (build + a full
beads cycle + webhook dispatch + Caddy proxy, all as non-root) plus the one-time-migration
documentation. Well-bounded; no architectural changes.
