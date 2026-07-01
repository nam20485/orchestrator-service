#!/bin/sh
set -e

# First-mount fixup: named volumes (caddy_data, caddy_config) are root-owned on
# first attach and may pre-date the non-root switch. Chown to caddy:caddy if
# still root-owned (idempotent). Runs as root before the privilege drop.
for d in /data /config; do
  if [ -d "$d" ] && [ "$(stat -c %u "$d")" = "0" ]; then
    chown -R caddy:caddy "$d" 2>/dev/null || true
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
  exec "$@"
fi
