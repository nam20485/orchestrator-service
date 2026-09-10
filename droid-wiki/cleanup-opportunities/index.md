# Cleanup Opportunities

Source-grounded, actionable items only — verified against `webhook_receiver/`, `tests/`, `coverage.xml`, and `.github/workflows/validate.yml` in `/home/nam20485/src/github/nam20485/orchestrator-service`. Nothing here is a guess about unused code or unverified stale dependencies.

- [Complexity Hotspots](complexity-hotspots.md) — the largest, highest-churn modules in `webhook_receiver/` and where their test coverage sits relative to the repo's stated floor.

## Testing-approach gap already tracked by the repo itself

`docs/testing-approach.md` documents an already-identified, still-open gap: several `test/test-*.sh` scripts (`test-docker-user.sh`, `test-webhook-scripts.sh`, `test-compose-config.sh`) are grep-over-source-text assertions that are labeled as if they verify runtime container behavior, when they cannot. The doc's own remediation checklist (section 5) is not yet fully executed:

1. Audit `test/test-*.sh` and classify each as static-guard vs. functional; only one functional image test currently exists (`test/test-webhook-image-entrypoint.sh`, wired into the CI `build` job).
2. Add at least one functional smoke test per runtime image for `orchestratorservice` and `orchestrator-caddy` (currently only `orchestrator-webhook` has one) — e.g. asserting the non-root user and that `scripts/docker-entrypoint.sh`'s memory-dir chown and `deploy/caddy/caddy-entrypoint.sh`'s `/data`+`/config` chown actually ran in the built image.
3. Add a negative-control CI step (or documented manual check) that builds a deliberately-broken variant of each functional test's target and confirms the test fails against it, per the doc's section 4.5.

This is cited here as an existing, first-party-documented action item rather than a new finding — the parent task's cleanup evidence should point contributors at this doc rather than duplicate it.
