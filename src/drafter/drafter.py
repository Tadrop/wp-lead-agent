"""Claude-powered auto-response drafter.

The drafter is the **only** module that calls Anthropic. It:

  * Builds a strict per-client system prompt from `ClientConfig.tone_profile`
  * Uses **prompt caching** on the system prompt so warm calls are cheap
  * Returns a `DraftedEmail` (subject + body) — never sends anything
    (sending lives in `sender`)

`set_drafter(...)` lets tests inject a stub without touching Anthropic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from anthropic import Anthropic

from ..logging_setup import get_logger
from ..models import ClientConfig, Lead
from ..settings import get_settings

log = get_logger(__name__)


@dataclass
class DraftedEmail:
    subject: str
    body: str
    model: str
    cache_hit_input_tokens: int = 0


class _DrafterFn(Protocol):
    def __call__(self, lead: Lead, *, config: ClientConfig) -> DraftedEmail: ...


def _build_system_prompt(config: ClientConfig) -> str:
    tp = config.tone_profile
    samples = "\n".join(f"- {s}" for s in tp.sample_phrases) if tp.sample_phrases else "(none)"
    sig = tp.signature or f"The team at {config.domain}"
    return (
        "You are an email auto-response drafter for a small business.\n"
        "You write the FIRST reply to a brand-new lead who just submitted a "
        "contact form. The reply must feel personal — never a generic "
        "template — and must match this brand's voice exactly.\n\n"
        f"Brand / client domain: {config.domain}\n"
        f"Tone profile id: {tp.id}\n"
        f"Tone description: {tp.description}\n"
        "Sample phrases this brand uses:\n"
        f"{samples}\n\n"
        f"Sign every email with: {sig}\n\n"
        "Output format — and nothing else — exactly this:\n"
        "SUBJECT: <one line>\n"
        "---\n"
        "<email body, plain text, 2–4 short paragraphs>\n"
    )


def _build_user_message(lead: Lead) -> str:
    name = " ".join(p for p in (lead.first_name, lead.last_name) if p) or "there"
    enrichment_bits = []
    if lead.enrichment.get("company_guess"):
        enrichment_bits.append(f"likely company: {lead.enrichment['company_guess']}")
    if lead.enrichment.get("is_personal_email"):
        enrichment_bits.append("uses a personal email")
    enrichment = "; ".join(enrichment_bits) or "(none)"

    return (
        "Draft a reply to this new lead.\n\n"
        f"Name: {name}\n"
        f"Email: {lead.email}\n"
        f"Company: {lead.company or '(not given)'}\n"
        f"Subject: {lead.subject or '(none)'}\n"
        f"Message:\n{lead.message or '(no message provided)'}\n\n"
        f"Enrichment: {enrichment}\n"
    )


def _parse_output(text: str) -> tuple[str, str]:
    text = text.strip()
    if "SUBJECT:" in text and "---" in text:
        subj_part, body_part = text.split("---", 1)
        subject = subj_part.replace("SUBJECT:", "").strip()
        body = body_part.strip()
    else:
        # Fallback: take first line as subject.
        lines = text.splitlines()
        subject = lines[0].strip() or "Thanks for getting in touch"
        body = "\n".join(lines[1:]).strip()
    subject = subject or "Thanks for getting in touch"
    body = body or "Thanks for reaching out — we'll be in touch shortly."
    return subject, body


def _real_drafter(lead: Lead, *, config: ClientConfig) -> DraftedEmail:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is unset. Set it, or call `set_drafter(stub)` in tests."
        )
    client = Anthropic(api_key=settings.anthropic_api_key)

    system_prompt = _build_system_prompt(config)
    user_message = _build_user_message(lead)

    # Prompt-caching: mark the static system prompt as `cache_control` so
    # subsequent leads for the same client are served from the cache.
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=800,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_message}],
    )

    text_parts = [b.text for b in response.content if getattr(b, "type", "") == "text"]
    text = "".join(text_parts)
    subject, body = _parse_output(text)
    usage = getattr(response, "usage", None)
    cache_hit = int(getattr(usage, "cache_read_input_tokens", 0) or 0) if usage else 0

    log.info(
        "drafter_response",
        client_id=config.client_id,
        lead_id=lead.lead_id,
        cache_read_input_tokens=cache_hit,
        model=settings.anthropic_model,
    )
    return DraftedEmail(
        subject=subject,
        body=body,
        model=settings.anthropic_model,
        cache_hit_input_tokens=cache_hit,
    )


# Pluggable for tests
_drafter_fn: _DrafterFn = _real_drafter


def set_drafter(fn: _DrafterFn | None) -> None:
    """Inject a stub drafter (tests). Pass None to restore the real one."""
    global _drafter_fn
    _drafter_fn = fn or _real_drafter


def draft_auto_response(lead: Lead, *, config: ClientConfig) -> DraftedEmail:
    return _drafter_fn(lead, config=config)
