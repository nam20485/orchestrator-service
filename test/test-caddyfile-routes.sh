#!/usr/bin/env bash
set -euo pipefail

# Functional check for deploy/caddy/Caddyfile: the public site must proxy the
# webhook endpoint and the health probe and answer 404 for everything else.
#
# test-caddyfile.sh only proves the config *parses*. This test runs it: the
# upstream stand-in returns 200 for every path, so any 404 seen through the
# proxy can only come from the proxy's own path restriction. That is what keeps
# the dashboard, the dashboard API, and the simulator off the Tailscale Funnel
# target (see plan_docs/dashboard-local-only-access-plan.md).

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CADDYFILE="${ROOT}/deploy/caddy/Caddyfile"
IMAGE="caddy:2.10.0-alpine"

SUFFIX="$$"
NET="caddy-routes-net-${SUFFIX}"
UPSTREAM="caddy-routes-up-${SUFFIX}"
PROXY="caddy-routes-proxy-${SUFFIX}"
WORKDIR="$(mktemp -d)"

cleanup() {
  docker rm -f "${PROXY}" "${UPSTREAM}" >/dev/null 2>&1 || true
  docker network rm "${NET}" >/dev/null 2>&1 || true
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

# Upstream answers 200 on every path, so a 404 is attributable to the proxy.
printf ':8080 {\n\trespond "upstream-ok" 200\n}\n' > "${WORKDIR}/upstream.Caddyfile"

docker network create "${NET}" >/dev/null

# --network-alias reproduces the compose service DNS name the Caddyfile upstreams to.
docker run -d --rm --name "${UPSTREAM}" \
  --network "${NET}" --network-alias webhook-receiver \
  -v "${WORKDIR}/upstream.Caddyfile:/etc/caddy/Caddyfile:ro" \
  "${IMAGE}" >/dev/null

# Publish on a random loopback port so the test cannot collide with a real
# deployment's :80/:8081.
docker run -d --rm --name "${PROXY}" \
  --network "${NET}" \
  -e WEBHOOK_SITE_ADDRESS=':8081' \
  -v "${CADDYFILE}:/etc/caddy/Caddyfile:ro" \
  -p "127.0.0.1::8081" \
  "${IMAGE}" >/dev/null

# `|| true` keeps a docker/curl transport failure from aborting the script
# under set -euo pipefail before the FAIL diagnostics can print; the guard
# surfaces the failure as an empty/000 value that fails the checks instead.
BOUND="$(docker port "${PROXY}" 8081/tcp 2>/dev/null | head -1 || true)"
if [[ -z "${BOUND}" ]]; then
  echo "FAIL: could not discover the proxy's published loopback address"
  exit 1
fi

# Wait for Caddy to serve (config adapt + listen is sub-second, but the
# container start is async). Require an actual 200: while the stub upstream is
# still starting, reverse_proxy answers 502, and treating any response as
# ready would let the allowlist checks below race the upstream.
ready=0
for _ in $(seq 1 30); do
  code="$(curl -s -o /dev/null --max-time 2 -w '%{http_code}' "http://${BOUND}/health" || true)"
  if [[ "${code}" == "200" ]]; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "${ready}" != "1" ]]; then
  echo "FAIL: proxy never became reachable at ${BOUND}"
  docker logs "${PROXY}" 2>&1 | tail -20 || true
  exit 1
fi

failures=0

# status <path> <expected-code>
status() {
  curl -s -o /dev/null --max-time 5 -w '%{http_code}' "http://${BOUND}$1"
}

check() {
  local path="$1" expected="$2" actual
  actual="$(status "${path}" || true)"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "FAIL: ${path} -> ${actual} (expected ${expected})"
    failures=$((failures + 1))
  fi
}

echo "public surface: proxying only the webhook and health paths"

# Must stay reachable: a regression here silently breaks GitHub delivery.
check /webhooks/github 200
check /health 200
check '/health?probe=1' 200

# POST is how GitHub delivers; method must not change the outcome.
post_code="$(curl -s -o /dev/null --max-time 5 -w '%{http_code}' \
  -X POST -H 'Content-Type: application/json' -d '{}' \
  "http://${BOUND}/webhooks/github" || true)"
if [[ "${post_code}" != "200" ]]; then
  echo "FAIL: POST /webhooks/github -> ${post_code} (expected 200)"
  failures=$((failures + 1))
fi

# Every one of these is served by webhook-receiver but must not be reachable
# through the public site. The list tracks the receiver's routes (app.py,
# dashboard.py, simulator.py, plus FastAPI's default /docs, /redoc,
# /openapi.json, /docs/oauth2-redirect) — add new receiver routes here so a
# targeted proxy regression cannot slip through.
for path in \
  /dashboard \
  /dashboard/ \
  /dashboard/runs \
  /dashboard/events \
  /dashboard/webhooks \
  /dashboard/pages \
  /dashboard/pages/ \
  /dashboard/bead/x \
  /dashboard/runs/x \
  /dashboard/pages/x \
  /api/dashboard/overview \
  /api/dashboard/beads \
  /api/dashboard/beads/x \
  /api/dashboard/beads/x/logs \
  /api/dashboard/graph \
  /api/dashboard/active \
  /api/dashboard/events \
  /api/dashboard/events/stream \
  /api/dashboard/run-events \
  /api/dashboard/pages/refresh \
  /api/dashboard/runs \
  /api/dashboard/runs/x/logs \
  /api/dashboard/runs/x/narrative \
  /api/dashboard/webhooks \
  /api/dashboard/webhooks/x \
  /simulator \
  /simulator/api/templates \
  /simulator/api/templates/x \
  /simulator/api/send \
  /docs \
  /docs/oauth2-redirect \
  /openapi.json \
  /redoc \
  / \
  /webhooks \
  /webhooks/github/extra \
  ; do
  check "${path}" 404
done

# The 404 must be the proxy's own, not an upstream leak of page content.
body="$(curl -s --max-time 5 "http://${BOUND}/dashboard" || true)"
if [[ "${body}" == *"<html"* || "${body}" == *"dashboard_token"* ]]; then
  echo "FAIL: /dashboard returned page content through the public site"
  failures=$((failures + 1))
fi

if [[ "${failures}" != "0" ]]; then
  echo "caddyfile routes: ${failures} failure(s)"
  exit 1
fi

echo "caddyfile routes: ok"
