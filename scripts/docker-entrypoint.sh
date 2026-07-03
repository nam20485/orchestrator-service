#!/bin/sh
set -e

HOME_DIR="${HOME:-/root}"
AUTH_DEST="${HOME_DIR}/.local/share/opencode/auth.json"

mkdir -p "$(dirname "$AUTH_DEST")"

if [ -n "${ZAI_CODING_API_KEY:-}${ZAI_API_KEY:-}${OPENROUTER_API_KEY:-}${MODEL_STUDIO_API_KEY:-}" ]; then
  python3 - <<'PY'
import json
import os
import pathlib

home = pathlib.Path(os.environ.get("HOME", "/root"))
auth_path = home / ".local/share/opencode/auth.json"
auth_path.parent.mkdir(parents=True, exist_ok=True)

auth = {}

zai_key = os.environ.get("ZAI_CODING_API_KEY") or os.environ.get("ZAI_API_KEY")
if zai_key:
    auth["zai-coding-plan"] = {"type": "api", "key": zai_key}

openrouter_key = os.environ.get("OPENROUTER_API_KEY")
if openrouter_key:
    auth["openrouter"] = {"type": "api", "key": openrouter_key}

model_studio_key = os.environ.get("MODEL_STUDIO_API_KEY")
if model_studio_key:
    auth["bailian-payg"] = {"type": "api", "key": model_studio_key}

if not auth:
    raise SystemExit("No provider API keys found in environment.")

auth_path.write_text(json.dumps(auth, indent=2) + "\n")
PY
else
  echo "ERROR: No OpenCode provider credentials found. Set one or more of: ZAI_CODING_API_KEY, ZAI_API_KEY, OPENROUTER_API_KEY, MODEL_STUDIO_API_KEY." >&2
  exit 1
fi

# opencode serve auto-loads config from the global dir (~/.config/opencode),
# where image/.opencode is installed in the Dockerfile (opencode.json, AGENTS.md,
# agents/, commands/, skills/). No OPENCODE_CONFIG/OPENCODE_CONFIG_DIR needed.

# The auth write above created ${HOME_DIR}/.local/share/opencode (and parents)
# as root; opencode runs as `app` after the gosu drop and must mkdir/write within
# it (e.g. repos/), so chown the runtime data tree back to app. Idempotent and
# tiny at startup (only auth.json until opencode populates it).
chown -R app:app "${HOME_DIR}/.local/share/opencode" 2>/dev/null || true

# First-mount fixup: named volumes (e.g. opencode-memory) are root-owned on
# first attach, or may pre-date an APP_UID rebuild (stale prior-UID owner).
# Chown to app:app whenever the owner differs from the current app user
# (idempotent — no-op on subsequent starts). Runs as root before the gosu drop.
MEM_DIR="/app/.memory"
APP_UID="$(id -u app 2>/dev/null || printf '%s\n' 0)"
if [ -d "$MEM_DIR" ] && [ "$(stat -c %u "$MEM_DIR")" != "$APP_UID" ]; then
  chown -R app:app "$MEM_DIR" 2>/dev/null || true
fi

# Memory store self-heal: @modelcontextprotocol/server-memory writes the entire
# memory.jsonl via an unprotected writeFile on every mutation. Concurrent writers
# (the orchestrator plus each subagent session each spawn their own process) can
# interleave writes and corrupt the file so reads fail with a JSON parse error.
# If the store is unparseable on startup, back it up and start fresh so reads stop
# failing. (With the single-writer invariant in place, corruption should not recur,
# but this guards against any pre-existing corrupt store from prior runs.)
MEM_FILE="$MEM_DIR/memory.jsonl"
if [ -f "$MEM_FILE" ] && ! python3 -c "import json,sys;[json.loads(l) for l in open(sys.argv[1]) if l.strip()]" "$MEM_FILE" >/dev/null 2>&1; then
  echo "docker-entrypoint: memory.jsonl is unparseable — backing up and resetting" >&2
  mv "$MEM_FILE" "$MEM_FILE.corrupt-$(date +%s)" 2>/dev/null || true
  : > "$MEM_FILE"
  chown app:app "$MEM_FILE" 2>/dev/null || true
fi

# Privilege drop: start as root (no USER directive in Dockerfile), then exec
# the server as the non-root app user via gosu. Fall back to direct exec if
# gosu is not available (e.g. when running the entrypoint test on the host).
if command -v gosu >/dev/null 2>&1; then
  exec gosu app "$@"
else
  exec "$@"
fi
