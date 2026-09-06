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

BOUND="$(docker port "${PROXY}" 8081/tcp 2>/dev/null | head -1)"
if [[ -z "${BOUND}" ]]; then
  echo "FAIL: could not discover the proxy's published loopback address"
  exit 1
fi

# Wait for Caddy to serve (config adapt + listen is sub-second, but the
# container start is async).
ready=0
for _ in $(seq 1 30); do
  if curl -s -o /dev/null --max-time 2 "http://${BOUND}/health"; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "${ready}" != "1" ]]; then
  echo "FAIL: proxy never became reachable at ${BOUND}"
  docker logs "${PROXY}" 2>&1 | tail -20
  exit 1
fi

failures=0

# status <path> <expected-code>
status() {
  curl -s -o /dev/null --max-time 5 -w '%{http_code}' "http://${BOUND}$1"
}

check() {
  local path="$1" expected="$2" actual
  actual="$(status "${path}")"
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
  "http://${BOUND}/webhooks/github")"
if [[ "${post_code}" != "200" ]]; then
  echo "FAIL: POST /webhooks/github -> ${post_code} (expected 200)"
  failures=$((failures + 1))
fi

# Every one of these is served by webhook-receiver but must not be reachable
# through the public site.
for path in \
  /dashboard \
  /dashboard/ \
  /dashboard/runs \
  /dashboard/events \
  /dashboard/webhooks \
  /dashboard/pages/ \
  /api/dashboard/overview \
  /api/dashboard/beads \
  /api/dashboard/runs \
  /api/dashboard/webhooks \
  /simulator \
  /docs \
  /openapi.json \
  /redoc \
  / \
  /webhooks \
  /webhooks/github/extra \
  ; do
  check "${path}" 404
done

# The 404 must be the proxy's own, not an upstream leak of page content.
body="$(curl -s --max-time 5 "http://${BOUND}/dashboard")"
if [[ "${body}" == *"<html"* || "${body}" == *"dashboard_token"* ]]; then
  echo "FAIL: /dashboard returned page content through the public site"
  failures=$((failures + 1))
fi

if [[ "${failures}" != "0" ]]; then
  echo "caddyfile routes: ${failures} failure(s)"
  exit 1
fi

echo "caddyfile routes: ok"
