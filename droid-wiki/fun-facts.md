# Fun facts

Curious, verifiable observations about `orchestrator-service` — the GitHub-Actions
+ webhook-driven AI orchestration runtime that dispatches OpenCode agent sessions.
Each fact was mined from the live git history and source tree at
`/home/nam20485/src/github/nam20485/orchestrator-service`. For raw counts and a
broader statistical view, see [by-the-numbers.md](by-the-numbers.md); for the
stories behind the code, see [lore.md](lore.md).

## 1. The whole repo started as a single `.gitignore`

The initial commit `97f65c1` ("Initial commit", 2026-05-27) added **exactly one
file**: `.gitignore`,
all 218 lines of it. No README, no source, no `pyproject.toml` — just a
comprehensive Python/IDE ignore list. The actual project scaffolding
(`README.md`, `pyproject.toml`, `uv.lock`, `.python-version`) only landed in the
third commit `ba5e614` two minutes later at 03:29:41. Someone wanted the ignore
rules in place before a single tracked file existed.

## 2. Oldest surviving survivors are all from minute 03:29

The longest-lived files still in the tree today were all born within a ~10-second
window on 2026-05-27 03:29:41:

- `README.md`
- `pyproject.toml`
- `uv.lock`
- `.python-version`
- `image/.opencode/agents/.gitkeep`

The `.gitkeep` for `image/.opencode/agents/` is a quiet hint that the agent
roster directory existed as an empty placeholder before any agent definition
files were written — those (`code-reviewer.md`, `developer.md`,
`orchestrator.md`, `planner.md`, plus `odbplusplus-expert.md` and
`github-expert.md`) didn't appear until 2026-06-01 06:52:37.

## 3. The dependency diet: three runtime packages, period

`pyproject.toml` declares
exactly **three** runtime dependencies — `fastapi>=0.136.3`, `jinja2>=3.1`, and
`uvicorn[standard]>=0.48.0`. That is the entire production surface for a service
that validates GitHub App webhooks, renders Jinja2 orchestration prompts, and
runs a background Beads DAG loop. Everything else (`pytest`, `pytest-cov`,
`httpx`, `ruff`) lives under the `dev` dependency group. For a system that
orchestrates a multi-repo "software factory," the import bill is remarkably
small.

## 4. Zero TODO/FIXME debt in the source tree

A grep across `webhook_receiver/`, `scripts/`, `tests/`, `image/`, and `test/`
for `TODO|FIXME|XXX|HACK` returns **no matches** in actual source files (the
only hits are instructional mentions of "TODO lists" in agent prompt Markdown).
Either the codebase is unusually disciplined, or the team religiously resolves
markers before commit — either way, there is no lurking "come back later" debt
to excavate.

## 5. The longest source files are the operational heart

The five longest non-test, non-doc source files are exactly the modules that do
the real work, in descending order of lines:

| Lines | File |
|-------|------|
| 948 | `webhook_receiver/dashboard.py` |
| 850 | `webhook_receiver/runner.py` |
| 808 | `webhook_receiver/watchdog.py` |
| 738 | `webhook_receiver/beads_loop.py` |

The `dashboard.py` (948 lines) and `beads_loop.py` (738 lines) — the Beads DAG
poller and the observability dashboard — outweigh the runner and watchdog, a
telling sign that *observing* the orchestrator grew to be as much code as
*driving* it. The corresponding test files dwarf their subjects:
`tests/test_dashboard.py` is 1,268 lines and `tests/test_beads_loop.py` is 940,
making the test suite the single largest body of code in the repo.

## 6. Codenames in the traces directory

`traces/` is a graveyard
of named experiment runs, each suffixed with a NATO-phonetic codename plus a
two-digit serial: `gap-miner-v2-alpha61`, `…-delta48`, `…-golf38`,
`…-juliet79`, `…-lima63`. Per `AGENTS.md`, each codename is a distinct project
instance stamped out by the `workflow-launch2` factory — useful for A/B
comparisons such as the "glm-4.7-vs-glm-5 delegation experiment." The traces
directory is, in effect, a lab notebook of model and delegation trials.

## 7. `Dockerfile.beads` is the canonical version source for two other files

`Dockerfile.beads`
opens with a comment declaring itself the "single source of truth for beads
versions": both `Dockerfile` and `Dockerfile.webhook` are expected to mirror its
`rust-builder` stage verbatim and `COPY --from` the `br` + `bvr` binaries. In CI,
the `rust-builder` stage in those sibling Dockerfiles is overridden with the
published `Dockerfile.beads` image so Rust is compiled exactly once across the
whole pipeline. A single, comment-anchored contract keeps three Dockerfiles in
sync — a neat piece of dependency archaeology where the build graph is
documented in a comment header rather than a tool.
