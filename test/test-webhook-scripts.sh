#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

# prompt.ps1 dot-sources init-project-workspace.ps1 (Resolve-ProjectWorkspace /
# Initialize-ProjectWorkspace). The webhook image dispatches the beads loop via
# prompt.ps1, so it must COPY both files into /app/scripts/ for the in-container
# dot-source to resolve. Without the helper, the beads worktree hand-off fails.

fail=0

check_contains() {
    local file="$1" needle="$2" label="$3"
    if ! grep -qF "$needle" "$file"; then
        echo "FAIL: $file missing $label ($needle)" >&2
        fail=1
    fi
}

check_contains "Dockerfile.webhook" "scripts/prompt.ps1" "prompt.ps1 COPY"
check_contains "Dockerfile.webhook" "scripts/init-project-workspace.ps1" "init-project-workspace.ps1 COPY"
check_contains "Dockerfile.webhook" "scripts/webhook-entrypoint.sh" "webhook-entrypoint.sh COPY"
# The webhook image only COPYs scripts re-included by .dockerignore (scripts/*
# is ignored). Grep the re-include lines so a future regression that drops one
# fails here, instead of only failing at `docker build` time with
# "file not found or excluded by .dockerignore".
check_contains ".dockerignore" "!scripts/prompt.ps1" ".dockerignore re-include prompt.ps1"
check_contains ".dockerignore" "!scripts/init-project-workspace.ps1" ".dockerignore re-include init-project-workspace.ps1"
check_contains ".dockerignore" "!scripts/webhook-entrypoint.sh" ".dockerignore re-include webhook-entrypoint.sh"

if [ "$fail" -ne 0 ]; then
    echo "webhook image scripts: FAIL" >&2
    exit 1
fi

echo "webhook image scripts: ok"
