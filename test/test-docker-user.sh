#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

# --- Static contract checks for non-root container execution -----------------
# Verifies the configuration the non-root runtime relies on. The CI `test` job
# does not build the project images (that's the separate `build` job), so we
# assert the Dockerfiles, compose files, and entrypoint scripts directly rather
# than running a container. A regression in any of these assumptions would let
# the pipeline pass while breaking non-root execution at runtime.

fail() { echo "FAIL: $*" >&2; exit 1; }

# 1. Compose must NOT set a runtime `user:` — the entrypoint owns the privilege
#    drop (gosu/su-exec). A runtime `user:` would bypass the drop and start as
#    an arbitrary numeric UID that cannot write the build-time-owned dirs.
export OPENCODE_SERVER_PASSWORD="FAKE-PASSWORD-FOR-TESTING"
export OS_WEBHOOK_SECRET="FAKE-WEBHOOK-SECRET-FOR-TESTING"
export ZAI_CODING_API_KEY="FAKE-KEY-FOR-TESTING-00000000"
export WEBHOOK_SITE_ADDRESS=":80"
export WORKSPACE_DIR="/tmp/test-workspace-$$"

user_keys="$(docker compose -f compose.yaml config --format json \
  | jq -c '[.services | to_entries[] | select(.value.user != null) | {service: .key, user: .value.user}]')"
[ "$user_keys" = "[]" ] || fail "compose.yaml sets a runtime 'user:' (must be omitted): ${user_keys}"
echo "compose.yaml has no runtime user: ok"

# 1b. Compose must set no-new-privileges for services using gosu/su-exec privilege
#     drop (defense-in-depth for root-starting entrypoints). webhook-proxy is exempt:
#     it relies on a file capability (setcap cap_net_bind_service=+ep) for privileged-
#     port binding, which no_new_privs blocks at execve (capabilities(7)).
#     cap_drop: ALL for services that need no capabilities (webhook-proxy exempt —
#     its root entrypoint needs SETUID/SETGID + CHOWN for su-exec drop + chown fixup).
nnps="$(docker compose -f compose.yaml config --format json \
  | jq -c '[.services | to_entries[] | select(.key != "webhook-proxy") | select((.value.security_opt // []) | index("no-new-privileges:true") == null) | {service: .key}]')"
[ "$nnps" = "[]" ] || fail "compose.yaml missing security_opt: no-new-privileges:true for (excluding webhook-proxy): ${nnps}"
echo "compose.yaml no-new-privileges: ok"

capdrops="$(docker compose -f compose.yaml config --format json \
  | jq -c '[.services | to_entries[] | select(.key != "webhook-proxy") | select((.value.cap_drop // []) | index("ALL") == null) | {service: .key}]')"
[ "$capdrops" = "[]" ] || fail "compose.yaml missing cap_drop: ALL for (excluding webhook-proxy): ${capdrops}"
echo "compose.yaml cap_drop: ok"

