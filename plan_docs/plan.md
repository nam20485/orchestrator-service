# OpenCode Server POR

## Purpose

Build a simple Dockerized OpenCode server that clients can use in two modes:

- One-shot non-interactive prompting through a bundled `prompt.sh` interface.
- Full interactive OpenCode attachment through a bundled `attach.sh` interface.

The server should be generic. It must not be tied to this repo, a specific workspace, or a pre-mounted host codebase. Clients, prompts, or higher-level launchers are responsible for telling the server what code to access and how to access it.

## Current Decisions

- The Docker image runs `opencode serve`.
- The OpenCode server listens inside the container on `0.0.0.0:4096`.
- Compose publishes `4096:4096` for direct local, LAN, or Tailscale-peer access.
- `OPENCODE_SERVER_PASSWORD` is required when launching through Compose.
- `OPENCODE_SERVER_USERNAME` remains optional and defaults to OpenCode's default username, `opencode`.
- The container does not mount the host repo or any workspace by default.
- Provider auth/config is not mounted by default.
- Client scripts require `opencode` to already be installed on the client host.
- Client scripts are thin wrappers around OpenCode's native CLI:
  - `opencode run --attach <url> ...` for one-shot prompts.
  - `opencode attach <url> ...` for interactive attachment.

## Requirements

### Server

- Install OpenCode in the Docker image.
- Start OpenCode in server mode by default.
- Expose server port `4096`.
- Require `OPENCODE_SERVER_PASSWORD` at Compose startup.
- Keep the image generic and workspace-agnostic.
- Do not mount `.` or any host workspace by default.
- Do not bake secrets, provider auth, repo credentials, or project-specific config into the image.

### Client Scripts

- Add `scripts/opencode/prompt.sh`.
- Add `scripts/opencode/attach.sh`.
- Both scripts resolve the server URL from:
  - `OPENCODE_SERVER_URL`, if set.
  - Otherwise `OPENCODE_HOST` and `OPENCODE_PORT`.
  - Otherwise default to `http://localhost:4096`.
- Both scripts pass through user arguments to the underlying OpenCode CLI.
- Both scripts fail clearly if local `opencode` is not installed.
- Neither script auto-installs OpenCode.
- Neither script reimplements the OpenCode HTTP API.
- Neither script uses a Dockerized client fallback for the first implementation.

### Documentation

- Document local startup.
- Document one-shot prompting.
- Document interactive attach.
- Document auth environment variables.
- Document Tailscale/LAN usage.
- Document that host files are not visible unless explicitly mounted or cloned inside the container.

## Tailscale and Networking Notes

Tailscale gives a machine an additional tailnet IP. It does not make another machine's `127.0.0.1` reachable.

If Docker publishes only:

```yaml
ports:
  - "127.0.0.1:4096:4096"
```

then only processes on that same host can connect to `localhost:4096`.

For direct access from another Tailscale peer, the service must listen on an address reachable through the Tailscale interface. The selected simple default is:

```yaml
ports:
  - "4096:4096"
```

This binds the published host port on all host interfaces. Because that can include LAN or public interfaces depending on the host, Compose must require `OPENCODE_SERVER_PASSWORD`. Operators should still use host firewall rules, cloud security groups, Tailscale ACLs, or a narrower bind address when needed.

## Proposed File Changes

- `Dockerfile`
  - Keep OpenCode installed via the official installer.
  - Keep `opencode serve --hostname 0.0.0.0 --port 4096` as the default command.

- `compose.yaml`
  - Publish `4096:4096`.
  - Require `OPENCODE_SERVER_PASSWORD`.
  - Pass optional `OPENCODE_SERVER_USERNAME`.
  - Do not mount the repo or provider config by default.

- `scripts/opencode/prompt.sh`
  - Resolve server URL.
  - Check for local `opencode`.
  - Run `opencode run --attach "$url" "$@"`.

- `scripts/opencode/attach.sh`
  - Resolve server URL.
  - Check for local `opencode`.
  - Run `opencode attach "$url" "$@"`.

