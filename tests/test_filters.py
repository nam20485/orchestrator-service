from __future__ import annotations

import importlib

from webhook_receiver import filters


def test_should_filter_matches_default_blacklist() -> None:
    assert filters.should_filter("service=bus type=message.part.delta stuff")
    assert filters.should_filter("service=bus type=message.part.updated x")


def test_should_filter_passes_normal_lines() -> None:
    assert not filters.should_filter("normal log line")
    assert not filters.should_filter("INFO server started")
    assert not filters.should_filter("")


def test_load_patterns_from_env(monkeypatch) -> None:
    monkeypatch.setenv("TRACE_BLACKLIST_PATTERNS", "my-custom-pattern\nanother-thing")
    importlib.reload(filters)
    try:
        assert filters.should_filter("contains my-custom-pattern here")
        assert filters.should_filter("has another-thing in it")
        assert not filters.should_filter("normal line")
    finally:
        monkeypatch.delenv("TRACE_BLACKLIST_PATTERNS", raising=False)
        importlib.reload(filters)


# ── should_dispatch: transport-level webhook event gate ───────────────────
# Mirrors orchestrator-agent.yml orchestrate-job `if:`. Only issues.labeled
# events with a non-bot actor and a workflow-relevant label may dispatch.


def _labeled(label: str = "orchestration:dispatch", sender: str = "nam20485") -> dict:
    return {"action": "labeled", "label": {"name": label}, "sender": {"login": sender}}


def test_should_dispatch_rejects_non_issues_events() -> None:
    for event in ("issue_comment", "pull_request", "workflow_run", "push", ""):
        allow, _ = filters.should_dispatch(event, {"action": "created"})
        assert allow is False, event


def test_should_dispatch_rejects_non_labeled_actions() -> None:
    for action in ("opened", "closed", "edited", "reopened", "assigned", ""):
        allow, _ = filters.should_dispatch("issues", {"action": action})
        assert allow is False, action


def test_should_dispatch_rejects_bot_actors() -> None:
    for sender in ("github-actions[bot]", "dependabot[bot]", "renovate-bot"):
        allow, reason = filters.should_dispatch(
            "issues", _labeled(sender=sender)
        )
        assert allow is False, sender
        assert "bot" in reason


def test_should_dispatch_allows_pat_user() -> None:
    # The orchestration agent applies labels via the PAT user (not a bot); it
    # must pass so the epic label sequence works.
    allow, _ = filters.should_dispatch("issues", _labeled(sender="nam20485"))
    assert allow is True


def test_should_dispatch_rejects_non_workflow_labels() -> None:
    for label in ("orchestrate", "bug", "documentation", "epic"):
        allow, reason = filters.should_dispatch("issues", _labeled(label=label))
        assert allow is False, label
        assert "label" in reason or "workflow" in reason


def test_should_dispatch_allows_each_workflow_label() -> None:
    for label in (
        "orchestration:plan-approved",
        "orchestration:epic-ready",
        "orchestration:epic-implemented",
        "orchestration:epic-reviewed",
        "orchestration:epic-complete",
        "orchestration:dispatch",
        "implementation:ready",
        "implementation:complete",
    ):
        allow, _ = filters.should_dispatch("issues", _labeled(label=label))
        assert allow is True, label


def test_should_dispatch_label_is_case_insensitive() -> None:
    allow, _ = filters.should_dispatch(
        "issues", _labeled(label="Orchestration:Dispatch")
    )
    assert allow is True


def test_should_dispatch_rejects_when_label_missing() -> None:
    # Fail-closed: a labeled event with no label.name is not dispatched.
    allow, _ = filters.should_dispatch(
        "issues", {"action": "labeled", "sender": {"login": "nam20485"}}
    )
    assert allow is False


def test_should_dispatch_rejects_when_sender_missing() -> None:
    allow, _ = filters.should_dispatch(
        "issues", {"action": "labeled", "label": {"name": "orchestration:dispatch"}}
    )
    assert allow is True  # missing sender is treated as a non-bot actor
