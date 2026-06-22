from __future__ import annotations

import hashlib
import hmac


def compute_signature(body: bytes, secret: str) -> str:
    """Compute the ``X-Hub-Signature-256`` header value (``sha256=<hex>``)."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(
    body: bytes, signature_header: str | None, secret: str
) -> bool:
    """Validate X-Hub-Signature-256 (sha256=<hex>)."""
    if not signature_header:
        return False
    prefix = "sha256="
    if not signature_header.startswith(prefix):
        return False
    expected_hex = signature_header[len(prefix) :]
    try:
        expected = bytes.fromhex(expected_hex)
    except ValueError:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return hmac.compare_digest(digest, expected)
