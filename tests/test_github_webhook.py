import hashlib
import hmac

from webhook_receiver.github import verify_signature


def test_verify_signature_accepts_valid_hmac() -> None:
    secret = "test-secret"
    body = b'{"zen":"test"}'
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    header = f"sha256={digest}"
    assert verify_signature(body, header, secret)


def test_verify_signature_rejects_invalid() -> None:
    assert not verify_signature(b"{}", "sha256=deadbeef", "secret")


def test_verify_signature_rejects_missing_header() -> None:
    assert not verify_signature(b"{}", None, "secret")


def test_verify_signature_rejects_wrong_prefix() -> None:
    assert not verify_signature(b"{}", "sha1=abc", "secret")
