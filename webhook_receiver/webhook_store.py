from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_EVENTS = 1000
_DEFAULT_RETENTION_DAYS = 30


class WebhookStore:
    """Persistent store of webhook delivery events keyed by ``delivery_id``.

    Each webhook delivery is recorded when received, then updated with the
    dispatch decision (allowed/denied) and — if allowed — the resulting run
    prompt stem so the webhooks page can link through to the run narrative.

    Events are persisted as a single JSON array in ``{log_dir}/webhooks.json``.
    This is simple, supports in-place updates by delivery_id, and is cheap
    given the very low webhook volume (a few per day). On startup the file is
    loaded into an in-memory dict; every mutation rewrites the file. The store
    is capped at ``max_events`` entries (oldest evicted by timestamp).

    The JSON array file is used instead of JSON-lines because events need
    *mutable* state updates (decision, prompt_stem) across phases; an
    append-only log would require collapse-on-read which adds complexity for
    no benefit at this scale.
    """

    def __init__(
        self,
        log_dir: Path,
        max_events: int = _DEFAULT_MAX_EVENTS,
    ) -> None:
        self._log_dir = log_dir
        self._max_events = max_events
        self._events: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._path = log_dir / "webhooks.json"
        self._load()
        # On startup, enforce both the count cap and time-based retention
        # so a long-lived store file doesn't grow unbounded across restarts.
        # No lock needed: constructor runs before any other thread can access.
        self._enforce_cap()
        if self._events:
            self._persist()
        self.cleanup_old()

    # ── public API ────────────────────────────────────────────────────────

    def record(self, delivery_id: str, **fields: Any) -> None:
        """Create or update a webhook event by ``delivery_id``.

        On first call for a delivery_id, the event is created with a
        ``received_ts``. Subsequent calls merge *fields* into the existing
        event (e.g. ``decision``, ``reason``, ``prompt_stem``).
        """
        with self._lock:
            now = time.time()
            ev = self._events.get(delivery_id)
            if ev is None:
                ev = {
                    "delivery_id": delivery_id,
                    "received_ts": now,
                    "decision": "pending",
                }
                self._events[delivery_id] = ev
            ev.update(fields)
            self._enforce_cap()
            self._persist()

    def list_events(self, limit: int = 500) -> list[dict[str, Any]]:
        """Return webhook events newest-first by ``received_ts``."""
        with self._lock:
            events = sorted(
                (dict(e) for e in self._events.values()),
                key=lambda e: e.get("received_ts", 0),
                reverse=True,
            )
        return events[:limit]

    def get(self, delivery_id: str) -> dict[str, Any] | None:
        """Return a single webhook event by delivery_id, or ``None``."""
        with self._lock:
            ev = self._events.get(delivery_id)
            return dict(ev) if ev else None

    def cleanup_old(self, max_age_days: int = _DEFAULT_RETENTION_DAYS) -> int:
        """Delete events older than *max_age_days*. Returns the count removed."""
        cutoff = time.time() - max_age_days * 86400
        with self._lock:
            stale = [did for did, ev in self._events.items() if ev.get("received_ts", 0) < cutoff]
            for did in stale:
                del self._events[did]
            if stale:
                self._persist()
        return len(stale)

    # ── internals ─────────────────────────────────────────────────────────

    def _enforce_cap(self) -> None:
        """Evict the oldest events when the store exceeds ``max_events``."""
        if len(self._events) <= self._max_events:
            return
        # Sort by received_ts ascending and remove the oldest entries.
        sorted_ids = sorted(
            self._events,
            key=lambda did: self._events[did].get("received_ts", 0),
        )
        excess = len(self._events) - self._max_events
        for did in sorted_ids[:excess]:
            del self._events[did]

    def _persist(self) -> None:
        """Write the full event set to ``webhooks.json`` (best-effort)."""
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            data = sorted(
                self._events.values(),
                key=lambda e: e.get("received_ts", 0),
            )
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            logger.warning("Failed to persist webhook store", exc_info=True)

    def _load(self) -> None:
        """Load events from ``webhooks.json`` on startup (best-effort)."""
        try:
            if not self._path.is_file():
                return
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return
            for ev in data:
                if isinstance(ev, dict) and ev.get("delivery_id"):
                    self._events[ev["delivery_id"]] = ev
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to load webhook store", exc_info=True)
