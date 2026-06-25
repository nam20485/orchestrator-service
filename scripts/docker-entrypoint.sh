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

exec "$@"
