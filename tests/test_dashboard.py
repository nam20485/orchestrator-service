from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from webhook_receiver.app import create_app
from webhook_receiver.beads_loop import BeadsLoop
from webhook_receiver.config import Settings
from webhook_receiver.dashboard import _parse_beads, _safe_bundle_relative_path
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
        max_payload_chars=120000,
        max_body_bytes=25 * 1024 * 1024,
        log_level="warning",
        enable_simulator=False,
        beads_enabled=False,
        beads_poll_interval=10,
        beads_max_retries=3,
        beads_workspace_root="/workspace",
        dashboard_token="test-dashboard-token",
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Clear the dashboard TTL cache between tests."""
    from webhook_receiver import dashboard
    dashboard._CACHE.clear()


def _client(app, token: str | None = "test-dashboard-token") -> TestClient:
    """TestClient that authenticates against the gated dashboard by default."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return TestClient(app, headers=headers)


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
    client = _client(app)

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
    client = _client(app)

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
    client = _client(app)

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
    loop._active_beads.add("test-proj:br-active")

    all_json = _br_list_json(
        {"id": "br-active", "status": "open", "title": "Active Task", "priority": 1},
    )
    ready_json = _br_ready_json({"id": "br-active"})

    app = create_app(_test_settings(), event_store=store, beads_loop=loop)
    client = _client(app)

    def fake_run(args, ws=None):
        if "list" in args:
            return all_json
        return ready_json

    with (
        patch("webhook_receiver.dashboard._run_beads_cmd", side_effect=fake_run),
        patch("webhook_receiver.dashboard._resolve_project", return_value="test-proj"),
    ):
        resp = client.get("/api/dashboard/beads")

    beads = resp.json()
    assert beads[0]["ui_status"] == "active"
    assert beads[0]["is_active"] is True


def test_active_bead_shown_in_list_and_agents_panel() -> None:
    """Regression: composite-keyed loop state must surface in both the bead
    list (ui_status='active') and the Active Agents panel.

    Before the fix, BeadsLoop stored state under ``"{project}:{bead_id}"``
    composite keys but the dashboard compared against raw bead IDs — so the
    Active card showed 1 but the bead never appeared in the list or agents
    panel.
    """
    store = EventStore()
    loop = BeadsLoop(_test_settings())
    loop._active_beads.add("my-proj:br-running")
    loop._bead_start_times["my-proj:br-running"] = 1000.0
    loop._retry_state["my-proj:br-running"] = {"count": 0, "logs": ""}

    all_json = _br_list_json(
        {"id": "br-running", "status": "open", "title": "Running Task", "priority": 1},
        {"id": "br-idle", "status": "open", "title": "Idle Task", "priority": 2},
    )
    ready_json = _br_ready_json({"id": "br-idle"})

    app = create_app(_test_settings(), event_store=store, beads_loop=loop)
    client = _client(app)

    def fake_run(args, ws=None):
        if "list" in args:
            return all_json
        return ready_json

    with (
        patch("webhook_receiver.dashboard._run_beads_cmd", side_effect=fake_run),
        patch("webhook_receiver.dashboard._resolve_project", return_value="my-proj"),
    ):
        beads_resp = client.get("/api/dashboard/beads")
        active_resp = client.get("/api/dashboard/active")
        overview_resp = client.get("/api/dashboard/overview")

    # The bead list must include the active bead with ui_status='active'.
    beads = {b["id"]: b for b in beads_resp.json()}
    assert "br-running" in beads, "Active bead missing from list entirely"
    assert beads["br-running"]["ui_status"] == "active"
    assert beads["br-running"]["is_active"] is True

    # The Active Agents panel must show the bead with its real title.
    agents = active_resp.json()
    assert len(agents) == 1
    assert agents[0]["bead_id"] == "br-running"
    assert agents[0]["title"] == "Running Task"

    # The overview Active card must show 1.
    assert overview_resp.json()["counts"]["active"] == 1


