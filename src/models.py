"""Core Pydantic models shared across the pipeline.

These models are the contract between every adapter, the queue, the worker
and the dashboard. Any new form plugin or CRM adapter MUST produce / accept
these shapes — no exceptions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Lead — normalized representation of a form submission
# ---------------------------------------------------------------------------


class LeadStatus(str, Enum):
    RECEIVED = "received"
    VALIDATING = "validating"
    INVALID_EMAIL = "invalid_email"
    ENRICHED = "enriched"
    CRM_PUSHED = "crm_pushed"
    CRM_FAILED = "crm_failed"
    CRM_DEAD_LETTER = "crm_dead_letter"
    AUTORESPONDED = "autoresponded"
    FOLLOWED_UP = "followed_up"
    REPLIED = "replied"
    DONE = "done"


class Lead(BaseModel):
    """Normalized lead shape — same regardless of source plugin."""

    model_config = ConfigDict(extra="forbid")

    lead_id: str = Field(default_factory=lambda: _new_id("lead"))
    client_id: str
    source_plugin: str  # "wpforms" | "gravity" | "fluent"
    source_form_id: str | None = None
    received_at: datetime = Field(default_factory=_utcnow)

    # Core contact fields
    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    company: str | None = None

    # Free-text fields
    message: str | None = None
    subject: str | None = None

    # Anything plugin-specific we want to keep around for the audit trail
    extra_fields: dict[str, Any] = Field(default_factory=dict)

    # Enrichment results (populated by the enricher)
    email_valid: bool | None = None
    email_disposable: bool | None = None
    enrichment: dict[str, Any] = Field(default_factory=dict)

    # Pipeline state
    status: LeadStatus = LeadStatus.RECEIVED
    auto_response_sent_at: datetime | None = None
    auto_response_thread_id: str | None = None
    followups_sent: int = 0
    reply_detected_at: datetime | None = None

    @field_validator("first_name", "last_name", "phone", "company", "message", "subject")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


# ---------------------------------------------------------------------------
# ClientConfig — loaded from per-client YAML
# ---------------------------------------------------------------------------


class AutoResponsePolicy(str, Enum):
    DRAFT = "draft"  # create a Gmail draft, do not send
    SEND = "send"  # send immediately


class RoutingRule(BaseModel):
    """A simple `when <field> matches <pattern> → assign to <owner>` rule."""

    model_config = ConfigDict(extra="forbid")

    when_field: str
    matches: str  # regex
    assign_to: str | None = None
    tag: str | None = None


class ToneProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    sample_phrases: list[str] = Field(default_factory=list)
    signature: str | None = None


class FollowupStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    delay_days: int = 3
    subject: str
    body_template: str


class ClientConfig(BaseModel):
    """Per-client config — one YAML file per client."""

    model_config = ConfigDict(extra="forbid")

    client_id: str
    domain: str  # e.g. "acme.com" — used to resolve webhook → client
    form_plugin: str  # default plugin (wpforms / gravity / fluent)
    crm: str  # hubspot / pipedrive / mailchimp
    crm_credentials_secret_id: str
    sender_email: EmailStr
    tone_profile: ToneProfile
    auto_response_policy: AutoResponsePolicy = AutoResponsePolicy.DRAFT
    followup_steps: list[FollowupStep] = Field(default_factory=list)
    routing_rules: list[RoutingRule] = Field(default_factory=list)

    # Optional per-client webhook HMAC secret (overrides env default)
    webhook_secret: str | None = None


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


class AuditStatus(str, Enum):
    STARTED = "started"
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"


class AuditEntry(BaseModel):
    """Append-only audit row. Written BEFORE any side effect."""

    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(default_factory=lambda: _new_id("aud"))
    lead_id: str
    client_id: str
    step: str  # e.g. "webhook_received", "crm_push", "send_autoresponse"
    status: AuditStatus
    payload_hash: str | None = None
    error: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# CRM push result
# ---------------------------------------------------------------------------


class PushResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    crm: str
    external_id: str | None = None
    error: str | None = None
    retryable: bool = False
    raw_response: dict[str, Any] | None = None
