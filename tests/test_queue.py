from __future__ import annotations

import time

from src.models import Lead
from src.queue import LeadQueue


def _make_lead() -> Lead:
    return Lead(client_id="acme", source_plugin="wpforms", email="jane@example.com")


def test_enqueue_and_pop_roundtrip() -> None:
    q = LeadQueue()
    lead = _make_lead()
    q.enqueue(lead)
    popped = q.pop(timeout=1)
    assert popped is not None
    got_lead, retries, kind = popped
    assert got_lead.lead_id == lead.lead_id
    assert retries == 0
    assert kind == "new"


def test_schedule_retry_is_invisible_until_due() -> None:
    q = LeadQueue()
    lead = _make_lead()
    q.schedule_retry(lead, retries=1, delay_seconds=60)
    # Nothing on the main queue
    assert q.pop(timeout=1) is None
    # Nothing in the retry zset is due yet
    assert q.drain_due_retries() == []
    # Pretend we waited
    due = q.drain_due_retries(now=time.time() + 120)
    assert len(due) == 1
    got, retries, kind = due[0]
    assert got.lead_id == lead.lead_id
    assert retries == 1
    assert kind == "retry"


def test_dead_letter_persists() -> None:
    q = LeadQueue()
    lead = _make_lead()
    q.dead_letter(lead, reason="testing")
    assert q.queue_lengths()["dead_letter"] == 1


def test_client_index_is_used_by_listing() -> None:
    q = LeadQueue()
    lead = _make_lead()
    q.enqueue(lead)
    leads = q.list_leads_for_client("acme")
    assert any(item.lead_id == lead.lead_id for item in leads)
