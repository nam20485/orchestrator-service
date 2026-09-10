# Patterns and conventions

The repository separates pure, testable Python logic from process and container boundaries. Small modules provide strict input validation and serialization, while `webhook_receiver/runner.py`, `webhook_receiver/beads_loop.py`, and `webhook_receiver/workspace.py` own subprocess and filesystem effects.

Changes should preserve this split and retain the project’s fail-closed approach to public ingress, privileged dispatch, and dashboard access.

## Code structure

- Keep HTTP wiring in `webhook_receiver/app.py`; extract reusable behavior into adjacent modules such as `webhook_receiver/filters.py`, `webhook_receiver/prompts.py`, or `webhook_receiver/workspace.py`.
- Use the frozen `Settings` dataclass in `webhook_receiver/config.py` to centralize environment defaults. Tests construct explicit settings rather than relying on the host environment.
- Treat subprocess operations as integration boundaries. `webhook_receiver/workspace.py` and `webhook_receiver/beads_loop.py` capture output, use explicit working directories, and check return codes.
- Preserve best-effort operational actions where a failure must not crash the receiver. Examples include workspace refresh in `webhook_receiver/app.py` and GitHub failure comments in `webhook_receiver/runner.py`.

## Security boundaries

- Verify `X-Hub-Signature-256` before interpreting a delivery. `webhook_receiver/github.py` uses HMAC SHA-256 with constant-time comparison.
- Keep the webhook trigger gate narrow. `webhook_receiver/filters.py` accepts only the expected issue-label path, ignores bot actors, and fail-closes direct-body dispatch when no trusted sender list exists.
- Validate user-controlled path fragments and log stems before filesystem access. `webhook_receiver/app.py`, `webhook_receiver/workspace.py`, and `webhook_receiver/dashboard.py` each apply containment or character allowlists.
- Do not copy credentials into source, test fixtures, wiki pages, or logs. `scripts/validate.ps1` invokes the changed-file secret scan, and `test/fixtures/` uses synthetic values only.

## Runtime and concurrency

- The receiver schedules dispatch after returning HTTP 202. Long-running subprocess work belongs outside the request handler.
- `EventStore` is process-local by design. Do not present it as durable storage; `WebhookStore` and run files cover the persistent observability path.
- Protect shared loop state with locks. `webhook_receiver/beads_loop.py` returns copied state to callers instead of exposing mutable internals.
- The memory graph is a single-writer resource. The shipped agent instructions in `image/.opencode/AGENTS.md` assign writes to the orchestrator and require subagents to hand back durable facts.

## Documentation and source references

Use full repository-root paths in documentation, such as `webhook_receiver/watchdog.py`, not bare filenames. This keeps generated code links unambiguous. Explain the behavior that exists now; historical plans under `plan_docs/.archived/` and `plan_docs/.deferred/` are not current implementation references.

## Related pages

- [Development workflow](development-workflow.md) describes the branch-to-validation cycle.
- [Security](../security.md) maps these patterns to the public and agent-execution trust boundaries.
- [Testing](testing.md) explains the test layers that enforce the conventions.
