from __future__ import annotations

import json
import time

from webhook_receiver.webhook_store import WebhookStore

# ── record + list ──────────────────────────────────────────────────────────


def test_record_creates_event_with_defaults(tmp_path) -> None:
    store = WebhookStore(tmp_path)
    store.record("delivery-1", event="issues", action="labeled", repo="owner/repo")
    events = store.list_events()
    assert len(events) == 1
    ev = events[0]
    assert ev["delivery_id"] == "delivery-1"
    assert ev["event"] == "issues"
    assert ev["action"] == "labeled"
    assert ev["repo"] == "owner/repo"
    assert ev["decision"] == "pending"
    assert isinstance(ev["received_ts"], float)


def test_record_updates_existing_event(tmp_path) -> None:
    store = WebhookStore(tmp_path)
    store.record("delivery-1", event="issues", action="labeled")
    store.record("delivery-1", decision="allowed", reason="allowed")
    store.record("delivery-1", prompt_stem="prompt-foo")
    ev = store.get("delivery-1")
    assert ev is not None
    assert ev["decision"] == "allowed"
    assert ev["reason"] == "allowed"
    assert ev["prompt_stem"] == "prompt-foo"
    assert ev["event"] == "issues"  # original field preserved


def test_list_events_newest_first(tmp_path) -> None:
    store = WebhookStore(tmp_path)
    for i in range(5):
        store.record(f"delivery-{i}")
        time.sleep(0.01)
    events = store.list_events()
    assert [e["delivery_id"] for e in events] == [
        "delivery-4",
        "delivery-3",
        "delivery-2",
        "delivery-1",
        "delivery-0",
    ]


def test_list_events_respects_limit(tmp_path) -> None:
    store = WebhookStore(tmp_path)
    for i in range(10):
        store.record(f"delivery-{i}")
    events = store.list_events(limit=3)
    assert len(events) == 3


def test_get_missing_returns_none(tmp_path) -> None:
    store = WebhookStore(tmp_path)
    assert store.get("nonexistent") is None


def test_empty_store_list(tmp_path) -> None:
    store = WebhookStore(tmp_path)
    assert store.list_events() == []


# ── persistence ────────────────────────────────────────────────────────────


def test_persists_and_loads_on_restart(tmp_path) -> None:
    store = WebhookStore(tmp_path)
    store.record("delivery-1", event="issues", repo="owner/repo")
    store.record("delivery-1", decision="denied", reason="not workflow-relevant")
    store.record("delivery-2", event="issues", repo="other/repo")
    store.record("delivery-2", decision="allowed", prompt_stem="prompt-abc")

    # Simulate restart: new store instance reads from disk.
    store2 = WebhookStore(tmp_path)
    events = store2.list_events()
    assert len(events) == 2
    ev1 = store2.get("delivery-1")
    assert ev1 is not None
    assert ev1["decision"] == "denied"
    assert ev1["reason"] == "not workflow-relevant"
    ev2 = store2.get("delivery-2")
    assert ev2 is not None
    assert ev2["decision"] == "allowed"
    assert ev2["prompt_stem"] == "prompt-abc"


def test_persist_file_is_valid_json(tmp_path) -> None:
    store = WebhookStore(tmp_path)
    store.record("delivery-1", event="issues", repo="owner/repo")
    data = json.loads((tmp_path / "webhooks.json").read_text())
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["delivery_id"] == "delivery-1"


# ── cap enforcement ────────────────────────────────────────────────────────


def test_cap_evicts_oldest(tmp_path) -> None:
    store = WebhookStore(tmp_path, max_events=3)
    for i in range(5):
        store.record(f"delivery-{i}")
        time.sleep(0.01)
    events = store.list_events()
    assert len(events) == 3
    # Oldest two should be evicted.
    ids = {e["delivery_id"] for e in events}
    assert "delivery-2" in ids
    assert "delivery-3" in ids
    assert "delivery-4" in ids
    assert "delivery-0" not in ids
    assert "delivery-1" not in ids


# ── cleanup_old ────────────────────────────────────────────────────────────


def test_cleanup_old_removes_stale(tmp_path) -> None:
    store = WebhookStore(tmp_path)
    # Create an event and manually age its timestamp.
    store.record("old-event")
    with store._lock:
        store._events["old-event"]["received_ts"] = time.time() - 40 * 86400
    store._persist()
    store.record("new-event")

    removed = store.cleanup_old(max_age_days=30)
    assert removed == 1
    assert store.get("old-event") is None
    assert store.get("new-event") is not None


# ── concurrent writes ──────────────────────────────────────────────────────


def test_concurrent_writes_are_safe(tmp_path) -> None:
    import threading

    store = WebhookStore(tmp_path)
    errors: list[Exception] = []

    def writer(start: int) -> None:
        try:
            for i in range(start, start + 20):
                store.record(f"delivery-{i}", event="issues")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n * 20,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(store.list_events(limit=1000)) == 100
