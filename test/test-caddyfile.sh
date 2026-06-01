#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CADDYFILE="${ROOT}/deploy/caddy/Caddyfile"

docker run --rm \
  -e WEBHOOK_SITE_ADDRESS=":80" \
  -v "${CADDYFILE}:/etc/caddy/Caddyfile:ro" \
  caddy:2.10.0-alpine \
  caddy validate --config /etc/caddy/Caddyfile

echo "caddyfile: ok"
