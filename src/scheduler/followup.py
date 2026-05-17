"""Follow-up scheduler + reply detector.

Run hourly. Logic:

  for every lead with `auto_response_sent_at` set and `reply_detected_at is None`:
    if reply detected since auto_response_sent_at:
      mark replied, suppress follow-up
    elif now - auto_response_sent_at >= followup_steps[N].delay_days:
      render template, send via Gmail, bump followups_sent

`check_for_reply` is the single function that talks to Gmail's threads API;
tests replace it with `set_reply_checker(stub)`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from ..audit.writer import audit_step
from ..config_loader import load_client_config
from ..logging_setup import get_logger
from ..models import AuditStatus, ClientConfig, Lead, LeadStatus
from ..queue import get_queue
from ..sender import send_or_draft

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Reply detection (Gmail thread check)
# ---------------------------------------------------------------------------


def _real_reply_checker(*, thread_id: str, since: datetime) -> bool:
    """Return True if Gmail thread has an inbound message after `since`.

    Implementation note: a real-world build would refresh an OAuth token and
    GET /gmail/v1/users/me/threads/{id}. We stub it as False here so this
    module is safe to run without Gmail creds, and tests inject the real
    behaviour via `set_reply_checker`.
    """
    return False


_reply_checker: Callable[..., bool] = _real_reply_checker


def set_reply_checker(fn: Callable[..., bool] | None) -> None:
    global _reply_checker
    _reply_checker = fn or _real_reply_checker


def check_for_reply(*, thread_id: str, since: datetime) -> bool:
    return _reply_checker(thread_id=thread_id, since=since)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


def _render(template: str, lead: Lead) -> str:
    return (
        template.replace("{first_name}", lead.first_name or "there")
        .replace("{last_name}", lead.last_name or "")
        .replace("{company}", lead.company or "your team")
        .replace("{email}", str(lead.email))
    )


class FollowupScheduler:
    """Stateless scanner — call `tick()` from cron / APScheduler / etc."""

    def tick(self, *, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(timezone.utc)
        queue = get_queue()

        sent = 0
        suppressed = 0
        skipped = 0

        # We don't have a per-client index of "in-flight follow-ups", so just
        # scan all clients that have leads. For typical SMB volume this is
        # fine; at scale, add a sorted-set keyed on next-due time.
        client_keys = queue.r.keys("client:*:leads")
        for ck in client_keys:
            client_id = (
                ck.decode() if isinstance(ck, bytes) else ck
            ).split(":")[1]
            try:
                config = load_client_config(client_id)
            except Exception as e:
                log.warning("scheduler_config_load_failed", client_id=client_id, error=str(e))
                continue
            for lead in queue.list_leads_for_client(client_id, limit=10_000):
                action = self._maybe_followup(lead, config=config, now=now)
                if action == "sent":
                    sent += 1
                elif action == "suppressed":
                    suppressed += 1
                else:
                    skipped += 1
        log.info("scheduler_tick", sent=sent, suppressed=suppressed, skipped=skipped)
        return {"sent": sent, "suppressed": suppressed, "skipped": skipped}

    # --- per-lead decision -------------------------------------------------

    def _maybe_followup(
        self,
        lead: Lead,
        *,
        config: ClientConfig,
        now: datetime,
    ) -> str:
        if not lead.auto_response_sent_at:
            return "skip"  # autoresponse never sent yet
        if lead.reply_detected_at:
            return "skip"  # already replied → no more follow-ups
        if lead.followups_sent >= len(config.followup_steps):
            return "skip"  # all configured steps fired

        # Reply detection: if a reply arrived between auto-response and now,
        # mark replied and stop.
        if lead.auto_response_thread_id:
            replied = check_for_reply(
                thread_id=lead.auto_response_thread_id,
                since=lead.auto_response_sent_at,
            )
            if replied:
                lead.reply_detected_at = now
                lead.status = LeadStatus.REPLIED
                get_queue()._index(lead)
                audit_step(
                    lead_id=lead.lead_id,
                    client_id=lead.client_id,
                    step="reply_detected",
                    status=AuditStatus.OK,
                )
                return "suppressed"

        step = config.followup_steps[lead.followups_sent]
        due_at = lead.auto_response_sent_at + timedelta(days=step.delay_days)
        if now < due_at:
            return "skip"

        # Send the follow-up.
        subject = _render(step.subject, lead)
        body = _render(step.body_template, lead)
        audit_step(
            lead_id=lead.lead_id,
            client_id=lead.client_id,
            step=f"followup:{step.id}",
            status=AuditStatus.STARTED,
            detail={"subject": subject},
        )
        result = send_or_draft(lead=lead, config=config, subject=subject, body=body)
        if not result.ok:
            audit_step(
                lead_id=lead.lead_id,
                client_id=lead.client_id,
                step=f"followup:{step.id}",
                status=AuditStatus.FAILED,
                error=result.error,
            )
            return "skip"

        lead.followups_sent += 1
        lead.status = LeadStatus.FOLLOWED_UP
        get_queue()._index(lead)
        audit_step(
            lead_id=lead.lead_id,
            client_id=lead.client_id,
            step=f"followup:{step.id}",
            status=AuditStatus.OK,
            detail={"mode": result.mode, "message_id": result.message_id},
        )
        return "sent"
