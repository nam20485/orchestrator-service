from __future__ import annotations

import copy
from typing import Any

SAFE_EVENTS = frozenset({"ping"})
WORK_EVENTS = frozenset(
    {"issues", "pull_request", "issue_comment", "workflow_run", "custom"}
)
ALL_EVENTS = SAFE_EVENTS | WORK_EVENTS

_DEFAULT_REPO = "org/repo"
_DEFAULT_SENDER = "simulator-user"


def list_templates(*, safe_only: bool = False) -> list[str]:
    if safe_only:
        return sorted(SAFE_EVENTS)
    return sorted(WORK_EVENTS)


def _repository(full_name: str) -> dict[str, Any]:
    owner, _, name = full_name.partition("/")
    return {
        "full_name": full_name,
        "name": name or "repo",
        "owner": {"login": owner or "org"},
    }


def _base_payload(
    *,
    action: str | None,
    repo: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "repository": _repository(repo),
        "sender": {"login": _DEFAULT_SENDER},
    }
    if action is not None:
        payload["action"] = action
    return payload


def get_template(
    event: str,
    *,
    repo: str = _DEFAULT_REPO,
    action: str | None = None,
    number: int | None = None,
) -> dict[str, Any]:
    event = event.lower()
    if event not in ALL_EVENTS:
        raise ValueError(f"Unknown event template: {event}")

    if event == "ping":
        return {
            "zen": "Design for failure.",
            "hook_id": 123456789,
            "hook": {
                "type": "Repository",
                "id": 123456789,
                "name": "web",
                "active": True,
                "events": ["issues", "pull_request"],
            },
        }

    if event == "custom":
        return _base_payload(action=action or "opened", repo=repo)

    if event == "issues":
        issue_number = number if number is not None else 1
        payload = _base_payload(action=action or "opened", repo=repo)
        payload["issue"] = {
            "number": issue_number,
            "title": f"Simulated issue #{issue_number}",
            "state": "open",
            "labels": [{"name": "orchestrate"}],
        }
        return payload

    if event == "pull_request":
        pr_number = number if number is not None else 42
        payload = _base_payload(action=action or "opened", repo=repo)
        payload["pull_request"] = {
            "number": pr_number,
            "title": f"Simulated PR #{pr_number}",
            "state": "open",
            "head": {"ref": "feature/simulated"},
            "base": {"ref": "main"},
        }
        return payload

    if event == "issue_comment":
        issue_number = number if number is not None else 1
        payload = _base_payload(action=action or "created", repo=repo)
        payload["issue"] = {
            "number": issue_number,
            "title": f"Simulated issue #{issue_number}",
        }
        payload["comment"] = {
            "id": 9001,
            "body": "Simulated comment from webhook simulator.",
        }
        return payload

    if event == "workflow_run":
        payload = _base_payload(action=action or "completed", repo=repo)
        payload["workflow_run"] = {
            "id": 10001,
            "name": "CI",
            "conclusion": "failure",
            "status": "completed",
            "head_branch": "main",
        }
        return payload

    raise ValueError(f"Unknown event template: {event}")


def merge_template(
    template: dict[str, Any],
    *,
    repo: str | None = None,
    action: str | None = None,
    number: int | None = None,
) -> dict[str, Any]:
    """Apply quick-field overrides to a template copy."""
    payload = copy.deepcopy(template)
    if repo:
        payload["repository"] = _repository(repo)
    if action is not None:
        payload["action"] = action
    if number is not None:
        if "issue" in payload and isinstance(payload["issue"], dict):
            payload["issue"]["number"] = number
        if "pull_request" in payload and isinstance(payload["pull_request"], dict):
            payload["pull_request"]["number"] = number
    return payload
