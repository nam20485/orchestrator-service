# Memory — detailed usage guide

Memory is a persistent knowledge-graph store (`@modelcontextprotocol/server-memory`) that survives across sessions and chats. Its data model is three primitives:

- **Entities** — typed nodes with a unique `name`, a specific `entityType`, and a list of `observations`.
- **Observations** — discrete string facts attached to an entity. **One fact per observation.**
- **Relations** — directed edges (`from` → `to`) with a `relationType`, always stored in **active voice**.

Use Memory for **durable, reusable context**, not transient scratch state (which belongs in TODO lists/chat).

## Single-writer invariant (critical)

`@modelcontextprotocol/server-memory` writes the entire `memory.jsonl` via an unprotected
`writeFile` on every mutation. Each OpenCode **session** (the orchestrator plus each delegated
subagent) spawns its own server-memory process against the one `MEMORY_FILE_PATH`. When two or
more sessions write concurrently, the `writeFile` streams interleave and the file becomes
unparseable (`Unexpected non-whitespace character after JSON`).

**Rule:** the Orchestrator is the **sole writer**. Only it may call the write tools:

| Reads (ALL agents) | Writes (ORCHESTRATOR ONLY) |
|--------------------|----------------------------|
| `search_nodes`     | `create_entities`          |
| `open_nodes`       | `create_relations`         |
| `read_graph`       | `add_observations`         |
|                    | `delete_entities`          |
|                    | `delete_observations`      |
|                    | `delete_relations`         |

Subagents and specialists are **READ-ONLY**. Instead of writing, each subagent ends its result
with a `## Memory Save Requests` list of durable facts; the Orchestrator reads that list and
persists the facts itself. Reads never corrupt the file, so read-many is safe — eliminating
concurrent *writers* removes the corruption at its source.

A subagent calling a write tool is a **critical protocol violation**.

### Hand-off format example

A subagent's result includes:

```markdown
## Memory Save Requests
- Entity: project-foo | Type: microservice | Observation: "uses PostgreSQL 16 on host db.internal:5432"
- Add observation to issue-42: "root cause was missing index on users.email"
- Create relation: ServiceA depends-on ServiceB
```

If the subagent has nothing to persist, the section reads `(none)` or is omitted.

### Startup self-heal

`scripts/docker-entrypoint.sh` checks `/app/.memory/memory.jsonl` on container start. If it is
unparseable (e.g. from a prior concurrent-write corruption), the corrupt file is moved to
`memory.jsonl.corrupt-<timestamp>` and a fresh empty one is created so reads stop failing.

## When to store

- Store durable facts: entity attributes, project/repository structure, decisions **and their rationale**, cross-component relationships, ownership, locations/paths/URLs/IDs, stable conventions.
- Do **not** store secrets, credentials, tokens, or PII — the store is a plaintext local file.
- Do **not** store large blobs, logs, or full file contents — store a reference (path/URL) instead.
- Do **not** dump chat transcripts or transient task progress — keep the graph high-signal.

## Search before create (de-duplicate)

- Before creating any entity, call `search_nodes` (fuzzy match) and/or `open_nodes` (exact name) to find existing matches.
- Prefer adding observations to an existing entity (`add_observations`) over creating a duplicate — `create_entities` silently ignores names that already exist, so a duplicate create loses the new observations.
- Before creating a relation, confirm both endpoints exist; `create_relations` skips exact duplicates automatically.

## Writing good observations and relations

- Make each observation **atomic and self-contained** — it should make sense read in isolation.
- Be **specific and concrete**: include values, IDs, versions, paths, URLs (e.g. `"uses PostgreSQL 16, host db.internal:5432"` beats `"uses a database"`).
- Avoid vague judgements like `"is important"` — state the constraint or reason instead.
- Use **active voice** for `relationType` (`"ServiceA calls ServiceB"`, not `"ServiceB is called by ServiceA"`) and reuse consistent verb phrases across the graph.

## Entity naming and types

- Use **unique, stable entity names** (the name is the identifier; renaming is not supported — delete and recreate).
- Use **specific, consistent `entityType` values** (e.g. `microservice`, `cli-tool`, `adr`, `team`) rather than a generic `thing`.

## Maintenance

- When a fact changes or is contradicted, **delete the stale observation** (`delete_observations`) and add the corrected one.
- Remove obsolete entities (`delete_entities` cascades to their relations) and obsolete edges (`delete_relations`).
- Keep the graph tidy; don't let it bloat with low-value noise.
