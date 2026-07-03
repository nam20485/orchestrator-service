#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

# Beads version pins (single source of truth: Dockerfile.beads).
# These are immutable commit SHAs; the version comment tracks the upstream tag.
BR_REV="d9f8d7083dee46d04a8e4741c5f535eb7fcabc97"      # beads_rust v0.2.15
BVR_REV="e4506f63214d32c8bcac4f29479a9b80cb932a6a"     # beads_viewer_rust v0.2.1
RUST_BASE="rust:1.95-slim-bookworm"

fail=0

check_contains() {
    local file="$1" needle="$2" label="$3"
    if ! grep -qF "$needle" "$file"; then
        echo "FAIL: $file missing $label ($needle)" >&2
        fail=1
    fi
}

# --- br SHA must be present in every build site ---
# (SKILL.md embeds the SHA as a fallback install command for agents)
for f in Dockerfile.beads Dockerfile Dockerfile.webhook scripts/install-dev-tools.ps1 image/.opencode/skills/plan-to-beads/SKILL.md; do
    check_contains "$f" "$BR_REV" "br SHA"
done

# --- bvr SHA must be present everywhere bvr is built ---
# (Dockerfile intentionally omits bvr — it only needs br)
for f in Dockerfile.beads Dockerfile.webhook scripts/install-dev-tools.ps1; do
    check_contains "$f" "$BVR_REV" "bvr SHA"
done

# --- Rust base image must be consistent across all Dockerfiles ---
for f in Dockerfile.beads Dockerfile Dockerfile.webhook; do
    check_contains "$f" "$RUST_BASE" "Rust base image"
done

if [ "$fail" -ne 0 ]; then
    echo "beads versions consistency: FAIL" >&2
    exit 1
fi

echo "beads versions consistency: ok"
