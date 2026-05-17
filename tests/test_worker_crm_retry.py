"""CRM 503 → re-queued and eventually pushed. Required test."""

from __future__ import annotations

import json

import pytest

from src.adapters.crm.base import _REGISTRY as CRM_REGISTRY
from src.audit import get_audit_trail
from src.models import AuditStatus, ClientConfig, Lead, PushResult
from src.queue import get_queue
from src.worker import process_lead


class _FlakeyCRM:
    """A CRM stub that fails N times with a 503-like error, then succeeds."""

    name = "flakey"

    def __init__(self, fail_count: int) -> None:
        self.fail_count = fail_count
        self.calls = 0

    async def push(self, lead: Lead, *, credentials, config: ClientConfig) -> PushResult:
        self.calls += 1
        if self.calls <= self.fail_count:
            return PushResult(ok=False, crm="flakey", error="503: down", retryable=True)
        return PushResult(ok=True, crm="flakey", external_id=f"ext_{self.calls}")


@pytest.mark.asyncio
async def test_crm_failure_requeues_then_succeeds(write_client, monkeypatch) -> None:
    write_client("acme", crm="flakey")
    # Register flakey adapter for this test
    flakey = _FlakeyCRM(fail_count=2)
    monkeypatch.setitem(CRM_REGISTRY, "flakey", flakey)

    # Provide creds
    monkeypatch.setenv("ACME_CREDS_JSON", json.dumps({"api_token": "x"}))

    lead = Lead(client_id="acme", source_plugin="wpforms", email="jane@example.com")
    queue = get_queue()

    # First attempt: fails retryably and re-queues.
    await process_lead(lead, retries=0, kind="new", queue=queue)
    assert flakey.calls == 1
    assert queue.queue_lengths()["scheduled_retries"] == 1
    assert queue.queue_lengths()["dead_letter"] == 0

    # Simulate retry 1: still fails, re-queued again.
    due = queue.drain_due_retries(now=10**12)  # very-far-future cutoff
    assert len(due) == 1
    lead2, retries, kind = due[0]
    await process_lead(lead2, retries=retries, kind=kind, queue=queue)
    assert flakey.calls == 2
    assert queue.queue_lengths()["scheduled_retries"] == 1

    # Simulate retry 2: succeeds.
    due = queue.drain_due_retries(now=10**12)
    lead3, retries, kind = due[0]
    await process_lead(lead3, retries=retries, kind=kind, queue=queue)
    assert flakey.calls == 3

    # Audit trail must show the eventual success.
    audit = get_audit_trail().read_for_lead("acme", lead.lead_id)
    steps_ok = [e for e in audit if e.step == "crm_push" and e.status is AuditStatus.OK]
    assert steps_ok, "expected an OK crm_push audit entry"
    assert queue.queue_lengths()["dead_letter"] == 0


@pytest.mark.asyncio
async def test_crm_permanent_failure_dead_letters(write_client, monkeypatch) -> None:
    write_client("acme", crm="flakey")

    class _AlwaysFail:
        name = "flakey"

        async def push(self, lead, *, credentials, config):
            return PushResult(ok=False, crm="flakey", error="400: bad", retryable=False)

    monkeypatch.setitem(CRM_REGISTRY, "flakey", _AlwaysFail())
    monkeypatch.setenv("ACME_CREDS_JSON", json.dumps({"api_token": "x"}))

    lead = Lead(client_id="acme", source_plugin="wpforms", email="jane@example.com")
    queue = get_queue()
    await process_lead(lead, retries=0, kind="new", queue=queue)
    assert queue.queue_lengths()["dead_letter"] == 1
