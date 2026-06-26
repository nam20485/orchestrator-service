from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from webhook_receiver.app import create_app
from webhook_receiver.beads_loop import BeadsLoop
from webhook_receiver.config import Settings
from webhook_receiver.dashboard import _parse_beads
from webhook_receiver.event_store import EventStore


def _test_settings(**overrides: object) -> Settings:
    repo = Path(__file__).resolve().parent.parent
    defaults = dict(
        host="127.0.0.1",
        port=8080,
        github_webhook_secret="test-secret",
        opencode_server_url="http://localhost:4099",
        prompt_script=repo / "scripts" / "prompt.ps1",
        workspace="/workspace",
        model="zai-coding-plan/glm-4.7-flash",
        agent="orchestrator",
        allowed_events=None,
        max_payload_chars=120000,
        max_body_bytes=25 * 1024 * 1024,
        log_level="warning",
        enable_simulator=False,
        beads_enabled=False,
        beads_poll_interval=10,
        beads_max_retries=3,
        beads_workspace_root="/workspace",
        beads_target_repo="",
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Clear the dashboard TTL cache between tests."""
    from webhook_receiver import dashboard
    dashboard._CACHE.clear()


def _br_list_json(*beads: dict) -> str:
    return json.dumps(list(beads))


def _br_ready_json(*beads: dict) -> str:
    return json.dumps({"issues": list(beads)})


# ── _parse_beads ───────────────────────────────────────────────────────────


def test_parse_beads_issues_key() -> None:
    result = _parse_beads(json.dumps({"issues": [{"id": "br-a"}]}))
    assert len(result) == 1
    assert result[0]["id"] == "br-a"


def test_parse_beads_bare_list() -> None:
    result = _parse_beads(json.dumps([{"id": "br-b"}]))
    assert len(result) == 1


def test_parse_beads_empty() -> None:
    assert _parse_beads("") == []
    assert _parse_beads("garbage") == []


def test_parse_beads_beads_key() -> None:
    result = _parse_beads(json.dumps({"beads": [{"id": "br-c"}]}))
    assert len(result) == 1


# ── Overview ───────────────────────────────────────────────────────────────


def test_overview_empty() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = TestClient(app)

    with patch("webhook_receiver.dashboard._run_beads_cmd", return_value=""):
        resp = client.get("/api/dashboard/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["counts"]["total"] == 0
    assert data["counts"]["ready"] == 0
    assert data["counts"]["closed"] == 0
    assert data["initialized"] is False


def test_overview_with_beads() -> None:
    store = EventStore()
    all_json = _br_list_json(
        {"id": "br-1", "status": "open", "title": "T1", "priority": 1},
        {"id": "br-2", "status": "open", "title": "T2", "priority": 2},
        {"id": "br-3", "status": "closed", "title": "T3", "priority": 3},
    )
    ready_json = _br_ready_json({"id": "br-1"})

    app = create_app(_test_settings(), event_store=store)
    client = TestClient(app)

    def fake_run(args, ws=None):
        if "list" in args:
            return all_json
        return ready_json

    with patch("webhook_receiver.dashboard._run_beads_cmd", side_effect=fake_run):
        resp = client.get("/api/dashboard/overview")

    data = resp.json()
    assert data["counts"]["total"] == 3
    assert data["counts"]["open"] == 2
    assert data["counts"]["ready"] == 1
    assert data["counts"]["blocked"] == 1
    assert data["counts"]["closed"] == 1
    assert data["initialized"] is True


# ── Beads list ─────────────────────────────────────────────────────────────


def test_beads_list_enriched() -> None:
    store = EventStore()
    all_json = _br_list_json(
        {"id": "br-ready", "status": "open", "title": "Ready Task", "priority": 1},
        {"id": "br-blocked", "status": "open", "title": "Blocked Task", "priority": 2},
        {"id": "br-done", "status": "closed", "title": "Done Task", "priority": 3},
    )
    ready_json = _br_ready_json({"id": "br-ready"})

    app = create_app(_test_settings(), event_store=store)
    client = TestClient(app)

    def fake_run(args, ws=None):
        if "list" in args:
            return all_json
        return ready_json

    with patch("webhook_receiver.dashboard._run_beads_cmd", side_effect=fake_run):
        resp = client.get("/api/dashboard/beads")

    assert resp.status_code == 200
    beads = resp.json()
    by_id = {b["id"]: b for b in beads}
    assert by_id["br-ready"]["ui_status"] == "ready"
    assert by_id["br-blocked"]["ui_status"] == "blocked"
    assert by_id["br-done"]["ui_status"] == "closed"


def test_beads_list_with_active_loop() -> None:
    store = EventStore()
    loop = BeadsLoop(_test_settings())
    loop._active_beads.add("br-active")

    all_json = _br_list_json(
        {"id": "br-active", "status": "open", "title": "Active Task", "priority": 1},
    )
    ready_json = _br_ready_json({"id": "br-active"})

    app = create_app(_test_settings(), event_store=store, beads_loop=loop)
    client = TestClient(app)

    def fake_run(args, ws=None):
        if "list" in args:
            return all_json
        return ready_json

    with patch("webhook_receiver.dashboard._run_beads_cmd", side_effect=fake_run):
        resp = client.get("/api/dashboard/beads")

    beads = resp.json()
    assert beads[0]["ui_status"] == "active"
    assert beads[0]["is_active"] is True


def test_beads_list_with_halted_bead() -> None:
    store = EventStore()
    loop = BeadsLoop(_test_settings(beads_max_retries=2))
    loop._retry_state["br-halted"] = {"count": 2, "logs": "error"}

    all_json = _br_list_json(
        {"id": "br-halted", "status": "open", "title": "Halted", "priority": 1},
    )
    ready_json = _br_ready_json({"id": "br-halted"})

    app = create_app(_test_settings(), event_store=store, beads_loop=loop)
    client = TestClient(app)

    def fake_run(args, ws=None):
        if "list" in args:
            return all_json
        return ready_json

    with patch("webhook_receiver.dashboard._run_beads_cmd", side_effect=fake_run):
        resp = client.get("/api/dashboard/beads")

    beads = resp.json()
    assert beads[0]["ui_status"] == "halted"
    assert beads[0]["retry_count"] == 2


# ── Active agents ──────────────────────────────────────────────────────────


def test_active_no_loop() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = TestClient(app)
    resp = client.get("/api/dashboard/active")
    assert resp.status_code == 200
    assert resp.json() == []


def test_active_with_loop() -> None:
    store = EventStore()
    loop = BeadsLoop(_test_settings())
    loop._active_beads.add("br-x")
    loop._bead_start_times["br-x"] = 1000.0

    all_json = _br_list_json({"id": "br-x", "title": "Task X"})

    app = create_app(_test_settings(), event_store=store, beads_loop=loop)
    client = TestClient(app)

    with patch("webhook_receiver.dashboard._run_beads_cmd", return_value=all_json):
        resp = client.get("/api/dashboard/active")

    data = resp.json()
    assert len(data) == 1
    assert data[0]["bead_id"] == "br-x"
    assert data[0]["title"] == "Task X"


# ── Events ─────────────────────────────────────────────────────────────────


def test_events_recent() -> None:
    store = EventStore()
    store.emit("test_a", x=1)
    store.emit("test_b", y=2)

    app = create_app(_test_settings(), event_store=store)
    client = TestClient(app)
    resp = client.get("/api/dashboard/events")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 2
    assert events[0]["type"] == "test_a"
    assert events[1]["type"] == "test_b"


def test_events_limit() -> None:
    store = EventStore()
    for i in range(10):
        store.emit("test", i=i)

    app = create_app(_test_settings(), event_store=store)
    client = TestClient(app)
    resp = client.get("/api/dashboard/events?limit=3")
    events = resp.json()
    assert len(events) == 3


# ── Bead logs ──────────────────────────────────────────────────────────────


def test_bead_logs_not_found(tmp_path: Path) -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = TestClient(app)

    with patch("webhook_receiver.dashboard.tempfile.gettempdir", return_value=str(tmp_path)):
        resp = client.get("/api/dashboard/beads/br-nonexist/logs")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert data["stdout"] == ""
    assert data["stderr"] == ""


def test_bead_logs_found(tmp_path: Path) -> None:
    log_dir = tmp_path / "orchestrator-webhook"
    log_dir.mkdir()
    (log_dir / "bead-br-1-abc.stdout").write_text("line1\nline2\nline3\n")
    (log_dir / "bead-br-1-abc.stderr").write_text("error1\n")

    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = TestClient(app)

    with patch("webhook_receiver.dashboard.tempfile.gettempdir", return_value=str(tmp_path)):
        resp = client.get("/api/dashboard/beads/br-1/logs")

    data = resp.json()
    assert data["available"] is True
    assert "line1" in data["stdout"]
    assert "error1" in data["stderr"]


# ── HTML page ──────────────────────────────────────────────────────────────


def test_dashboard_html_served() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = TestClient(app)
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "Orchestration Dashboard" in resp.text


# ── Graceful degradation (br not found) ────────────────────────────────────


def test_overview_br_not_found() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = TestClient(app)

    with patch("webhook_receiver.dashboard._run_beads_cmd", return_value=""):
        resp = client.get("/api/dashboard/overview")

    data = resp.json()
    assert data["counts"]["total"] == 0
    assert data["initialized"] is False
