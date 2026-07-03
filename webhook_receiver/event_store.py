from __future__ import annotations

import itertools
import queue
import threading
import time
from collections import deque
from collections.abc import Iterator
from typing import Any


class Subscriber:
    """Iterator over events from an :class:`EventStore`.

    Created by :meth:`EventStore.subscribe`. Iteration blocks until an event
    arrives or the keepalive timeout expires (yields ``None``). Call
    :meth:`close` to deregister; this is also done via ``__del__``.
    """

    def __init__(
        self,
        store: EventStore,
        q: queue.Queue[dict[str, Any] | None],
        keepalive: float,
    ) -> None:
        self._store = store
        self._q = q
        self._keepalive = keepalive
        self._closed = False

    def __iter__(self) -> Iterator[dict[str, Any] | None]:
        return self

    def __next__(self) -> dict[str, Any] | None:
        if self._closed:
            raise StopIteration
        try:
            return self._q.get(timeout=self._keepalive)
        except queue.Empty:
            return None

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            with self._store._lock:
                try:
                    self._store._subscribers.remove(self._q)
                except ValueError:
                    pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class EventStore:
    """Thread-safe ring buffer of system events with SSE subscriber fan-out.

    Events are appended from sync threads (BeadsLoop, BackgroundTasks) and
    delivered to SSE subscribers via per-subscriber ``queue.Queue`` objects.
    """

    def __init__(self, maxlen: int = 1000) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._subscribers: list[queue.Queue[dict[str, Any] | None]] = []
        self._lock = threading.Lock()
        self._counter = itertools.count(1)

    def emit(self, event_type: str, **data: Any) -> None:
        """Append an event and fan-out to all subscribers."""
        event = {
            "id": next(self._counter),
            "type": event_type,
            "ts": time.time(),
            "data": data,
        }
        with self._lock:
            self._events.append(event)
            subs = list(self._subscribers)
        for sub in subs:
            try:
                sub.put_nowait(event)
            except queue.Full:
                pass

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the last *limit* events (oldest-first within the window)."""
        if limit <= 0:
            return []
        with self._lock:
            events = list(self._events)
        return events[-limit:] if limit < len(events) else events

    def subscribe(self, keepalive: float = 30.0) -> Subscriber:
        """Register a subscriber that yields events for SSE streaming.

        Returns a :class:`Subscriber` iterator. Existing events are queued
        immediately; new events are delivered as they arrive. On keepalive
        timeout the iterator yields ``None``. Call ``close()`` to deregister.
        """
        q: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=500)
        with self._lock:
            for e in list(self._events):
                try:
                    q.put_nowait(e)
                except queue.Full:
                    break
            self._subscribers.append(q)
        return Subscriber(self, q, keepalive)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)
