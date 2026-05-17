"""Worker — consumes the Redis queue and drives the lead pipeline.

Steps (audit BEFORE every side effect):

  1. validate email
  2. enrich
  3. push to CRM (re-queue with backoff on retryable failure)
  4. draft auto-response via Claude
  5. send / draft via Gmail
  6. schedule follow-up (just persists state — actual sending is the
     scheduler's job)

Run with:
    python -m src.worker          # blocking main loop
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from datetime import datetime, timezone

from .adapters.crm import get_crm_adapter
from .audit.writer import audit_step
from .config_loader import ClientConfigError, load_client_config
from .drafter import draft_auto_response
from .enrich import enrich_lead, validate_email_address
from .logging_setup import configure_logging, get_logger
from .models import AuditStatus, ClientConfig, Lead, LeadStatus
from .queue import LeadQueue, get_queue
from .secrets import SecretNotFoundError, get_secret
from .sender import send_or_draft
from .settings import get_settings

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# One lead end-to-end
# ---------------------------------------------------------------------------


async def process_lead(
    lead: Lead,
    *,
    retries: int = 0,
    kind: str = "new",
    queue: LeadQueue | None = None,
) -> Lead:
    queue = queue or get_queue()
    try:
        config = load_client_config(lead.client_id)
    except ClientConfigError as e:
        audit_step(
            lead_id=lead.lead_id,
            client_id=lead.client_id,
            step="load_config",
            status=AuditStatus.FAILED,
            error=str(e),
        )
        queue.dead_letter(lead, reason=f"config_load: {e}")
        return lead

    # If this is a retry-only envelope, jump straight to CRM push.
    if kind == "retry":
        await _crm_push(lead, config=config, retries=retries, queue=queue)
        return lead

    await _validate_email(lead, queue=queue)
    if lead.status is LeadStatus.INVALID_EMAIL:
        return lead

    _enrich(lead)

    await _crm_push(lead, config=config, retries=retries, queue=queue)
    if lead.status is LeadStatus.CRM_DEAD_LETTER:
        # We still send the auto-response; never drop the customer experience
        # because a CRM is down. The dead-letter ensures ops will fix it.
        log.warning("crm_dead_letter_continuing_with_autoresponse", lead_id=lead.lead_id)

    _draft_and_send(lead, config=config, queue=queue)
    return lead


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


async def _validate_email(lead: Lead, *, queue: LeadQueue) -> None:
    audit_step(
        lead_id=lead.lead_id,
        client_id=lead.client_id,
        step="validate_email",
        status=AuditStatus.STARTED,
    )
    try:
        result = validate_email_address(
            str(lead.email),
            check_deliverability=get_settings().email_check_deliverability,
        )
    except Exception as e:
        result = None
        log.warning("validator_crashed", lead_id=lead.lead_id, error=str(e))

    if result is None or not result.valid:
        lead.email_valid = False
        lead.status = LeadStatus.INVALID_EMAIL
        queue._index(lead)
        audit_step(
            lead_id=lead.lead_id,
            client_id=lead.client_id,
            step="validate_email",
            status=AuditStatus.FAILED,
            error=(result.reason if result else "validator crashed"),
        )
        return

    lead.email_valid = True
    lead.email_disposable = result.disposable
    lead.status = LeadStatus.VALIDATING
    queue._index(lead)
    audit_step(
        lead_id=lead.lead_id,
        client_id=lead.client_id,
        step="validate_email",
        status=AuditStatus.OK,
        detail={"disposable": result.disposable},
    )


def _enrich(lead: Lead) -> None:
    audit_step(
        lead_id=lead.lead_id,
        client_id=lead.client_id,
        step="enrich",
        status=AuditStatus.STARTED,
    )
    info = enrich_lead(lead)
    lead.enrichment.update(info)
    lead.status = LeadStatus.ENRICHED
    get_queue()._index(lead)
    audit_step(
        lead_id=lead.lead_id,
        client_id=lead.client_id,
        step="enrich",
        status=AuditStatus.OK,
        detail=info,
    )


async def _crm_push(
    lead: Lead,
    *,
    config: ClientConfig,
    retries: int,
    queue: LeadQueue,
) -> None:
    audit_step(
        lead_id=lead.lead_id,
        client_id=lead.client_id,
        step="crm_push",
        status=AuditStatus.STARTED,
        detail={"crm": config.crm, "retry": retries},
    )
    try:
        credentials = get_secret(config.crm_credentials_secret_id)
    except SecretNotFoundError as e:
        audit_step(
            lead_id=lead.lead_id,
            client_id=lead.client_id,
            step="crm_push",
            status=AuditStatus.FAILED,
            error=str(e),
        )
        # Missing credentials is a config problem — dead-letter, don't retry.
        lead.status = LeadStatus.CRM_DEAD_LETTER
        queue.dead_letter(lead, reason=f"missing_credentials: {e}")
        return

    adapter = get_crm_adapter(config.crm)
    result = await adapter.push(lead, credentials=credentials, config=config)

    if result.ok:
        lead.status = LeadStatus.CRM_PUSHED
        queue._index(lead)
        audit_step(
            lead_id=lead.lead_id,
            client_id=lead.client_id,
            step="crm_push",
            status=AuditStatus.OK,
            detail={"external_id": result.external_id},
        )
        return

    settings = get_settings()
    if not result.retryable or retries >= settings.crm_max_retries:
        lead.status = LeadStatus.CRM_DEAD_LETTER
        queue.dead_letter(lead, reason=f"crm_failed: {result.error}")
        audit_step(
            lead_id=lead.lead_id,
            client_id=lead.client_id,
            step="crm_push",
            status=AuditStatus.FAILED,
            error=result.error,
            detail={"retries": retries, "dead_letter": True},
        )
        return

    delay = settings.crm_backoff_seconds[
        min(retries, len(settings.crm_backoff_seconds) - 1)
    ]
    queue.schedule_retry(lead, retries=retries + 1, delay_seconds=delay)
    lead.status = LeadStatus.CRM_FAILED
    queue._index(lead)
    audit_step(
        lead_id=lead.lead_id,
        client_id=lead.client_id,
        step="crm_push",
        status=AuditStatus.FAILED,
        error=result.error,
        detail={"retries": retries, "next_delay_s": delay},
    )


def _draft_and_send(lead: Lead, *, config: ClientConfig, queue: LeadQueue) -> None:
    audit_step(
        lead_id=lead.lead_id,
        client_id=lead.client_id,
        step="draft_autoresponse",
        status=AuditStatus.STARTED,
    )
    try:
        drafted = draft_auto_response(lead, config=config)
    except Exception as e:
        audit_step(
            lead_id=lead.lead_id,
            client_id=lead.client_id,
            step="draft_autoresponse",
            status=AuditStatus.FAILED,
            error=str(e),
        )
        return
    audit_step(
        lead_id=lead.lead_id,
        client_id=lead.client_id,
        step="draft_autoresponse",
        status=AuditStatus.OK,
        detail={"subject": drafted.subject, "model": drafted.model},
    )

    audit_step(
        lead_id=lead.lead_id,
        client_id=lead.client_id,
        step="send_autoresponse",
        status=AuditStatus.STARTED,
        detail={"mode": config.auto_response_policy.value},
    )
    result = send_or_draft(
        lead=lead, config=config, subject=drafted.subject, body=drafted.body
    )
    if not result.ok:
        audit_step(
            lead_id=lead.lead_id,
            client_id=lead.client_id,
            step="send_autoresponse",
            status=AuditStatus.FAILED,
            error=result.error,
        )
        return

    lead.auto_response_sent_at = datetime.now(timezone.utc)
    lead.auto_response_thread_id = result.thread_id
    lead.status = LeadStatus.AUTORESPONDED
    queue._index(lead)
    audit_step(
        lead_id=lead.lead_id,
        client_id=lead.client_id,
        step="send_autoresponse",
        status=AuditStatus.OK,
        detail={
            "mode": result.mode,
            "message_id": result.message_id,
            "thread_id": result.thread_id,
        },
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


_should_stop = False


def _install_signal_handlers() -> None:
    def _handler(signum, frame):  # noqa: ANN001
        global _should_stop
        log.info("worker_shutdown_signal", signum=signum)
        _should_stop = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        # On Windows / non-main threads SIGTERM isn't installable; skip.
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, _handler)


async def main() -> int:
    configure_logging(get_settings().log_level)
    _install_signal_handlers()
    queue = get_queue()
    log.info("worker_started")

    while not _should_stop:
        # Drain due retries first, then take one new lead.
        for lead, retries, kind in queue.drain_due_retries():
            await process_lead(lead, retries=retries, kind=kind, queue=queue)

        item = queue.pop(timeout=2)
        if item is None:
            continue
        lead, retries, kind = item
        try:
            await process_lead(lead, retries=retries, kind=kind, queue=queue)
        except Exception as e:
            log.error("worker_unhandled", lead_id=lead.lead_id, error=str(e))
            audit_step(
                lead_id=lead.lead_id,
                client_id=lead.client_id,
                step="worker_unhandled",
                status=AuditStatus.FAILED,
                error=str(e),
            )

    log.info("worker_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
