"""Gmail send / draft.

Two modes per `ClientConfig.auto_response_policy`:

* `DRAFT` — create a draft in the sender's mailbox (a human reviews and sends)
* `SEND`  — send immediately via the Gmail API

We hit the REST endpoints directly (no `google-api-python-client` dep) so
the install footprint stays small. The OAuth refresh-token flow exchanges
the per-client refresh token for a short-lived access token on each send.

Tests should call `set_sender(stub)` instead of hitting Gmail.
"""

from __future__ import annotations

import base64
import email.mime.text
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from ..logging_setup import get_logger
from ..models import AutoResponsePolicy, ClientConfig, Lead
from ..settings import get_settings

log = get_logger(__name__)


@dataclass
class GmailSendResult:
    ok: bool
    mode: str  # "send" | "draft"
    message_id: str | None = None
    thread_id: str | None = None
    error: str | None = None


class _SenderFn(Protocol):
    def __call__(
        self,
        *,
        lead: Lead,
        config: ClientConfig,
        subject: str,
        body: str,
    ) -> GmailSendResult: ...


def _build_mime(*, sender: str, to: str, subject: str, body: str) -> str:
    msg = email.mime.text.MIMEText(body, _charset="utf-8")
    msg["to"] = to
    msg["from"] = sender
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return raw


def _access_token(refresh_token: str, client_id: str, client_secret: str) -> str:
    r = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=15.0,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _real_sender(
    *,
    lead: Lead,
    config: ClientConfig,
    subject: str,
    body: str,
) -> GmailSendResult:
    settings = get_settings()
    if not (settings.gmail_client_id and settings.gmail_client_secret and settings.gmail_refresh_token):
        raise RuntimeError(
            "Gmail OAuth env vars are unset. Configure them, or call `set_sender(stub)`."
        )
    token = _access_token(
        settings.gmail_refresh_token, settings.gmail_client_id, settings.gmail_client_secret
    )
    raw = _build_mime(
        sender=config.sender_email, to=str(lead.email), subject=subject, body=body
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    if config.auto_response_policy is AutoResponsePolicy.SEND:
        url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
        payload: dict[str, Any] = {"raw": raw}
        mode = "send"
    else:
        url = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
        payload = {"message": {"raw": raw}}
        mode = "draft"

    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=20.0)
    except httpx.RequestError as e:
        return GmailSendResult(ok=False, mode=mode, error=f"network: {e}")

    if r.status_code in (200, 201):
        data = r.json()
        # `drafts` returns {"id":..., "message":{...}}; `send` returns the message.
        msg = data.get("message", data)
        return GmailSendResult(
            ok=True,
            mode=mode,
            message_id=msg.get("id"),
            thread_id=msg.get("threadId"),
        )
    log.warning("gmail_failed", status=r.status_code, body=r.text[:500])
    return GmailSendResult(
        ok=False, mode=mode, error=f"{r.status_code}: {r.text[:500]}"
    )


_sender_fn: _SenderFn = _real_sender


def set_sender(fn: _SenderFn | None) -> None:
    global _sender_fn
    _sender_fn = fn or _real_sender


def send_or_draft(
    *,
    lead: Lead,
    config: ClientConfig,
    subject: str,
    body: str,
) -> GmailSendResult:
    return _sender_fn(lead=lead, config=config, subject=subject, body=body)
