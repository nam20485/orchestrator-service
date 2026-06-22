from __future__ import annotations

import importlib

from webhook_receiver import filters


def test_should_filter_matches_default_blacklist() -> None:
    assert filters.should_filter("service=bus type=message.part.delta stuff")
    assert filters.should_filter("service=bus type=message.part.updated x")


def test_should_filter_passes_normal_lines() -> None:
    assert not filters.should_filter("normal log line")
    assert not filters.should_filter("INFO server started")
    assert not filters.should_filter("")


def test_load_patterns_from_env(monkeypatch) -> None:
    monkeypatch.setenv("TRACE_BLACKLIST_PATTERNS", "my-custom-pattern\nanother-thing")
    importlib.reload(filters)
    try:
        assert filters.should_filter("contains my-custom-pattern here")
        assert filters.should_filter("has another-thing in it")
        assert not filters.should_filter("normal line")
    finally:
        monkeypatch.delenv("TRACE_BLACKLIST_PATTERNS", raising=False)
        importlib.reload(filters)
