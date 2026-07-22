#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

# --- Static contract checks for non-root container execution -----------------
# Verifies the configuration the non-root runtime relies on. The CI `test` job
# does not build the project images (that's the separate `build` job), so we
# assert the Dockerfiles, compose files, and entrypoint scripts directly rather
# than running a container. A regression in any of these assumptions would let
# the pipeline pass while breaking non-root execution at runtime.

fail() { echo "FAIL: $*" >&2; exit 1; }

# 1. Compose must NOT set a runtime `user:` — the entrypoint owns the privilege
#    drop (gosu/su-exec). A runtime `user:` would bypass the drop and start as
#    an arbitrary numeric UID that cannot write the build-time-owned dirs.
export OPENCODE_SERVER_PASSWORD="FAKE-PASSWORD-FOR-TESTING"
export OS_WEBHOOK_SECRET="FAKE-WEBHOOK-SECRET-FOR-TESTING"
export ZAI_CODING_API_KEY="FAKE-KEY-FOR-TESTING-00000000"
export WEBHOOK_SITE_ADDRESS=":80"
export WORKSPACE_DIR="/tmp/test-workspace-$$"

user_keys="$(docker compose -f compose.yaml config --format json \
  | jq -c '[.services | to_entries[] | select(.value.user != null) | {service: .key, user: .value.user}]')"
[ "$user_keys" = "[]" ] || fail "compose.yaml sets a runtime 'user:' (must be omitted): ${user_keys}"
echo "compose.yaml has no runtime user: ok"

# 2. Main Dockerfile: no USER directive (entrypoint drops via gosu), declares
#    the APP_UID/APP_GID build args, and creates the app user.
grep -qE '^USER\b' Dockerfile && fail "Dockerfile must not set a USER directive (entrypoint owns the gosu drop)"
grep -qE '^ARG APP_UID=' Dockerfile || fail "Dockerfile missing 'ARG APP_UID='"
grep -qE '^ARG APP_GID=' Dockerfile || fail "Dockerfile missing 'ARG APP_GID='"
grep -qE 'useradd.*\bapp\b' Dockerfile || fail "Dockerfile missing app user creation"
echo "Dockerfile non-root contract: ok"

# 3. Caddy Dockerfile: no USER directive; creates a caddy user; root entrypoint.
grep -qE '^USER\b' deploy/caddy/Dockerfile && fail "deploy/caddy/Dockerfile must not set a USER directive (root entrypoint owns the su-exec drop)"
grep -qE 'adduser.*caddy' deploy/caddy/Dockerfile || fail "caddy Dockerfile missing caddy user creation"
grep -qE '^ENTRYPOINT' deploy/caddy/Dockerfile || fail "caddy Dockerfile missing root ENTRYPOINT"
echo "caddy Dockerfile non-root contract: ok"

# 4. Entrypoints contain the privilege-drop commands and named-volume chowns.
grep -q 'gosu app' scripts/docker-entrypoint.sh || fail "docker-entrypoint.sh missing 'gosu app' drop"
grep -q '/app/.memory' scripts/docker-entrypoint.sh || fail "docker-entrypoint.sh missing memory-dir chown"
grep -q 'su-exec caddy' deploy/caddy/caddy-entrypoint.sh || fail "caddy-entrypoint.sh missing 'su-exec caddy' drop"
grep -qE 'for d in /data /config' deploy/caddy/caddy-entrypoint.sh || fail "caddy-entrypoint.sh missing /data+/config chown"
# Webhook entrypoint must chown the root-owned runner-log bind mount to app
# before the gosu drop (Docker creates ./traces/runner as root:root on first
# attach; without this fixup the non-root app user cannot write runner logs).
grep -q 'gosu app' scripts/webhook-entrypoint.sh || fail "webhook-entrypoint.sh missing 'gosu app' drop"
grep -q '/tmp/orchestrator-webhook' scripts/webhook-entrypoint.sh || fail "webhook-entrypoint.sh missing log-dir chown"
grep -qF 'ENTRYPOINT ["webhook-entrypoint.sh"]' Dockerfile.webhook || fail "Dockerfile.webhook must use webhook-entrypoint.sh as ENTRYPOINT"
echo "entrypoint privilege-drop logic: ok"

echo "docker-user: ok"
