#!/usr/bin/env bash
# Regression tests for scan-uncommitted-secrets/scripts/scan.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCAN="$ROOT/.cursor/skills/scan-uncommitted-secrets/scripts/scan.sh"
FIXTURES="$ROOT/test/fixtures/secret-scan"

if [ ! -x "$SCAN" ] && [ ! -f "$SCAN" ]; then
  echo "missing scanner: $SCAN" >&2
  exit 1
fi

run_scan() {
  SECRET_SCAN_FILES="$1" bash "$SCAN"
}

expect_fail() {
  local label="$1"
  local file="$2"
  if run_scan "$file" >/dev/null 2>&1; then
    echo "FAIL: expected scan to reject $label ($file)" >&2
    exit 1
  fi
  echo "ok reject $label"
}

expect_pass() {
  local label="$1"
  local file="$2"
  if ! run_scan "$file" >/dev/null 2>&1; then
    echo "FAIL: expected scan to accept $label ($file)" >&2
    run_scan "$file" || true
    exit 1
  fi
  echo "ok accept $label"
}

expect_fail "repo webhook secret" "$FIXTURES/bad-os-webhook-secret.txt"
expect_fail "ghp token" "$FIXTURES/bad-ghp-token.txt"
expect_fail "gho token" "$FIXTURES/bad-gho-token.txt"
expect_fail "jwt" "$FIXTURES/bad-jwt.txt"

TMP_BLOCKED="$(mktemp -d)"
trap 'rm -rf "$TMP_BLOCKED"' EXIT
echo '{"note": "fixture"}' > "$TMP_BLOCKED/credentials.json"
expect_fail "blocked credentials.json" "$TMP_BLOCKED/credentials.json"

expect_pass "compose interpolation" "$FIXTURES/good-compose-interpolation.txt"
expect_pass "fake test fixtures" "$FIXTURES/good-fake-fixtures.txt"
expect_pass "docs ellipsis placeholders" "$FIXTURES/good-docs-ellipsis.txt"
expect_pass "lockfile pii skip" "$FIXTURES/good-lockfile-sample.lock"

echo "secret scan regression tests passed"
