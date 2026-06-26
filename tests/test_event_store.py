from __future__ import annotations

import threading
import time

from webhook_receiver.event_store import EventStore

# ── emit + recent ──────────────────────────────────────────────────────────


def test_emit_creates_event_with_id_and_ts() -> None:
    store = EventStore()
    store.emit("test_event", key="value")
    events = store.recent(limit=10)
    assert len(events) == 1
    e = events[0]
    assert e["type"] == "test_event"
    assert e["data"] == {"key": "value"}
    assert isinstance(e["id"], int)
    assert isinstance(e["ts"], float)


def test_recent_returns_in_order() -> None:
    store = EventStore()
    for i in range(5):
        store.emit("test", index=i)
    events = store.recent(limit=10)
    assert len(events) == 5
    assert [e["data"]["index"] for e in events] == [0, 1, 2, 3, 4]


def test_recent_respects_limit() -> None:
    store = EventStore()
    for i in range(10):
        store.emit("test", index=i)
    events = store.recent(limit=3)
    assert len(events) == 3
    assert [e["data"]["index"] for e in events] == [7, 8, 9]


def test_recent_empty_store() -> None:
    store = EventStore()
    assert store.recent() == []


def test_recent_limit_zero_returns_empty() -> None:
    store = EventStore()
    for i in range(5):
        store.emit("test", index=i)
    assert store.recent(limit=0) == []


def test_recent_negative_limit_returns_empty() -> None:
    store = EventStore()
    for i in range(5):
        store.emit("test", index=i)
    assert store.recent(limit=-3) == []


# ── maxlen eviction ────────────────────────────────────────────────────────


def test_maxlen_evicts_oldest() -> None:
    store = EventStore(maxlen=3)
    for i in range(5):
        store.emit("test", index=i)
    events = store.recent(limit=10)
    assert len(events) == 3
    assert [e["data"]["index"] for e in events] == [2, 3, 4]


# ── subscriber fan-out ─────────────────────────────────────────────────────


def test_subscribe_yields_existing_events() -> None:
    store = EventStore()
    store.emit("a", x=1)
    store.emit("b", x=2)
    sub = store.subscribe(keepalive=0.5)
    e1 = next(sub)
    e2 = next(sub)
    assert e1["type"] == "a"
    assert e2["type"] == "b"
    sub.close()


def test_subscribe_receives_new_events() -> None:
    store = EventStore()
    received: list[dict] = []
    ready = threading.Event()

    def consumer() -> None:
        sub = store.subscribe(keepalive=1.0)
        ready.set()
        for e in sub:
            if e is not None:
                received.append(e)
                if len(received) >= 2:
                    break
        sub.close()

    t = threading.Thread(target=consumer, daemon=True)
    t.start()
    ready.wait(timeout=2)
    time.sleep(0.1)

    store.emit("new1", v=1)
    store.emit("new2", v=2)
    t.join(timeout=2)

    assert len(received) == 2
    assert received[0]["type"] == "new1"
    assert received[1]["type"] == "new2"


def test_subscribe_cleanup_on_stop() -> None:
    store = EventStore()
    assert store.subscriber_count == 0

    sub = store.subscribe(keepalive=0.5)
    assert store.subscriber_count == 1

    sub.close()
    assert store.subscriber_count == 0


def test_subscribe_keepalive_yields_none() -> None:
    store = EventStore()
    sub = store.subscribe(keepalive=0.1)
    result = next(sub)
    assert result is None
    sub.close()


# ── thread safety ──────────────────────────────────────────────────────────


def test_concurrent_emit_is_thread_safe() -> None:
    store = EventStore(maxlen=10000)

    def emitter(start: int) -> None:
        for i in range(start, start + 100):
            store.emit("concurrent", index=i)

    threads = [threading.Thread(target=emitter, args=(i * 100,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = store.recent(limit=10000)
    assert len(events) == 500
    # All IDs should be unique
    ids = [e["id"] for e in events]
    assert len(set(ids)) == 500
