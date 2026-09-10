#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

export OPENCODE_SERVER_PASSWORD="FAKE-PASSWORD-FOR-TESTING"
export OS_WEBHOOK_SECRET="FAKE-WEBHOOK-SECRET-FOR-TESTING"
export ZAI_CODING_API_KEY="FAKE-KEY-FOR-TESTING-00000000"
export WEBHOOK_SITE_ADDRESS=":80"
export WORKSPACE_DIR="/tmp/test-workspace-$$"

docker compose -f compose.yaml config --quiet
echo "compose config: ok"

docker compose -f compose.yaml -f compose.https.yaml config --quiet
echo "compose https overlay: ok"

# Verify enforcement: compose config should fail without OPENCODE_SERVER_PASSWORD.
# Use --env-file /dev/null so a developer's local .env (auto-loaded by compose)
# does not re-supply the variable and mask a missing enforcement.
if (unset OPENCODE_SERVER_PASSWORD && docker compose --env-file /dev/null -f compose.yaml config --quiet 2>/dev/null); then
  echo "FAIL: compose should require OPENCODE_SERVER_PASSWORD"
  exit 1
else
  echo "compose enforces OPENCODE_SERVER_PASSWORD: ok"
fi

# Verify enforcement: compose config should fail without WORKSPACE_DIR.
if (unset WORKSPACE_DIR && docker compose --env-file /dev/null -f compose.yaml config --quiet 2>/dev/null); then
  echo "FAIL: compose should require WORKSPACE_DIR"
  exit 1
else
  echo "compose enforces WORKSPACE_DIR: ok"
fi

# Verify the webhook-receiver persists runner/beads logs to the host so a
# torn-down/failed run stays diagnosable (T2.2). The mount target is the
# container path runner.py / beads_loop.py write to.
found_log_mount=$(docker compose -f compose.yaml config --format json \
  | jq -r '[.services["webhook-receiver"].volumes[]?.target] | index("/tmp/orchestrator-webhook") // empty')
if [[ -z "$found_log_mount" ]]; then
  echo "FAIL: webhook-receiver must mount a host dir at /tmp/orchestrator-webhook"
  exit 1
fi
echo "webhook-receiver log-dir mount: ok"

# Verify the receiver's dashboard/API port is published loopback-only. Caddy's
# public site restricts itself to the webhook + health paths, and the funnel
# targets that site; the direct receiver publish is the private path to the
# dashboard, so binding it to anything but 127.0.0.1 would re-expose the
# dashboard on the LAN.
receiver_ports=$(docker compose -f compose.yaml config --format json \
  | jq -c '.services["webhook-receiver"].ports // []')
if [[ "$(jq -r 'length' <<<"$receiver_ports")" == "0" ]]; then
  echo "FAIL: webhook-receiver must publish a loopback port for dashboard access"
  exit 1
fi
if [[ "$(jq -r '[.[] | select(.host_ip != "127.0.0.1")] | length' <<<"$receiver_ports")" != "0" ]]; then
  echo "FAIL: webhook-receiver ports must all bind host_ip 127.0.0.1 (got $(jq -c '[.[].host_ip]' <<<"$receiver_ports"))"
  exit 1
fi
echo "webhook-receiver loopback-only publish: ok"

# The same must hold for the development overlay, which otherwise duplicates the
# service block rather than inheriting it.
dev_ports=$(docker compose -f compose.development.yaml config --format json \
  | jq -c '.services["webhook-receiver"].ports // []')
if [[ "$(jq -r '[.[] | select(.host_ip != "127.0.0.1")] | length' <<<"$dev_ports")" != "0" || "$(jq -r 'length' <<<"$dev_ports")" == "0" ]]; then
  echo "FAIL: compose.development.yaml webhook-receiver must publish loopback-only too"
  exit 1
fi
echo "compose.development.yaml loopback-only publish: ok"
