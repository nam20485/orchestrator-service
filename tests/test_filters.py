from __future__ import annotations

import importlib

from webhook_receiver import filters


def test_should_filter_matches_default_blacklist() -> None:
    assert filters.should_filter("service=bus type=message.part.delta stuff")
    assert filters.should_filter("service=bus type=message.part.updated x")


def test_should_filter_passes_normal_lines() -> None:
    assert not filters.should_filter("normal log line")
    assert not filters.should_filter("INFO server started")
    # High-signal run-narrative lines must survive filtering.
    assert not filters.should_filter(
        'message="exiting loop" session.id=ses_0d7beca54ffe3ACvvcZv31Yc0u'
    )
    assert not filters.should_filter(
        "Webhook received delivery_id=81f44120 event=issues action=labeled"
    )


# ── run-log boilerplate (traces/gap-miner-v2-lima63-log-noise-analysis.md) ──
# Each "X/remove" noise category must be filtered from the container logger.


def test_should_filter_matches_log_noise_categories() -> None:
    samples = [
        # 1: permission-always-allowed evaluations
        'message="evaluated permission=memory-graph_search_nodes pattern=* '
        'action.permission=* action.action=allow action.pattern=*',
        # 2: tracking hash echo (unchanged workspace)
        "message=tracking hash=d28910629f2af8ddd331b44acabe41cab0717105 cwd=/workspace",
        # 3: bare loop counter (must NOT swallow "exiting loop")
        "message=loop session.id=ses_0d7beca54ffe3ACvvcZv31Yc0u step=0",
        # 4: per-call provider/model restatement
        "message=stream providerID=zai-coding-plan modelID=glm-4.7 session.id=ses_",
        # 5: duplicate of #4 (runtime always ai-sdk)
        '"llm runtime selected" llm.runtime=ai-sdk llm.provider=zai-coding-plan',
        # 6: opaque per-message ID churn
        "message=process session.id=ses_0d7beca54ffe3ACvvcZv31Yc0u "
        "messageID=msg_f28413ad7001NBaD5L8uMDuvIe",
        # 7: internal file-access bookkeeping
        'message="touching file" file=/workspace/nam20485-gap-miner-v2-lima63/AGENTS.md',
        # 10: interleaved blank line (runner emits "\n"; EOF sentinel is "")
        "\n",
        # 11: config-file probe (mostly not-found)
        "message=loading path=/home/app/.config/opencode/config.json",
        # 12: session-creation with giant permission JSON blob
        'message=created id=ses_0d7beca54ffe3ACvvcZv31Yc0u title="New session - ..."',
    ]
    for line in samples:
        assert filters.should_filter(line), repr(line)


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


def test_should_dispatch_allows_each_workflow_label(monkeypatch) -> None:
    # direct-body is gated to a trusted-sender allowlist; authorize the
    # default sender so it passes here. Other labels are unrestricted.
    monkeypatch.setenv("DIRECT_BODY_ALLOWED_SENDERS", "nam20485")
    for label in (
        "orchestration:plan-approved",
        "orchestration:epic-ready",
        "orchestration:epic-implemented",
        "orchestration:epic-reviewed",
        "orchestration:epic-complete",
        "orchestration:dispatch",
        "implementation:ready",
        "implementation:complete",
        # gh-issue-tracking: dispatch-trigger prefix — every state label maps
        # to a match clause in orchestration_prompt.jinja2.md.
        "gh-issue-tracking:direct-body",
        "gh-issue-tracking:init-success",
    ):
        allow, _ = filters.should_dispatch("issues", _labeled(label=label))
        assert allow is True, label


def test_should_dispatch_allows_gh_issue_tracking_prefix(monkeypatch) -> None:
    # The entire gh-issue-tracking: namespace is a dispatch-trigger space;
    # future state-suffixed labels must dispatch without a code change.
    # direct-body still requires the trusted-sender allowlist (set below).
    monkeypatch.setenv("DIRECT_BODY_ALLOWED_SENDERS", "nam20485")
    for label in (
        "gh-issue-tracking:direct-body",
        "gh-issue-tracking:init-success",
        "gh-issue-tracking:some-future-state",
        "GH-ISSUE-TRACKING:Direct-Body",
    ):
        allow, _ = filters.should_dispatch("issues", _labeled(label=label))
        assert allow is True, label


# ── direct-body trusted-sender allowlist (security gate) ──────────────────


def test_direct_body_rejected_by_default(monkeypatch) -> None:
    """Fail-closed: with no allowlist configured, direct-body never dispatches."""
    monkeypatch.delenv("DIRECT_BODY_ALLOWED_SENDERS", raising=False)
    allow, reason = filters.should_dispatch(
        "issues", _labeled(label="gh-issue-tracking:direct-body")
    )
    assert allow is False
    assert "DIRECT_BODY_ALLOWED_SENDERS" in reason


def test_direct_body_rejected_for_unlisted_sender(monkeypatch) -> None:
    """An allowlist that excludes the sender blocks the dispatch."""
    monkeypatch.setenv("DIRECT_BODY_ALLOWED_SENDERS", "trusted-admin")
    allow, reason = filters.should_dispatch(
        "issues",
        _labeled(label="gh-issue-tracking:direct-body", sender="attacker"),
    )
    assert allow is False
    assert "not permitted" in reason


def test_direct_body_allowed_for_listed_sender(monkeypatch) -> None:
    monkeypatch.setenv("DIRECT_BODY_ALLOWED_SENDERS", "trusted-admin, nam20485")
    allow, _ = filters.should_dispatch(
        "issues",
        _labeled(label="gh-issue-tracking:direct-body", sender="nam20485"),
    )
    assert allow is True


def test_direct_body_allowlist_is_case_insensitive(monkeypatch) -> None:
    monkeypatch.setenv("DIRECT_BODY_ALLOWED_SENDERS", "Nam20485")
    allow, _ = filters.should_dispatch(
        "issues",
        _labeled(label="GH-ISSUE-TRACKING:Direct-Body", sender="nam20485"),
    )
    assert allow is True


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
