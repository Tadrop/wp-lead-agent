"""End-to-end happy-path through the worker."""

from __future__ import annotations

import json

import pytest

from src.adapters.crm.base import _REGISTRY as CRM_REGISTRY
from src.audit import get_audit_trail
from src.models import AuditStatus, Lead, LeadStatus, PushResult
from src.queue import get_queue
from src.worker import process_lead


class _StubCRM:
    name = "stub"

    async def push(self, lead, *, credentials, config):
        return PushResult(ok=True, crm="stub", external_id="ext_1")


@pytest.mark.asyncio
async def test_happy_path_runs_all_steps(write_client, monkeypatch) -> None:
    write_client("acme", crm="stub")
    monkeypatch.setitem(CRM_REGISTRY, "stub", _StubCRM())
    monkeypatch.setenv("ACME_CREDS_JSON", json.dumps({"api_token": "x"}))

    lead = Lead(
        client_id="acme",
        source_plugin="wpforms",
        email="jane@example.com",
        first_name="Jane",
        message="hello",
    )
    await process_lead(lead)

    audit = get_audit_trail().read_for_lead("acme", lead.lead_id)
    steps = {e.step for e in audit if e.status is AuditStatus.OK}
    # Every key step audited
    for required in {"validate_email", "enrich", "crm_push", "draft_autoresponse", "send_autoresponse"}:
        assert required in steps, f"{required} missing from audit; have {steps}"

    fresh = get_queue().get_lead(lead.lead_id)
    assert fresh is not None
    assert fresh.status is LeadStatus.AUTORESPONDED
    assert fresh.auto_response_sent_at is not None


@pytest.mark.asyncio
async def test_invalid_email_skips_crm(write_client, monkeypatch) -> None:
    write_client("acme", crm="stub")

    calls = []

    class _CRM:
        name = "stub"

        async def push(self, lead, *, credentials, config):
            calls.append(lead)
            return PushResult(ok=True, crm="stub")

    monkeypatch.setitem(CRM_REGISTRY, "stub", _CRM())
    monkeypatch.setenv("ACME_CREDS_JSON", json.dumps({"x": "y"}))

    # Pydantic will reject `test@test` at construction — so build a Lead with a
    # syntactically-valid but DNS-undeliverable email and stub the validator.
    from src import worker as worker_mod
    from src.enrich.email_validator import EmailValidationResult

    monkeypatch.setattr(
        worker_mod,
        "validate_email_address",
        lambda email, *, check_deliverability=True: EmailValidationResult(
            email=email, valid=False, disposable=False, reason="no-mx"
        ),
    )

    lead = Lead(client_id="acme", source_plugin="wpforms", email="jane@example.com")
    await process_lead(lead)

    assert calls == []
    fresh = get_queue().get_lead(lead.lead_id)
    assert fresh.status is LeadStatus.INVALID_EMAIL


@pytest.mark.asyncio
async def test_two_clients_dont_bleed(write_client, monkeypatch) -> None:
    """Two consecutive leads for two clients use different configs / tones."""
    write_client("acme", crm="stub", policy="send")
    write_client("globex", crm="stub", policy="draft")
    monkeypatch.setitem(CRM_REGISTRY, "stub", _StubCRM())
    monkeypatch.setenv("ACME_CREDS_JSON", json.dumps({"x": "y"}))
    monkeypatch.setenv("GLOBEX_CREDS_JSON", json.dumps({"x": "y"}))

    lead_a = Lead(client_id="acme", source_plugin="wpforms", email="a@a.com", first_name="A")
    lead_b = Lead(client_id="globex", source_plugin="wpforms", email="b@b.com", first_name="B")

    await process_lead(lead_a)
    await process_lead(lead_b)

    # The stub sender records every send — check the policy stayed per-client.
    from src.sender.gmail import _sender_fn  # type: ignore

    log = _sender_fn.log  # type: ignore[attr-defined]
    by_client = {e["client_id"]: e["policy"] for e in log}
    assert by_client == {"acme": "send", "globex": "draft"}
