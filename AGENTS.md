## Learned User Preferences

- Wants repo-visible POR/spec documents (e.g. `plan_docs/plan.md`) with requirements, acceptance criteria, validation plan, and phased development—not Cursor-internal plan files alone for approval.
- Does not want RFC-style planning writeups; wants a formal spec the user can see, save, and approve before implementation.
- OpenCode server containers should be generic and workspace-agnostic; do not mount the host repo by default—clients, prompts, or launchers define code access.
- Client wrapper scripts (`scripts/prompt.ps1`, `scripts/attach.ps1`) should be thin pass-throughs that require a local `opencode` CLI; no auto-install or Docker client fallback in the first implementation.
- When the server is network-reachable (e.g. Tailscale/LAN), publish port `4099` and require `OPENCODE_SERVER_PASSWORD` at Compose startup.
- Does not use a project `.env` for compose; provider credentials belong in gitignored `auth/auth.json` mounted at runtime (entrypoint env-var synthesis is fallback only).
- Client scripts should rely on host `OPENCODE_SERVER_PASSWORD` (opencode CLI default); never hardcode server passwords in committed scripts.
- Before commit, scan changed files for secrets (API keys, hardcoded passwords); keep `auth/auth.json` gitignored and never commit API keys.

## Learned Workspace Facts

- Docker image uses `debian:bookworm-slim`, runs `opencode serve` on `0.0.0.0:4099`, and bundles Node.js 24.14.0 (MCP), uv, gh CLI, and Python3.
- Install Node in the Dockerfile via the official `.tar.gz` with `tar -xzf`; `.tar.xz` / `tar -xJf` fails on bookworm-slim because `xz-utils` is not installed.
- One-shot client flow: `opencode run --attach <url>`; interactive attach: `opencode attach <url>` (not `attach --prompt`).
- Authoritative OpenCode server planning spec for this repo: `plan_docs/plan.md`.
- OpenCode server config source of truth is repo `image/` (`opencode.json`, `AGENTS.md`, `.opencode/agents/`, `.opencode/commands/`); Dockerfile copies those into `/app` (no full-repo `COPY . .`); `.dockerignore` excludes non-image files.
- Root repo `AGENTS.md` is Cursor continual-learning memory only; the container uses `image/AGENTS.md` copied to `/app/AGENTS.md` (overwrites any root copy).
- Provider auth: compose mounts gitignored `auth/auth.json` read-only at `/run/opencode/auth.json`; `scripts/docker-entrypoint.sh` copies to `/root/.local/share/opencode/auth.json` where OpenCode reads it.
- `zai-coding-plan/glm-4.7` needs Z.AI Coding Plan credentials in `auth.json`; `OPENROUTER_API_KEY` alone does not authenticate that provider (`ZHIPU_API_KEY` is China Zhipu, not z.ai Coding Plan).
- MCP `memory-graph` in `image/opencode.json` uses `@modelcontextprotocol/server-memory` with `MEMORY_FILE_PATH=/app/.memory/memory.jsonl`.
- Compose `environment: - VAR` passes host shell env into the container; `${VAR}` adds `.env` interpolation—this project does not use `.env`.
- Host client scripts: `scripts/prompt.ps1`, `scripts/attach.ps1` (PowerShell thin wrappers to local `opencode`).
