from __future__ import annotations

import os
import re

_DEFAULT_BLACKLIST: list[str] = [
    r"service=bus\s+type=message\.part\.delta",
    r"service=bus\s+type=message\.part\.updated",
]


def _load_patterns() -> list[re.Pattern[str]]:
    raw = os.environ.get("TRACE_BLACKLIST_PATTERNS", "")
    if raw.strip():
        patterns = [p.strip() for p in raw.split("\n") if p.strip()]
    else:
        patterns = _DEFAULT_BLACKLIST
    return [re.compile(p) for p in patterns]


_PATTERNS: list[re.Pattern[str]] = _load_patterns()


def should_filter(line: str) -> bool:
    """Return True if *line* matches any blacklisted trace pattern."""
    return any(p.search(line) for p in _PATTERNS)
