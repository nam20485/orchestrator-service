#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="${ROOT}/image/.opencode/AGENTS.md"
AGENTS_DIR="${ROOT}/image/.opencode/agents"
PROMPT="${ROOT}/webhook_receiver/orchestration_prompt.jinja2.md"

[ -f "${AGENTS}" ] || { echo "missing ${AGENTS}" >&2; exit 1; }
[ -d "${AGENTS_DIR}" ] || { echo "missing ${AGENTS_DIR}" >&2; exit 1; }
[ -f "${PROMPT}" ] || { echo "missing ${PROMPT}" >&2; exit 1; }

# --- 5.3 Single-writer memory invariant ---

# AGENTS.md must document the single-writer (orchestrator-only write) invariant.
if ! grep -qi 'ORCHESTRATOR ONLY' "${AGENTS}"; then
  echo "AGENTS.md: missing ORCHESTRATOR ONLY write rule for persistent memory" >&2
  exit 1
fi

if ! grep -qi 'Memory Save Requests' "${AGENTS}"; then
  echo "AGENTS.md: missing Memory Save Requests hand-off contract" >&2
  exit 1
fi

# The stale mcp-memory-service claim must be corrected to server-memory.
if grep -qi 'mcp-memory-service' "${AGENTS}"; then
  echo "AGENTS.md: stale mcp-memory-service reference present (should be server-memory)" >&2
  exit 1
fi

# No non-orchestrator agent file may instruct memory WRITES.
# Writes: create_entities, create_relations, add_observations, delete_*.
WRITE_RE='create_entities|create_relations|add_observations|delete_entities|delete_observations|delete_relations'
found=0
for f in "${AGENTS_DIR}"/*.md; do
  base="$(basename "$f")"
  [ "$base" = "orchestrator.md" ] && continue
  if grep -Eq "$WRITE_RE" "$f"; then
    echo "subagent ${base}: must NOT instruct memory write tools (single-writer: orchestrator only)" >&2
    found=1
  fi
done
[ "$found" -eq 0 ] || exit 1

# Every non-orchestrator agent must declare memory READ-ONLY.
for f in "${AGENTS_DIR}"/*.md; do
  base="$(basename "$f")"
  [ "$base" = "orchestrator.md" ] && continue
  if ! grep -Eqi 'memory.*(READ-ONLY|read only)' "$f"; then
    echo "subagent ${base}: missing memory READ-ONLY declaration" >&2
    found=1
  fi
done
[ "$found" -eq 0 ] || exit 1

# The orchestrator agent must declare itself the sole memory writer.
if ! grep -Eqi 'sole.*(writer|memory)|memory.*sole writer' "${AGENTS_DIR}/orchestrator.md"; then
  echo "orchestrator.md: missing sole memory writer declaration" >&2
  exit 1
fi

# --- 5.4 Verified dispatch publish gate ---

# The dispatch clause must contain explicit push + PR verification language.
if ! grep -q 'git push -u origin' "${PROMPT}"; then
  echo "orchestration_prompt: dispatch clause missing 'git push -u origin'" >&2
  exit 1
fi

if ! grep -q 'gh pr list --head' "${PROMPT}"; then
  echo "orchestration_prompt: dispatch clause missing 'gh pr list --head' verification" >&2
  exit 1
fi

echo "memory-protocol: ok"