def test_beads_list_with_halted_bead() -> None:
    store = EventStore()
    loop = BeadsLoop(_test_settings(beads_max_retries=2))
    loop._retry_state["test-proj:br-halted"] = {"count": 2, "logs": "error"}
    loop._halted_beads.add("test-proj:br-halted")

    all_json = _br_list_json(
        {"id": "br-halted", "status": "open", "title": "Halted", "priority": 1},
    )
    ready_json = _br_ready_json({"id": "br-halted"})

    app = create_app(_test_settings(), event_store=store, beads_loop=loop)
    client = _client(app)

    def fake_run(args, ws=None):
        if "list" in args:
            return all_json
        return ready_json

    with (
        patch("webhook_receiver.dashboard._run_beads_cmd", side_effect=fake_run),
        patch("webhook_receiver.dashboard._resolve_project", return_value="test-proj"),
    ):
        resp = client.get("/api/dashboard/beads")

    beads = resp.json()
    assert beads[0]["ui_status"] == "halted"
    assert beads[0]["retry_count"] == 2


# ── Active agents ──────────────────────────────────────────────────────────


def test_active_no_loop() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)
    resp = client.get("/api/dashboard/active")
    assert resp.status_code == 200
    assert resp.json() == []


def test_active_with_loop() -> None:
    store = EventStore()
    loop = BeadsLoop(_test_settings())
    loop._active_beads.add("test-proj:br-x")
    loop._bead_start_times["test-proj:br-x"] = 1000.0

    all_json = _br_list_json({"id": "br-x", "title": "Task X"})

    app = create_app(_test_settings(), event_store=store, beads_loop=loop)
    client = _client(app)

    with (
        patch("webhook_receiver.dashboard._run_beads_cmd", return_value=all_json),
        patch("webhook_receiver.dashboard._resolve_project", return_value="test-proj"),
    ):
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
    client = _client(app)
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
    client = _client(app)
    resp = client.get("/api/dashboard/events?limit=3")
    events = resp.json()
    assert len(events) == 3


# ── Bead logs ──────────────────────────────────────────────────────────────


def test_bead_logs_not_found(tmp_path: Path) -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

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
    client = _client(app)

    with patch("webhook_receiver.dashboard.tempfile.gettempdir", return_value=str(tmp_path)):
        resp = client.get("/api/dashboard/beads/br-1/logs")

    data = resp.json()
    assert data["available"] is True
    assert "line1" in data["stdout"]
    assert "error1" in data["stderr"]


def test_bead_logs_rejects_glob_chars() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    for bad_id in ["*", "br-x[abc]", "br%20x"]:
        resp = client.get(f"/api/dashboard/beads/{bad_id}/logs")
        assert resp.status_code == 400, f"bad bead_id={bad_id!r} got {resp.status_code}"


def test_bead_logs_accepts_valid_ids(tmp_path: Path) -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    with patch("webhook_receiver.dashboard.tempfile.gettempdir", return_value=str(tmp_path)):
        for valid_id in ["br-1", "workspace-abc", "br_my_bead", "task-123"]:
            resp = client.get(f"/api/dashboard/beads/{valid_id}/logs")
            assert resp.status_code == 200, f"Expected 200 for {valid_id!r}"


def test_bead_logs_clamps_tail_zero(tmp_path: Path) -> None:
    log_dir = tmp_path / "orchestrator-webhook"
    log_dir.mkdir()
    (log_dir / "bead-br-1-abc.stdout").write_text("line1\nline2\nline3\n")

    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    with patch("webhook_receiver.dashboard.tempfile.gettempdir", return_value=str(tmp_path)):
        resp = client.get("/api/dashboard/beads/br-1/logs?tail=0")

    data = resp.json()
    assert data["available"] is True
    # tail=0 is clamped to 1, so at least one line is returned
    assert "line3" in data["stdout"]


def test_bead_logs_clamps_tail_upper_bound(tmp_path: Path) -> None:
    log_dir = tmp_path / "orchestrator-webhook"
    log_dir.mkdir()
    content = "\n".join(f"line{i}" for i in range(10))
    (log_dir / "bead-br-1-abc.stdout").write_text(content + "\n")

    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    with patch("webhook_receiver.dashboard.tempfile.gettempdir", return_value=str(tmp_path)):
        # An absurd tail value is clamped to 2000; all 10 lines still fit.
        resp = client.get("/api/dashboard/beads/br-1/logs?tail=999999")

    data = resp.json()
    assert resp.status_code == 200
    assert "line9" in data["stdout"]


