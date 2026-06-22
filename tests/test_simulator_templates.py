from __future__ import annotations

import pytest

from webhook_receiver.simulator_templates import (
    get_template,
    merge_template,
)

# ── get_template error paths ──────────────────────────────────────────────


def test_get_template_unknown_event_raises() -> None:
    with pytest.raises(ValueError, match="Unknown event template"):
        get_template("not_real")


def test_get_template_custom_event() -> None:
    payload = get_template("custom", action="opened", repo="o/r")
    assert payload["action"] == "opened"
    assert payload["repository"]["full_name"] == "o/r"
    assert payload["sender"]["login"]


# ── get_template event templates ──────────────────────────────────────────


def test_get_template_pull_request() -> None:
    payload = get_template("pull_request", number=42)
    assert payload["action"] == "opened"
    assert payload["pull_request"]["number"] == 42
    assert payload["pull_request"]["state"] == "open"
    assert payload["pull_request"]["head"]["ref"] == "feature/simulated"
    assert payload["pull_request"]["base"]["ref"] == "main"


def test_get_template_issue_comment() -> None:
    payload = get_template("issue_comment", number=5)
    assert payload["action"] == "created"
    assert payload["issue"]["number"] == 5
    assert "comment" in payload
    assert payload["comment"]["id"] == 9001


def test_get_template_workflow_run() -> None:
    payload = get_template("workflow_run")
    assert payload["action"] == "completed"
    assert payload["workflow_run"]["conclusion"] == "failure"
    assert payload["workflow_run"]["status"] == "completed"
    assert payload["workflow_run"]["head_branch"] == "main"


# ── merge_template ────────────────────────────────────────────────────────


def test_merge_template_pull_request_number() -> None:
    base = get_template("pull_request", number=1)
    merged = merge_template(base, number=99)
    assert merged["pull_request"]["number"] == 99
    assert base["pull_request"]["number"] == 1