- `README.md`
  - Add concise usage examples and operational notes.

## Acceptance Criteria

- `docker compose up --build` fails fast if `OPENCODE_SERVER_PASSWORD` is not set.
- `OPENCODE_SERVER_PASSWORD=secret docker compose up --build` starts the OpenCode server.
- `curl http://localhost:4096/global/health` reaches the server when auth is not enforced for that endpoint, or returns an auth-related response when auth applies.
- `./scripts/opencode/prompt.sh "hello"` sends a one-shot prompt to `http://localhost:4096`.
- `./scripts/opencode/attach.sh` starts an interactive OpenCode client attached to `http://localhost:4096`.
- `OPENCODE_SERVER_URL=http://100.x.y.z:4096 ./scripts/opencode/prompt.sh "hello"` targets a remote/Tailscale address.
- Scripts preserve normal OpenCode flags such as `--model`, `--agent`, `--session`, `--file`, `--format`, `--dir`, `--username`, and `--password`.
- Scripts print a clear install hint if `opencode` is missing.
- No default Compose volume exposes the host repo or Docker socket.

## Validation Plan

1. Build the image:

```bash
OPENCODE_SERVER_PASSWORD=secret docker compose build
```

2. Confirm password is required:

```bash
docker compose up
```

Expected: Compose fails with a clear missing `OPENCODE_SERVER_PASSWORD` error.

3. Start the server:

```bash
OPENCODE_SERVER_PASSWORD=secret docker compose up
```

Expected: OpenCode server starts on port `4096`.

4. Check health/spec endpoint:

```bash
curl -u opencode:secret http://localhost:4096/global/health
curl -u opencode:secret http://localhost:4096/doc
```

Expected: health/spec responses confirm the server is reachable.

5. Validate one-shot prompt wrapper:

```bash
OPENCODE_SERVER_PASSWORD=secret ./scripts/opencode/prompt.sh "Say hello"
```

Expected: wrapper calls `opencode run --attach http://localhost:4096 "Say hello"`.

6. Validate interactive attach wrapper:

```bash
OPENCODE_SERVER_PASSWORD=secret ./scripts/opencode/attach.sh
```

Expected: wrapper calls `opencode attach http://localhost:4096`.

7. Validate URL override:

```bash
OPENCODE_SERVER_URL=http://100.x.y.z:4096 OPENCODE_SERVER_PASSWORD=secret ./scripts/opencode/prompt.sh "Say hello"
```

Expected: wrapper targets the provided URL.

8. Validate missing local OpenCode behavior:

```bash
PATH=/usr/bin ./scripts/opencode/prompt.sh "Say hello"
```

Expected: script exits with a clear message explaining that local `opencode` must be installed.

## Phased Development Plan

### Phase 1: Server Compose Defaults

- Update `compose.yaml` to require `OPENCODE_SERVER_PASSWORD`.
- Preserve `4096:4096` publishing.
- Keep the container generic with no default workspace mounts.

### Phase 2: Client Script Interface

- Add `scripts/opencode/prompt.sh`.
- Add `scripts/opencode/attach.sh`.
- Add shared URL resolution logic directly in each script or through a small sourced helper if that stays simple.
- Ensure scripts are executable.

### Phase 3: Documentation

- Update `README.md` with startup, prompt, attach, auth, and Tailscale examples.
- Document that code access is the responsibility of the client prompt or container launcher.

### Phase 4: Validation

- Run the validation commands above.
- Fix any Docker, auth, or shell portability issues found during validation.

## Out Of Scope For First Implementation

- Auto-installing OpenCode from the wrapper scripts.
- Dockerized client fallback.
- Reimplementing OpenCode's HTTP API with `curl`.
- Mounting host workspaces by default.
- Mounting provider auth/config by default.
- Managing Tailscale ACLs, firewall rules, or reverse proxy configuration.
- Creating per-client containers automatically.

## Open Questions

None.
