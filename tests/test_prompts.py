from webhook_receiver.prompts import build_orchestrator_prompt


def test_build_prompt_loads_orchestration_template() -> None:
    payload = {
        "action": "labeled",
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "alice"},
    }
    prompt = build_orchestrator_prompt(
        delivery_id="delivery-1",
        event="issues",
        payload=payload,
        max_payload_chars=120000,
    )
    assert "Orchestrator Agent Prompt" in prompt
    assert "MANDATORY STARTUP" in prompt
    assert "EVENT_DATA Branching Logic" in prompt
    assert "delivery-1" in prompt
    assert '"type": "issues"' in prompt
    assert "labeled" in prompt
    assert "org/repo" in prompt
    assert "alice" in prompt
    assert "{{ event_data }}" not in prompt
    assert "{{event_data}}" not in prompt


def test_build_prompt_truncates_large_payload() -> None:
    payload = {"data": "x" * 5000}
    prompt = build_orchestrator_prompt(
        delivery_id="d",
        event="push",
        payload=payload,
        max_payload_chars=100,
    )
    assert "truncated" in prompt.lower()


def test_dispatch_clause_links_issue_to_project_and_milestone() -> None:
    """The dispatch clause must link the dispatch issue to the Project V2 and a
    milestone (discovery-path-alignment leftover issue #1)."""
    payload = {
        "action": "labeled",
        "repository": {"full_name": "org/repo"},
    }
    prompt = build_orchestrator_prompt(
        delivery_id="d",
        event="issues",
        payload=payload,
        max_payload_chars=120000,
    )
    assert "TRACKER LINKING" in prompt
    assert "gh project item-add" in prompt
    assert "gh issue edit" in prompt
    assert "--milestone" in prompt
    # Project/milestone must be verified after the attempt.
    assert "milestone,projectItems" in prompt


def test_dispatch_clause_links_pr_to_issue() -> None:
    """A PR opened by the dispatch clause must reference its issue so GitHub
    auto-links it (discovery-path-alignment leftover issue #2)."""
    prompt = build_orchestrator_prompt(
        delivery_id="d",
        event="issues",
        payload={"action": "labeled"},
        max_payload_chars=120000,
    )
    assert "Resolves #" in prompt
    assert "closingIssuesReferences" in prompt
