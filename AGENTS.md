## Learned User Preferences

- Wants repo-visible POR/spec documents (e.g. `plan_docs/plan.md`) with requirements, acceptance criteria, validation plan, and phased development—not Cursor-internal plan files alone for approval.
- Does not want RFC-style planning writeups; wants a formal spec the user can see, save, and approve before implementation.
- OpenCode server containers should be generic and workspace-agnostic; do not mount the host repo by default—clients, prompts, or launchers define code access.
- Client wrapper scripts (`prompt.sh`, `attach.sh`) should be thin pass-throughs that require a local `opencode` CLI; no auto-install or Docker client fallback in the first implementation.
- When the server is network-reachable (e.g. Tailscale/LAN), publish port `4099` and require `OPENCODE_SERVER_PASSWORD` at Compose startup.

## Learned Workspace Facts

- Docker image uses `debian:bookworm-slim`, runs `opencode serve` on `0.0.0.0:4099`, and bundles Node.js 24.14.0 (MCP), uv, gh CLI, and Python3.
- Install Node in the Dockerfile via the official `.tar.gz` with `tar -xzf`; `.tar.xz` / `tar -xJf` fails on bookworm-slim because `xz-utils` is not installed.
- One-shot client flow: `opencode run --attach <url>`; interactive attach: `opencode attach <url>` (not `attach --prompt`).
- Authoritative OpenCode server planning spec for this repo: `plan_docs/plan.md`.
- Repo includes `scripts/prompt.ps1`; bash wrappers are planned under `scripts/opencode/`.
- OpenCode server config (opencode.json, AGENTS.md, `.opencode/agent/`, `.opencode/commands/`) lives under repo `image/`.
- In the container, OpenCode loads project config from WORKDIR `/app` (`/app/opencode.json`, `/app/.opencode/agents/` or `commands/`); alternatively set `OPENCODE_CONFIG` and `OPENCODE_CONFIG_DIR` to `/app/image/...`.
- Dockerfile `COPY image/* .` runs before `WORKDIR /app` (files land in `/`) and skips hidden `.opencode/`; baked config ends up at `/app/image/` via `COPY . .` unless layout or env vars are fixed.
