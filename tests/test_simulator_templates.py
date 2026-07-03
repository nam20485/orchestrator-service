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


# ── issues.labeled dispatchability (Comment 2 functionality fix) ──────────


def test_get_template_issues_labeled_is_dispatchable() -> None:
    """An issues.labeled template must carry a workflow label so it dispatches.

    Mirrors GitHub's real payload shape: the added label appears at the top
    level (``label``) and on the issue (``labels``). ``should_dispatch`` reads
    the top-level ``label.name``, so without it the Work-events simulator flow
    is silently rejected.
    """
    from webhook_receiver.filters import should_dispatch

    payload = get_template("issues", action="labeled")
    assert payload["action"] == "labeled"
    assert payload["label"]["name"] == "orchestration:dispatch"
    assert payload["issue"]["labels"][0]["name"] == "orchestration:dispatch"
    # End-to-end: the template must clear the transport dispatch gate.
    allow, _ = should_dispatch("issues", payload)
    assert allow is True


def test_get_template_issues_opened_has_no_top_level_label() -> None:
    """Non-labeled issue actions keep the legacy shape (no top-level label)."""
    payload = get_template("issues", action="opened")
    assert payload["action"] == "opened"
    assert "label" not in payload
    assert payload["issue"]["labels"][0]["name"] == "orchestrate"
