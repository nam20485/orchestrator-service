"""Sentry error tracking, inert unless ``settings.sentry_dsn`` is set.

sentry-sdk is a required runtime dependency, but every function here is a
no-op when Sentry has not been initialized (no DSN configured), so tests and
local runs never need a DSN. ``send_default_pii=False`` and the sanitized
context values (never raw webhook payloads/tokens) keep dispatch context out
of Sentry's PII surface.
"""

from __future__ import annotations

from typing import Any

from webhook_receiver.config import Settings

# Module-level flag, not sentry_sdk.is_initialized() -- a Client is "active"
# per the SDK's own definition even with no DSN, so init_sentry() tracks
# whether *this process* actually configured a DSN.
_active = False


def init_sentry(settings: Settings) -> bool:
    """Initialize Sentry from *settings*. Returns True if initialized."""
    global _active
    if not settings.sentry_dsn:
        _active = False
        return False

    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        release=settings.sentry_release,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
    )
    _active = True
    return True


def capture_dispatch_failure(message: str, **context: Any) -> None:
    """Report a dispatch-run failure with sanitized context tags.

    No-op when Sentry is not initialized. *context* values should already be
    sanitized (e.g. via runner._sanitize_for_comment) before being passed in.
    """
    if not _active:
        return

    import sentry_sdk

    with sentry_sdk.new_scope() as scope:
        for key, value in context.items():
            if value is not None:
                scope.set_tag(key, value)
        sentry_sdk.capture_message(message, level="error")
