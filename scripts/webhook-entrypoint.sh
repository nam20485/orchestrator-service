#!/bin/sh
set -e

# First-mount fixup: the runner/beads log bind mount (/tmp/orchestrator-webhook,
# host ${WEBHOOK_LOG_DIR:-./traces/runner}) is created root:root by Docker on
# first attach. The app user (UID 1000) writes runner prompts, stdout/stderr,
# and bead-* artifacts here, so chown to app whenever the owner differs from
# the current app user (idempotent — no-op on subsequent starts). Runs as root
# before the gosu drop, mirroring scripts/docker-entrypoint.sh's memory-dir fixup.
LOG_DIR="/tmp/orchestrator-webhook"
APP_UID="$(id -u app 2>/dev/null || printf '%s\n' 0)"
if [ -d "$LOG_DIR" ] && [ "$(stat -c %u "$LOG_DIR")" != "$APP_UID" ]; then
  chown -R app:app "$LOG_DIR" 2>/dev/null || echo "webhook-entrypoint: WARNING: chown $LOG_DIR failed (owner=$(stat -c %u "$LOG_DIR" 2>/dev/null || echo unknown))" >&2
fi

# Privilege drop: start as root (no USER directive in Dockerfile), then exec
# the server as the non-root app user via gosu. Fall back to direct exec if
# gosu is not available (e.g. when running the entrypoint test on the host).
if command -v gosu >/dev/null 2>&1; then
  exec gosu app "$@"
else
  echo "webhook-entrypoint: WARNING: gosu not found, running as $(id -u)" >&2
  exec "$@"
fi
