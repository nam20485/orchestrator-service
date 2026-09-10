#!/bin/sh
set -e

# First-mount fixup: named volumes (caddy_data, caddy_config) are root-owned on
# first attach and may pre-date the non-root switch or a rebuild that re-assigned
# the caddy UID. Chown to caddy:caddy whenever the owner differs from the current
# caddy user (idempotent). Runs as root before the privilege drop.
CADDY_UID="$(id -u caddy 2>/dev/null || printf '%s\n' 0)"
for d in /data /config; do
  if [ -d "$d" ] && [ "$(stat -c %u "$d")" != "$CADDY_UID" ]; then
    chown -R caddy:caddy "$d" 2>/dev/null || echo "caddy-entrypoint: WARNING: chown $d failed (owner=$(stat -c %u "$d" 2>/dev/null || echo unknown))" >&2
  fi
done

# Drop to the non-root caddy user (su-exec is Alpine's gosu). CAP_NET_BIND_SERVICE
# for :80/:443 is granted via a file capability on /usr/bin/caddy (set at build),
# which the kernel reapplies at execve regardless of the caller's uid — so the
# privilege drop does not strip privileged-port binding. Fall back to direct exec
# if su-exec is unavailable.
if command -v su-exec >/dev/null 2>&1; then
  exec su-exec caddy "$@"
else
  echo "caddy-entrypoint: WARNING: su-exec not found, running as $(id -u)" >&2
  exec "$@"
fi