# ── Bead metadata (single bead) ────────────────────────────────────────────


def test_bead_metadata_found() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    all_json = _br_list_json(
        {"id": "br-1", "status": "open", "title": "First Task", "priority": 2,
         "type": "task", "description": "Do the thing."},
        {"id": "br-2", "status": "open", "title": "Second Task", "priority": 1},
    )
    ready_json = _br_ready_json()

    def fake_run(args, ws=None):
        return all_json if "list" in args else ready_json

    with patch("webhook_receiver.dashboard._run_beads_cmd", side_effect=fake_run):
        resp = client.get("/api/dashboard/beads/br-1")

    assert resp.status_code == 200
    bead = resp.json()
    assert bead["id"] == "br-1"
    assert bead["title"] == "First Task"
    assert bead["type"] == "task"
    assert bead["description"] == "Do the thing."


def test_bead_metadata_not_found() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    all_json = _br_list_json({"id": "br-1", "status": "open", "title": "T"})
    ready_json = _br_ready_json()

    def fake_run(args, ws=None):
        return all_json if "list" in args else ready_json

    with patch("webhook_receiver.dashboard._run_beads_cmd", side_effect=fake_run):
        resp = client.get("/api/dashboard/beads/br-missing")

    assert resp.status_code == 404


def test_bead_metadata_rejects_glob_chars() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    for bad_id in ["*", "br-x[abc]", "br%20x"]:
        resp = client.get(f"/api/dashboard/beads/{bad_id}")
        assert resp.status_code == 400, f"bad bead_id={bad_id!r} got {resp.status_code}"


def test_bead_metadata_accepts_valid_ids() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    all_json = _br_list_json(
        {"id": "br-1", "status": "open", "title": "T", "priority": 1},
        {"id": "workspace-abc", "status": "open", "title": "T2", "priority": 1},
        {"id": "br_my_bead", "status": "open", "title": "T3", "priority": 1},
    )
    ready_json = _br_ready_json()

    def fake_run(args, ws=None):
        return all_json if "list" in args else ready_json

    with patch("webhook_receiver.dashboard._run_beads_cmd", side_effect=fake_run):
        for valid_id in ["br-1", "workspace-abc", "br_my_bead"]:
            resp = client.get(f"/api/dashboard/beads/{valid_id}")
            assert resp.status_code == 200, f"Expected 200 for {valid_id!r}"


# ── HTML page ──────────────────────────────────────────────────────────────


def test_dashboard_html_served() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "Orchestration Dashboard" in resp.text


def test_bead_detail_html_served() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)
    resp = client.get("/dashboard/bead/br-1")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "no-store" in resp.headers.get("cache-control", "")
    assert "Back to dashboard" in resp.text


def test_bead_detail_page_rejects_glob_chars() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)
    for bad_id in ["*", "br-x[abc]", "br%20x"]:
        resp = client.get(f"/dashboard/bead/{bad_id}")
        assert resp.status_code == 400, f"bad bead_id={bad_id!r} got {resp.status_code}"


def test_bead_detail_page_rejects_missing_token() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = TestClient(app)  # no Authorization header
    assert client.get("/dashboard/bead/br-1").status_code == 401


def test_bead_detail_page_accepts_query_token_and_sets_cookie() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = TestClient(app)  # rely on ?token= query
    resp = client.get("/dashboard/bead/br-1", params={"token": "test-dashboard-token"})
    assert resp.status_code == 200
    assert "dashboard_token" in resp.headers.get("set-cookie", "")
    # follow-up same-origin fetch (cookie auto-stored) succeeds w/o auth header
    client.headers.pop("authorization", None)
    with patch("webhook_receiver.dashboard._run_beads_cmd", return_value=""):
        assert client.get("/api/dashboard/overview").status_code == 200


# ── Graceful degradation (br not found) ────────────────────────────────────


def test_overview_br_not_found() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    with patch("webhook_receiver.dashboard._run_beads_cmd", return_value=""):
        resp = client.get("/api/dashboard/overview")

    data = resp.json()
    assert data["counts"]["total"] == 0
    assert data["initialized"] is False


