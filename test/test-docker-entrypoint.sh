#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENTRYPOINT="${ROOT}/scripts/docker-entrypoint.sh"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

export HOME="${WORKDIR}/home"
mkdir -p "${HOME}/.local/share/opencode"

run_entrypoint() {
  local zai_key="${1-}"
  local zai_alt_key="${2-}"
  local openrouter_key="${3-}"
  local model_studio_key="${4-}"
  env -i \
    PATH="/usr/bin:/bin" \
    HOME="${HOME}" \
    ZAI_CODING_API_KEY="${zai_key}" \
    ZAI_API_KEY="${zai_alt_key}" \
    OPENROUTER_API_KEY="${openrouter_key}" \
    MODEL_STUDIO_API_KEY="${model_studio_key}" \
    /bin/sh "${ENTRYPOINT}" /bin/true
}

# No keys -> must fail
if run_entrypoint "" "" "" "" 2>/dev/null; then
  echo "expected entrypoint to fail without provider keys" >&2
  exit 1
fi

run_entrypoint "FAKE-KEY-FOR-TESTING-00000000" "" "" ""

AUTH="${HOME}/.local/share/opencode/auth.json"
test -f "${AUTH}"
grep -q 'zai-coding-plan' "${AUTH}"
grep -q 'FAKE-KEY-FOR-TESTING' "${AUTH}"

rm -f "${AUTH}"
run_entrypoint "" "" "" "FAKE-MODEL-STUDIO-KEY-FOR-TESTING"
grep -q 'bailian-payg' "${AUTH}"
grep -q 'FAKE-MODEL-STUDIO-KEY-FOR-TESTING' "${AUTH}"

echo "docker-entrypoint: ok"
