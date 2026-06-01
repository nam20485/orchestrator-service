from __future__ import annotations

import json
from typing import Any


def build_orchestrator_prompt(
    *,
    delivery_id: str,
    event: str,
    payload: dict[str, Any],
    max_payload_chars: int,
) -> str:
    action = payload.get("action")
    repo = payload.get("repository") or {}
    full_name = repo.get("full_name", "unknown")
    sender = (payload.get("sender") or {}).get("login", "unknown")

    payload_json = json.dumps(payload, indent=2, sort_keys=True)
    truncated = False
    if len(payload_json) > max_payload_chars:
        payload_json = payload_json[:max_payload_chars]
        truncated = True

    action_line = f"- Action: `{action}`\n" if action else ""
    truncate_note = (
        "\n\n(Payload JSON was truncated for size; use `gh` against the repo for full context.)"
        if truncated
        else ""
    )

    return f"""# GitHub App webhook — orchestration task

A GitHub App webhook was delivered to the orchestrator service. Analyze the event and
take appropriate action using the orchestrator agent workflow (delegate to specialists as needed).

## Delivery metadata
- Delivery ID: `{delivery_id}`
- Event: `{event}`
{action_line}- Repository: `{full_name}`
- Sender: `{sender}`

## Instructions
- Use the payload below as the source of truth for repo, issue/PR numbers, labels, and comments.
- Prefer `gh` CLI for GitHub operations when the token is available in the environment.
- Do not assume files exist on disk unless you clone or verify them under the workspace.

## Payload
```json
{payload_json}
```{truncate_note}
"""