# ── Authentication gating ──────────────────────────────────────────────────


def test_dashboard_disabled_by_default() -> None:
    """With no DASHBOARD_TOKEN configured, the whole surface is disabled (404)."""
    store = EventStore()
    settings = _test_settings(dashboard_token=None)
    app = create_app(settings, event_store=store)
    client = TestClient(app)  # no token header; endpoints should be gone

    assert client.get("/dashboard").status_code == 404
    assert client.get("/dashboard/bead/br-1").status_code == 404
    assert client.get("/api/dashboard/overview").status_code == 404
    assert client.get("/api/dashboard/beads/br-1").status_code == 404
    assert client.get("/api/dashboard/beads/br-1/logs").status_code == 404
    assert client.get("/api/dashboard/events").status_code == 404


def test_dashboard_rejects_missing_token() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = TestClient(app)  # no Authorization header

    assert client.get("/api/dashboard/overview").status_code == 401
    assert client.get("/dashboard").status_code == 401


def test_dashboard_rejects_wrong_token() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = TestClient(app, headers={"Authorization": "Bearer wrong-token"})
    assert client.get("/api/dashboard/overview").status_code == 401


def test_dashboard_accepts_bearer_token() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = TestClient(app, headers={"Authorization": "Bearer test-dashboard-token"})

    with patch("webhook_receiver.dashboard._run_beads_cmd", return_value=""):
        resp = client.get("/api/dashboard/overview")
    assert resp.status_code == 200


def test_dashboard_accepts_query_token_and_sets_cookie() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    # No Authorization header: auth relies on the ?token= query + cookie.
    client = TestClient(app)

    resp = client.get("/dashboard", params={"token": "test-dashboard-token"})
    assert resp.status_code == 200
    assert "Orchestration Dashboard" in resp.text
    # The token is persisted as a cookie so subsequent same-origin fetches
    # (and EventSource) are authenticated automatically.
    assert "dashboard_token" in resp.headers.get("set-cookie", "")

    # A follow-up request from the same client (cookie auto-stored) succeeds
    # with no Authorization header.
    client.headers.pop("authorization", None)
    with patch("webhook_receiver.dashboard._run_beads_cmd", return_value=""):
        assert client.get("/api/dashboard/overview").status_code == 200


# ── Dependency graph ────────────────────────────────────────────────────────


def _br_graph_json(*nodes: dict, edges=None, total=None) -> str:
    """Build canned `br graph --all --json` output (single component)."""
    edges = edges if edges is not None else []
    total = total if total is not None else len(nodes)
    return json.dumps(
        {
            "components": [{"nodes": list(nodes), "edges": edges, "roots": []}],
            "total_nodes": total,
            "total_components": 1,
        }
    )


def test_graph_endpoint_returns_nodes_and_edges() -> None:
    store = EventStore()
    graph_json = _br_graph_json(
        {"id": "br-1", "title": "Epic", "status": "open", "priority": 1, "depth": 0},
        {"id": "br-2", "title": "Task", "status": "open", "priority": 2, "depth": 1},
        edges=[["br-2", "br-1"]],  # br-2 depends on br-1
        total=2,
    )
    list_json = json.dumps(
        {
            "issues": [
                {
                    "id": "br-1",
                    "title": "Epic",
                    "status": "open",
                    "priority": 1,
                    "issue_type": "epic",
                },
                {
                    "id": "br-2",
                    "title": "Task",
                    "status": "open",
                    "priority": 2,
                    "issue_type": "task",
                },
            ]
        }
    )
    ready_json = json.dumps({"issues": [{"id": "br-1"}]})

    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    def fake_run(args, ws=None):
        if "graph" in args:
            return graph_json
        if "list" in args:
            return list_json
        if "ready" in args:
            return ready_json
        return ""

    with patch("webhook_receiver.dashboard._run_beads_cmd", side_effect=fake_run):
        resp = client.get("/api/dashboard/graph")

    assert resp.status_code == 200
    data = resp.json()
    assert data["initialized"] is True
    ids = {n["id"] for n in data["nodes"]}
    assert ids == {"br-1", "br-2"}
    # Node metadata is merged in from `br list`.
    by_id = {n["id"]: n for n in data["nodes"]}
    assert by_id["br-1"]["type"] == "epic"
    # Shared enrichment: br-1 is ready, br-2 depends on it so it is blocked.
    assert by_id["br-1"]["ui_status"] == "ready"
    assert by_id["br-2"]["ui_status"] == "blocked"
    # Edge passes through as {source, target}.
    assert {"source": "br-2", "target": "br-1"} in data["edges"]


