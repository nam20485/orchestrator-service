#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

# --- Static contract checks for container HEALTHCHECK probes -----------------
# The CI `test` job does not build images (that's the separate `build` job),
# so these assert the Dockerfile directives directly. A regression here would
# let a container silently start without ever being reported "healthy".

fail() { echo "FAIL: $*" >&2; exit 1; }

grep -qE '^HEALTHCHECK\b.*--start-period' Dockerfile || fail "Dockerfile missing HEALTHCHECK with --start-period"
grep -qE '/dev/tcp/127\.0\.0\.1/4099' Dockerfile || fail "Dockerfile HEALTHCHECK must probe the opencode TCP listener on 4099"
echo "Dockerfile healthcheck: ok"

grep -qE '^HEALTHCHECK\b.*--start-period' Dockerfile.webhook || fail "Dockerfile.webhook missing HEALTHCHECK with --start-period"
grep -qE 'curl -fsS "http://127\.0\.0\.1:\$\{WEBHOOK_PORT\}/health"' Dockerfile.webhook || fail "Dockerfile.webhook HEALTHCHECK must probe /health"
echo "Dockerfile.webhook healthcheck: ok"

grep -qE '^HEALTHCHECK\b.*--start-period' deploy/caddy/Dockerfile || fail "caddy Dockerfile missing HEALTHCHECK with --start-period"
grep -qE 'wget -q -O- http://127\.0\.0\.1:2019/config/' deploy/caddy/Dockerfile || fail "caddy Dockerfile HEALTHCHECK must probe the admin API"
echo "caddy Dockerfile healthcheck: ok"

# compose.yaml must gate webhook-receiver on orchestratorservice's health so
# dispatch can't fire before opencode serve is actually listening.
export OPENCODE_SERVER_PASSWORD="FAKE-PASSWORD-FOR-TESTING"
export WORKSPACE_DIR="/tmp/test-workspace-$$"

if command -v docker >/dev/null 2>&1; then
  condition=$(docker compose -f compose.yaml config --format json \
    | jq -r '.services["webhook-receiver"].depends_on.orchestratorservice.condition // empty')
  [ "$condition" = "service_healthy" ] || fail "webhook-receiver must depend_on orchestratorservice with condition: service_healthy"
  echo "compose depends_on service_healthy: ok"
else
  echo "docker not available; skipping compose depends_on check"
fi

echo "docker-healthchecks: ok"
