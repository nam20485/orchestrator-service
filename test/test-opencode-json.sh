#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${ROOT}/image/.opencode/opencode.json"

command -v jq >/dev/null || { echo "jq required" >&2; exit 1; }

jq empty "${CONFIG}"
grep -q '"default_agent"' "${CONFIG}"
grep -q '"remote"' "${CONFIG}"
echo "opencode.json: ok"
