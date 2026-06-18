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
