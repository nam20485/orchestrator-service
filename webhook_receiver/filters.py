from __future__ import annotations

import os
import re

_DEFAULT_BLACKLIST: list[str] = [
    # OpenCode bus message-part churn (high-frequency, zero signal).
    r"service=bus\s+type=message\.part\.delta",
    r"service=bus\s+type=message\.part\.updated",
    # Per-run boilerplate emitted identically on every tool call / loop step /
    # config probe. ~73% of a run log is pure repetition from these patterns.
    # See traces/gap-miner-v2-lima63-log-noise-analysis.md (only the "X/remove"
    # categories are listed here; high-signal lines such as "exiting loop" and
    # all ERROR/WARN levels are intentionally kept).
    r"evaluated permission=.*action\.action=allow",  # 1: permission-always-allowed
    r"message=tracking hash=",  # 2: unchanged-workspace hash echo
    r"message=loop .*step=",  # 3: bare loop counter (does NOT match "exiting loop")
    r"message=stream .*modelID=",  # 4: per-call provider/model restatement
    r'"llm runtime selected"',  # 5: duplicate of #4 (runtime is always ai-sdk)
    r"message=process .*messageID=",  # 6: opaque per-message ID churn
    r'"touching file"',  # 7: internal file-access bookkeeping
    r"^$",  # 10: interleaved blank lines
    r"message=loading path=",  # 11: config-file probe (mostly not-found)
    r"created id=ses",  # 12: session-creation with giant perm JSON blob
]


def _load_patterns() -> list[re.Pattern[str]]:
    raw = os.environ.get("TRACE_BLACKLIST_PATTERNS", "")
    if raw.strip():
        patterns = [p.strip() for p in raw.split("\n") if p.strip()]
    else:
        patterns = _DEFAULT_BLACKLIST
    return [re.compile(p) for p in patterns]


_PATTERNS: list[re.Pattern[str]] = _load_patterns()


def should_filter(line: str) -> bool:
    """Return True if *line* matches any blacklisted trace pattern."""
    return any(p.search(line) for p in _PATTERNS)


# ── Transport-level webhook dispatch gate ─────────────────────────────────
# Hardcoded replica of the GitHub Actions ``orchestrator-agent.yml``
# orchestrate-job ``if:`` guard. The webhook-receiver must only spawn the
# orchestrator agent for events the prompt's match-clause state machine can
# actually handle; otherwise the ``(default)`` clause posts a comment, which
# generates a fresh webhook and cascades into an echo-loop (see
# traces/gap-miner-v2-juliet79-analysis.md). This is the single source of
# truth for which webhook deliveries may dispatch the agent.

_EVENT_ALLOW: set[str] = {"issues"}
_ACTION_ALLOW: set[str] = {"labeled"}
# Both colon-prefixed namespaces are dispatch-trigger label spaces: every
# ``orchestration:*`` and ``gh-issue-tracking:*`` label maps to a match clause
# in orchestration_prompt.jinja2.md. The gh-issue-tracking hierarchy taxonomy
# uses BARE names (plan/epic/story/task — see skill labels.json), so these
# prefixes never collide with organizational labels.
_LABEL_PREFIXES: tuple[str, ...] = ("orchestration:", "gh-issue-tracking:")
_LABEL_EXACT: set[str] = {"implementation:ready", "implementation:complete"}


def _is_workflow_label(name: str) -> bool:
    n = (name or "").strip().lower()
    return any(n.startswith(p) for p in _LABEL_PREFIXES) or n in _LABEL_EXACT


def _is_bot_actor(login: str) -> bool:
    """Return True for GitHub App / automation actors (``*[bot]``, ``*-bot``)."""
    n = (login or "").strip().lower()
    return n.endswith("[bot]") or n.endswith("-bot")


def should_dispatch(event: str, payload: dict) -> tuple[bool, str]:
    """Decide whether a webhook delivery may dispatch the orchestrator agent.

    Returns ``(True, "allowed")`` for the exact set the prompt match clauses
    expect: ``issues.labeled`` by a non-bot actor with a workflow-relevant
    label. Anything else returns ``(False, "<reason>")`` so the caller can log
    and return ``202 ignored`` without spawning the agent.
    """
    event = (event or "").lower()
    action = str((payload or {}).get("action") or "").lower()
    if event not in _EVENT_ALLOW:
        return False, f"event {event!r} not dispatched (only issues)"
    if action not in _ACTION_ALLOW:
        return False, f"{event}.{action!r} not dispatched (only labeled)"

    sender = str((payload.get("sender") or {}).get("login") or "")
    if _is_bot_actor(sender):
        return False, f"bot actor {sender!r} skipped (anti-loop)"

    label_name = str((payload.get("label") or {}).get("name") or "")
    if not _is_workflow_label(label_name):
        return False, f"label {label_name!r} not workflow-relevant"
    return True, "allowed"