def test_graph_endpoint_not_initialized() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    with patch("webhook_receiver.dashboard._run_beads_cmd", return_value=""):
        resp = client.get("/api/dashboard/graph")

    assert resp.status_code == 200
    data = resp.json()
    assert data["initialized"] is False
    assert data["nodes"] == []
    assert data["edges"] == []


def test_graph_endpoint_enriches_active_via_shared_helper() -> None:
    """The graph uses the same _ui_status helper as the list endpoint."""
    store = EventStore()
    loop = BeadsLoop(_test_settings())
    loop._active_beads.add("test-proj:br-active")

    graph_json = _br_graph_json(
        {"id": "br-active", "title": "Running", "status": "open", "priority": 1, "depth": 0},
        total=1,
    )
    list_json = json.dumps(
        {
            "issues": [
                {
                    "id": "br-active",
                    "title": "Running",
                    "status": "open",
                    "issue_type": "task",
                }
            ]
        }
    )

    app = create_app(_test_settings(), event_store=store, beads_loop=loop)
    client = _client(app)

    def fake_run(args, ws=None):
        if "graph" in args:
            return graph_json
        if "list" in args:
            return list_json
        return ""

    with (
        patch("webhook_receiver.dashboard._run_beads_cmd", side_effect=fake_run),
        patch("webhook_receiver.dashboard._resolve_project", return_value="test-proj"),
    ):
        resp = client.get("/api/dashboard/graph")

    nodes = {n["id"]: n for n in resp.json()["nodes"]}
    assert nodes["br-active"]["ui_status"] == "active"


# ── bvr pages bundle ────────────────────────────────────────────────────────


def _fake_bvr_export_writing(bundle_html: str):
    """Return a stand-in for _run_bvr_export that writes a fake index.html."""

    def _export(args, cwd):
        # args: ["bvr", "--export-pages", <bundle_dir>, ...]
        bundle = Path(args[2])
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "index.html").write_text(bundle_html)
        (bundle / "styles.css").write_text("body{color:#fff}")

    return _export


def test_pages_endpoint_serves_bundle(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / ".beads").mkdir(parents=True)
    bundle = tmp_path / "bundle"

    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    with (
        patch("webhook_receiver.dashboard._workspace", return_value=str(ws)),
        patch("webhook_receiver.dashboard._bvr_bundle_dir", return_value=bundle),
        patch(
            "webhook_receiver.dashboard._run_bvr_export",
            side_effect=_fake_bvr_export_writing("<html><body>bvr bundle</body></html>"),
        ),
    ):
        resp = client.get("/dashboard/pages/")

    assert resp.status_code == 200
    assert "bvr bundle" in resp.text

    # Relative sub-assets resolve through the gated catch-all route.
    with (
        patch("webhook_receiver.dashboard._workspace", return_value=str(ws)),
        patch("webhook_receiver.dashboard._bvr_bundle_dir", return_value=bundle),
        patch(
            "webhook_receiver.dashboard._run_bvr_export",
            side_effect=_fake_bvr_export_writing("<html><body>bvr bundle</body></html>"),
        ),
    ):
        asset = client.get("/dashboard/pages/styles.css")
    assert asset.status_code == 200
    assert "color" in asset.text


