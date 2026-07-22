#!/usr/bin/env bash
# Functional regression test for the webhook log-dir write permission.
#
# Reproduces the real trigger (Docker auto-creates a non-existent bind-mount
# source as root:root, exactly like ./traces/runner), then runs the REAL image
# with its REAL entrypoint (webhook-entrypoint.sh: chown -> gosu drop) and proves
# the non-root `app` user (UID 1000) can write under /tmp/orchestrator-webhook.
#
# This is a FUNCTIONAL test of the built artifact — it does NOT grep the
# Dockerfile. A stale image whose entrypoint is bare ["gosu","app"] (no chown)
# fails here, which is precisely the regression it guards.
#
# CI build-job only: it needs the built image (orchestrator-webhook:ci). Local
# validation (scripts/validate.ps1) intentionally does not build images, so this
# is not wired into -Test; it runs as a step in the validate.yml `build` job.
set -euo pipefail

fail() { echo "FAIL: $*" >&2; exit 1; }

IMG="${WEBHOOK_IMAGE:-orchestrator-webhook:ci}"
docker image inspect "$IMG" >/dev/null 2>&1 \
  || fail "image not found: $IMG (build it first, or run in the CI build job)"

# Reproduce the real trigger: a bind-mount source path Docker auto-creates as
# root:root. Do NOT pre-create $LOGDIR — the daemon creates it root-owned, the
# same way ./traces/runner gets created on first attach.
PARENT="$(mktemp -d)"
LOGDIR="$PARENT/runner"

cleanup() {
  # The container's entrypoint chowns the bind mount to UID 1000 (app), so the
  # CI runner user (a different UID) cannot `rm` the artifacts the container
  # created. The image runs as root by default (the entrypoint does the gosu
  # drop), so use it to restore ownership to the runner before the host cleanup.
  # Best-effort: a functional test must never fail CI on cleanup.
  if [ -n "${PARENT:-}" ]; then
    docker run --rm --entrypoint chown -v "$PARENT:/work" "$IMG" \
      -R "$(id -u):$(id -g)" /work >/dev/null 2>&1 || true
    rm -rf "$PARENT" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# Run the REAL image with the REAL entrypoint (no --entrypoint override). The
# entrypoint execs `gosu app "$@"`, so this CMD runs as UID 1000. Assert:
#   1. we actually dropped to UID 1000
#   2. app can write+read a file under the root-created mount
docker run --rm -v "$LOGDIR:/tmp/orchestrator-webhook" "$IMG" \
  sh -c '[ "$(id -u)" = 1000 ] && echo ok > /tmp/orchestrator-webhook/probe && [ "$(cat /tmp/orchestrator-webhook/probe)" = ok ]' \
  || fail "app (UID 1000) could not write to the root-owned mount — entrypoint chown is missing or broken in image $IMG"

# The chown must have propagated through the bind mount to the host dir.
[ "$(stat -c %u "$LOGDIR")" = 1000 ] \
  || fail "entrypoint did not chown the mount to UID 1000 (host owner uid=$(stat -c %u "$LOGDIR"))"

echo "webhook image non-root log-dir write: ok"
