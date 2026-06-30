#!/usr/bin/env bash
# scripts/git-trust.sh
#
# Shared git-trust setup used by both Dockerfile and Dockerfile.webhook so the
# rationale lives in exactly one place.
#
# Marks all repositories as safe for git so root-running containers do not
# refuse operations on bind-mounted repos with "fatal: detected dubious
# ownership" (CVE-2022-24765). The containers only ever operate on the
# /workspace bind mount, whose per-session project dirs are created at runtime
# by the host (UID 1000) — paths git cannot know at build time. safe.directory
# is NOT recursive, so enumerating only /workspace would not cover
# /workspace/<project>/; '*' is the git-documented pattern for root-running
# containers on bind-mounted repos of arbitrary owner.
set -euo pipefail
git config --global --add safe.directory '*'