def test_pages_endpoint_redirect_from_no_slash() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    # /dashboard/pages (no trailing slash) → 307 → /dashboard/pages/.
    resp = client.get("/dashboard/pages", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/dashboard/pages/"


def test_pages_endpoint_not_initialized(tmp_path: Path) -> None:
    ws = tmp_path / "empty-ws"  # no .beads dir
    ws.mkdir()
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    with patch("webhook_receiver.dashboard._workspace", return_value=str(ws)):
        resp = client.get("/dashboard/pages/")

    assert resp.status_code == 200
    assert "not initialized" in resp.text


def test_pages_endpoint_rejects_traversal(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / ".beads").mkdir(parents=True)
    bundle = tmp_path / "bundle"
    secret = tmp_path / "secret.txt"
    secret.write_text("topsecret")

    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    with (
        patch("webhook_receiver.dashboard._workspace", return_value=str(ws)),
        patch("webhook_receiver.dashboard._bvr_bundle_dir", return_value=bundle),
        patch(
            "webhook_receiver.dashboard._run_bvr_export",
            side_effect=_fake_bvr_export_writing("<p>x</p>"),
        ),
    ):
        # Resolve is guarded: escaping the bundle root yields 404.
        resp = client.get("/dashboard/pages/../../secret.txt")
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "file_path",
    [
        "..",
        "../secret.txt",
        "../../etc/passwd",
        "vendor/../..",          # traversal buried after a legit segment
        "/etc/passwd",           # absolute unix path
        "\\windows\\system32",   # backslash traversal / drive-style
        "foo/../../bar",         # net-escape attempt
        "good\x00bad",           # NUL byte injection
    ],
)
def test_safe_bundle_relative_path_rejects_traversal(file_path: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _safe_bundle_relative_path(file_path)
    assert exc.value.status_code == 404


@pytest.mark.parametrize(
    "file_path, expected",
    [
        ("styles.css", "styles.css"),
        ("vendor/app.js", "vendor/app.js"),     # nested, no traversal
        ("./styles.css", "./styles.css"),         # harmless, stays under root
        ("a//b", "a//b"),                         # empty segment, harmless
        ("vendor/../styles.css", None),          # still-traversal -> rejected
    ],
)
def test_safe_bundle_relative_path_accepts_safe(file_path: str, expected) -> None:
    if expected is None:
        with pytest.raises(HTTPException):
            _safe_bundle_relative_path(file_path)
    else:
        assert _safe_bundle_relative_path(file_path) == expected


def test_pages_endpoint_rejects_absolute_path(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / ".beads").mkdir(parents=True)
    bundle = tmp_path / "bundle"

    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    with (
        patch("webhook_receiver.dashboard._workspace", return_value=str(ws)),
        patch("webhook_receiver.dashboard._bvr_bundle_dir", return_value=bundle),
        patch(
            "webhook_receiver.dashboard._run_bvr_export",
            side_effect=_fake_bvr_export_writing("<p>x</p>"),
        ),
    ):
        resp = client.get("/dashboard/pages//etc/passwd")
    assert resp.status_code == 404


def test_pages_refresh_generates_bundle(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / ".beads").mkdir(parents=True)
    bundle = tmp_path / "bundle"

    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    with (
        patch("webhook_receiver.dashboard._workspace", return_value=str(ws)),
        patch("webhook_receiver.dashboard._bvr_bundle_dir", return_value=bundle),
        patch(
            "webhook_receiver.dashboard._run_bvr_export",
            side_effect=_fake_bvr_export_writing("<p>fresh</p>"),
        ),
    ):
        resp = client.post("/api/dashboard/pages/refresh")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "generated_at" in data


def test_pages_refresh_not_initialized(tmp_path: Path) -> None:
    ws = tmp_path / "empty"
    ws.mkdir()  # no .beads
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)

    with patch("webhook_receiver.dashboard._workspace", return_value=str(ws)):
        resp = client.post("/api/dashboard/pages/refresh")

    data = resp.json()
    assert data["ok"] is False
    assert data["initialized"] is False


# ── HTML page: view selector ────────────────────────────────────────────────


def test_dashboard_html_has_view_selector() -> None:
    store = EventStore()
    app = create_app(_test_settings(), event_store=store)
    client = _client(app)
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert 'name="viewType"' in resp.text
    assert 'value="list"' in resp.text
    assert 'value="graph"' in resp.text
    assert 'value="pages"' in resp.text
    # Persisted client-side via the localStorage key used by applyView().
    assert "dashboard.viewType" in resp.text
