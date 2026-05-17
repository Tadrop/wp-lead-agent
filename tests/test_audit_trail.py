from __future__ import annotations

from pathlib import Path

from src.audit.writer import JsonlAuditTrail, audit_step, get_audit_trail, hash_payload
from src.models import AuditEntry, AuditStatus


def test_jsonl_append_only(tmp_path: Path) -> None:
    trail = JsonlAuditTrail(tmp_path / "audit")
    trail.write(AuditEntry(lead_id="l1", client_id="acme", step="webhook", status=AuditStatus.OK))
    trail.write(AuditEntry(lead_id="l1", client_id="acme", step="enrich", status=AuditStatus.OK))
    entries = trail.read("acme")
    assert [e.step for e in entries] == ["enrich", "webhook"]  # newest first


def test_payload_hash_is_stable() -> None:
    a = hash_payload({"a": 1, "b": [1, 2]})
    b = hash_payload({"b": [1, 2], "a": 1})
    assert a == b


def test_audit_step_writes_before_returning() -> None:
    entry = audit_step(
        lead_id="l1", client_id="acme", step="x", status=AuditStatus.OK, payload={"k": 1}
    )
    fetched = get_audit_trail().read_for_lead("acme", "l1")
    assert any(e.entry_id == entry.entry_id for e in fetched)
