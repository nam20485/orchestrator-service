#!/bin/sh
set -e

AUTH_DEST="/root/.local/share/opencode/auth.json"

mkdir -p "$(dirname "$AUTH_DEST")"

if [ -n "${ZAI_CODING_API_KEY:-}${ZAI_API_KEY:-}${OPENROUTER_API_KEY:-}" ]; then
  python3 - <<'PY'
import json
import os
import pathlib

auth_path = pathlib.Path("/root/.local/share/opencode/auth.json")
auth_path.parent.mkdir(parents=True, exist_ok=True)

auth = {}

zai_key = os.environ.get("ZAI_CODING_API_KEY") or os.environ.get("ZAI_API_KEY")
if zai_key:
    auth["zai-coding-plan"] = {"type": "api", "key": zai_key}

openrouter_key = os.environ.get("OPENROUTER_API_KEY")
if openrouter_key:
    auth["openrouter"] = {"type": "api", "key": openrouter_key}

if not auth:
    raise SystemExit("No provider API keys found in environment.")

auth_path.write_text(json.dumps(auth, indent=2) + "\n")
PY
else
  echo "ERROR: No OpenCode provider credentials found. Set one or more of: ZAI_CODING_API_KEY, ZAI_API_KEY, OPENROUTER_API_KEY." >&2
  exit 1
fi

exec "$@"
