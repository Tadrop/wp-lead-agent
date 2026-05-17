"""Webhook signature verification (HMAC-SHA256)."""

from __future__ import annotations

import hashlib
import hmac

from ..models import ClientConfig
from ..settings import get_settings


def expected_signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(*, config: ClientConfig, body: bytes, header_signature: str | None) -> bool:
    if not header_signature:
        return False
    secret = config.webhook_secret or get_settings().webhook_default_secret
    sig = header_signature.strip()
    # Allow `sha256=...` prefix that many plugins (Gravity, WPForms) send.
    if sig.lower().startswith("sha256="):
        sig = sig.split("=", 1)[1]
    return hmac.compare_digest(sig, expected_signature(secret, body))
