#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

# OpenCode pin + dispatch-flag consistency (PR #49 review hardening).
# - The server (Dockerfile) and the attach client (Dockerfile.webhook) must
#   run the same opencode version; client/server skew changes run/serve flag
#   and log-format behavior the watchdog/runner parse.
# - scripts/prompt.ps1 must NOT pass --auto: permission policy is the
#   fail-closed server-side opencode.json block; --auto would silently
#   auto-approve a default `ask` if that config ever failed to load.

fail=0

ver_main="$(grep -oE '^ARG OPENCODE_VERSION=[0-9]+\.[0-9]+\.[0-9]+' Dockerfile | head -1 | cut -d= -f2 || true)"
ver_webhook="$(grep -oE '^ARG OPENCODE_VERSION=[0-9]+\.[0-9]+\.[0-9]+' Dockerfile.webhook | head -1 | cut -d= -f2 || true)"

if [ -z "${ver_main}" ] || [ -z "${ver_webhook}" ]; then
    echo "FAIL: could not read OPENCODE_VERSION from both Dockerfiles (main='${ver_main}' webhook='${ver_webhook}')" >&2
    fail=1
elif [ "${ver_main}" != "${ver_webhook}" ]; then
    echo "FAIL: OPENCODE_VERSION skew — Dockerfile=${ver_main}, Dockerfile.webhook=${ver_webhook}" >&2
    fail=1
fi

# Matches the flag actually being appended to $runArgs ("--auto" as a quoted
# string); explanatory comments mentioning --auto are fine.
if grep -qF '"--auto"' scripts/prompt.ps1; then
    echo "FAIL: scripts/prompt.ps1 must not pass --auto (fail-closed permission policy)" >&2
    fail=1
fi

if [ "$fail" -ne 0 ]; then
    echo "opencode pin consistency: FAIL" >&2
    exit 1
fi

echo "opencode pin consistency: ok (v${ver_main}, no --auto)"
