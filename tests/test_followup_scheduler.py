"""Reply detected → follow-up suppressed. Required test."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.models import Lead, LeadStatus
from src.queue import get_queue
from src.scheduler.followup import FollowupScheduler, set_reply_checker


def _ago(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def test_followup_fires_after_delay(write_client) -> None:
    write_client("acme")
    queue = get_queue()
    lead = Lead(
        client_id="acme",
        source_plugin="wpforms",
        email="jane@example.com",
        first_name="Jane",
        status=LeadStatus.AUTORESPONDED,
        auto_response_sent_at=_ago(4),  # past the 3-day delay
        auto_response_thread_id="thr_1",
    )
    queue._index(lead)

    stats = FollowupScheduler().tick()
    assert stats["sent"] == 1
    fresh = queue.get_lead(lead.lead_id)
    assert fresh.followups_sent == 1
    assert fresh.status is LeadStatus.FOLLOWED_UP


def test_reply_suppresses_followup(write_client) -> None:
    """If Gmail tells us a reply arrived, no follow-up should be sent."""
    write_client("acme")
    set_reply_checker(lambda *, thread_id, since: True)

    queue = get_queue()
    lead = Lead(
        client_id="acme",
        source_plugin="wpforms",
        email="jane@example.com",
        first_name="Jane",
        status=LeadStatus.AUTORESPONDED,
        auto_response_sent_at=_ago(5),
        auto_response_thread_id="thr_1",
    )
    queue._index(lead)

    stats = FollowupScheduler().tick()
    assert stats["sent"] == 0
    assert stats["suppressed"] == 1
    fresh = queue.get_lead(lead.lead_id)
    assert fresh.status is LeadStatus.REPLIED
    assert fresh.followups_sent == 0


def test_followup_not_fired_before_delay(write_client) -> None:
    write_client("acme")
    queue = get_queue()
    lead = Lead(
        client_id="acme",
        source_plugin="wpforms",
        email="jane@example.com",
        first_name="Jane",
        status=LeadStatus.AUTORESPONDED,
        auto_response_sent_at=_ago(1),  # only 1 day in, delay is 3
        auto_response_thread_id="thr_1",
    )
    queue._index(lead)

    stats = FollowupScheduler().tick()
    assert stats["sent"] == 0
    fresh = queue.get_lead(lead.lead_id)
    assert fresh.followups_sent == 0
