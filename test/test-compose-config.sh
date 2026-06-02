#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

export OPENCODE_SERVER_PASSWORD="FAKE-PASSWORD-FOR-TESTING"
export OS_WEBHOOK_SECRET="FAKE-WEBHOOK-SECRET-FOR-TESTING"
export ZAI_CODING_API_KEY="FAKE-KEY-FOR-TESTING-00000000"
export WEBHOOK_SITE_ADDRESS=":80"

docker compose -f compose.yaml config --quiet
echo "compose config: ok"
