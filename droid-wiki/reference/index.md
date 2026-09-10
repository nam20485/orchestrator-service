# Reference

This section is the lookup layer beneath the narrative pages in Overview and How to contribute. It catalogs the concrete configuration surface, the in-memory and persisted data shapes the runtime creates, and the dependency stack (Python packages, container base images, CLIs, and external services) that `orchestrator-service` is built from.

## Pages

| Page | Covers |
| --- | --- |
| [Configuration](configuration.md) | Compose environment wiring, the `webhook_receiver/config.py` `Settings` dataclass, Docker build arguments, and the non-secret `image/.opencode/opencode.json` knobs — grouped by category with code/compose defaults. |
| [Data models](data-models.md) | The persisted records (run manifest sidecars, the webhook store) and in-memory-only structures (`EventStore`, `WatchdogState`/`WatchdogConfig`/`WatchdogResult`) the runtime creates, plus the narrative structure synthesized from run logs. |
| [Dependencies](dependencies.md) | Python packages (`pyproject.toml`/`uv.lock`), container base images and installed CLIs (`Dockerfile`, `Dockerfile.webhook`), OpenCode-level integrations (MCP servers, model providers), and the external services the runtime talks to at runtime. |

## Scope

These pages document what exists in the checked-in configuration and source files — categories, field names, defaults, and roles. They intentionally do **not** reproduce secret values or the live/current state of any host or CI environment; for the exhaustive variable-by-variable reference (including required-vs-optional status) see `docs/environment-variables.md` in the repository root.

## Related pages

- [Architecture](../overview/architecture.md) describes the runtime layers and data paths that this configuration and these data models support.
- [Glossary](../overview/glossary.md) defines the terms (Bead, Dispatch, Manifest, Watchdog, …) used throughout these tables.
- [Getting started](../overview/getting-started.md) walks through exercising this configuration locally.
- [Patterns and conventions](../how-to-contribute/patterns-and-conventions.md) explains the security and concurrency rules that shape several of these data models (fail-closed ingress, single-writer memory graph, process-local `EventStore`).
