from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Template

_PROMPT_TEMPLATE_FILENAME = "orchestration_prompt.jinja2.md"
_PROMPT_TEMPLATE_PATH = Path(__file__).resolve().parent / _PROMPT_TEMPLATE_FILENAME


@lru_cache(maxsize=1)
def _prompt_template() -> Template:
    if not _PROMPT_TEMPLATE_PATH.is_file():
        raise FileNotFoundError(
            f"Orchestration prompt template not found: {_PROMPT_TEMPLATE_PATH}"
        )
    source = _PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    return Template(source, keep_trailing_newline=True)


def _format_event_data(
    *,
    delivery_id: str,
    event: str,
    payload: dict[str, Any],
    max_payload_chars: int,
) -> tuple[str, bool]:
    event_data: dict[str, Any] = {
        "delivery_id": delivery_id,
        "type": event,
        **payload,
    }
    event_json = json.dumps(event_data, indent=2, sort_keys=True)
    truncated = len(event_json) > max_payload_chars
    if truncated:
        event_json = event_json[:max_payload_chars]
    return event_json, truncated


def build_orchestrator_prompt(
    *,
    delivery_id: str,
    event: str,
    payload: dict[str, Any],
    max_payload_chars: int,
) -> str:
    event_json, truncated = _format_event_data(
        delivery_id=delivery_id,
        event=event,
        payload=payload,
        max_payload_chars=max_payload_chars,
    )
    prompt = _prompt_template().render(event_data=event_json)
    if truncated:
        prompt += (
            "\n\n(Payload JSON was truncated for size; use `gh` against the repo "
            "for full context.)"
        )
    return prompt
