#!/bin/sh
set -e

AUTH_SOURCE="${OPENCODE_AUTH_JSON:-/run/opencode/auth.json}"
AUTH_DEST="/root/.local/share/opencode/auth.json"

mkdir -p "$(dirname "$AUTH_DEST")"

if [ -f "$AUTH_SOURCE" ]; then
  cp "$AUTH_SOURCE" "$AUTH_DEST"
elif [ -n "${ZAI_CODING_API_KEY:-}" ] || [ -n "${ZAI_API_KEY:-}" ] || [ -n "${OPENROUTER_API_KEY:-}" ]; then
  python3 - <<'PY'
import json
import os
import pathlib

auth_path = pathlib.Path("/root/.local/share/opencode/auth.json")
auth_path.parent.mkdir(parents=True, exist_ok=True)

auth = {}
if auth_path.exists():
    auth = json.loads(auth_path.read_text())

zai_key = os.environ.get("ZAI_CODING_API_KEY") or os.environ.get("ZAI_API_KEY")
if zai_key:
    auth["zai-coding-plan"] = {"type": "api", "key": zai_key}

openrouter_key = os.environ.get("OPENROUTER_API_KEY")
if openrouter_key:
    auth["openrouter"] = {"type": "api", "key": openrouter_key}

if auth:
    auth_path.write_text(json.dumps(auth, indent=2) + "\n")
PY
else
  echo "ERROR: No OpenCode credentials found. Mount auth/auth.json or set ZAI_API_KEY / OPENROUTER_API_KEY." >&2
  exit 1
fi

exec "$@"