# 1c. Services with cap_drop: ALL must re-add the capabilities their root
#     entrypoint needs. All such services need SETUID+SETGID (gosu/su-exec drop)
#     and CHOWN (named-volume fixups). orchestratorservice additionally needs
#     DAC_OVERRIDE: its entrypoint writes auth.json and runs memory.jsonl
#     self-heal as root on files that are chowned to app after first start —
#     without DAC_OVERRIDE, root gets EACCES on restart.
required_caps="$(docker compose -f compose.yaml config --format json \
  | jq -c '[.services | to_entries[]
    | select((.value.cap_drop // []) | index("ALL") != null)
    | .key as $svc
    | (.value.cap_add // []) as $caps
    | (["SETUID","SETGID","CHOWN"] | map(select(. as $req | $caps | index($req) == null))) as $missing
    | select($missing | length > 0)
    | {service: $svc, missing_caps: $missing}]')"
[ "$required_caps" = "[]" ] || fail "compose.yaml cap_drop: ALL services missing required cap_add (SETUID/SETGID/CHOWN): ${required_caps}"

dac_override="$(docker compose -f compose.yaml config --format json \
  | jq -c '[.services | to_entries[]
    | select(.key == "orchestratorservice")
    | select((.value.cap_add // []) | index("DAC_OVERRIDE") == null)
    | {service: .key}]')"
[ "$dac_override" = "[]" ] || fail "compose.yaml orchestratorservice missing cap_add: DAC_OVERRIDE (entrypoint writes app-owned files as root on restart): ${dac_override}"
echo "compose.yaml cap_add for gosu/chown/dac_override: ok"

# 2. Main Dockerfile: no USER directive (entrypoint drops via gosu), declares
#    the APP_UID/APP_GID build args, and creates the app user.
grep -qE '^USER\b' Dockerfile && fail "Dockerfile must not set a USER directive (entrypoint owns the gosu drop)"
grep -qE '^ARG APP_UID=' Dockerfile || fail "Dockerfile missing 'ARG APP_UID='"
grep -qE '^ARG APP_GID=' Dockerfile || fail "Dockerfile missing 'ARG APP_GID='"
grep -qE 'useradd.*\bapp\b' Dockerfile || fail "Dockerfile missing app user creation"
echo "Dockerfile non-root contract: ok"

# 3. Caddy Dockerfile: no USER directive; creates a caddy user; root entrypoint.
grep -qE '^USER\b' deploy/caddy/Dockerfile && fail "deploy/caddy/Dockerfile must not set a USER directive (root entrypoint owns the su-exec drop)"
grep -qE 'adduser.*caddy' deploy/caddy/Dockerfile || fail "caddy Dockerfile missing caddy user creation"
grep -qE '^ENTRYPOINT' deploy/caddy/Dockerfile || fail "caddy Dockerfile missing root ENTRYPOINT"
echo "caddy Dockerfile non-root contract: ok"

# 4. Entrypoints contain the privilege-drop commands and named-volume chowns.
grep -q 'gosu app' scripts/docker-entrypoint.sh || fail "docker-entrypoint.sh missing 'gosu app' drop"
grep -q '/app/.memory' scripts/docker-entrypoint.sh || fail "docker-entrypoint.sh missing memory-dir chown"
grep -q 'WARNING: gosu not found' scripts/docker-entrypoint.sh || fail "docker-entrypoint.sh missing gosu-fallback warning"
grep -q 'WARNING: chown' scripts/docker-entrypoint.sh || fail "docker-entrypoint.sh missing chown-failure warning"
grep -q 'su-exec caddy' deploy/caddy/caddy-entrypoint.sh || fail "caddy-entrypoint.sh missing 'su-exec caddy' drop"
grep -qE 'for d in /data /config' deploy/caddy/caddy-entrypoint.sh || fail "caddy-entrypoint.sh missing /data+/config chown"
grep -q 'WARNING: su-exec not found' deploy/caddy/caddy-entrypoint.sh || fail "caddy-entrypoint.sh missing su-exec-fallback warning"
grep -q 'WARNING: chown' deploy/caddy/caddy-entrypoint.sh || fail "caddy-entrypoint.sh missing chown-failure warning"
# Webhook entrypoint must chown the root-owned runner-log bind mount to app
# before the gosu drop (Docker creates ./traces/runner as root:root on first
# attach; without this fixup the non-root app user cannot write runner logs).
grep -q 'gosu app' scripts/webhook-entrypoint.sh || fail "webhook-entrypoint.sh missing 'gosu app' drop"
grep -q '/tmp/orchestrator-webhook' scripts/webhook-entrypoint.sh || fail "webhook-entrypoint.sh missing log-dir chown"
grep -q 'WARNING: gosu not found' scripts/webhook-entrypoint.sh || fail "webhook-entrypoint.sh missing gosu-fallback warning"
grep -q 'WARNING: chown' scripts/webhook-entrypoint.sh || fail "webhook-entrypoint.sh missing chown-failure warning"
grep -qF 'ENTRYPOINT ["webhook-entrypoint.sh"]' Dockerfile.webhook || fail "Dockerfile.webhook must use webhook-entrypoint.sh as ENTRYPOINT"
echo "entrypoint privilege-drop logic: ok"

echo "docker-user: ok"
