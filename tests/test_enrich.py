from __future__ import annotations

from src.enrich.email_validator import validate_email_address
from src.enrich.enricher import enrich_lead
from src.models import Lead


def test_invalid_email_syntax() -> None:
    r = validate_email_address("not-an-email", check_deliverability=False)
    assert not r.valid


def test_valid_email_offline() -> None:
    r = validate_email_address("jane@example.com", check_deliverability=False)
    assert r.valid
    assert not r.disposable


def test_disposable_email_flagged() -> None:
    r = validate_email_address("user@mailinator.com", check_deliverability=False)
    assert r.valid
    assert r.disposable
    assert r.reason == "disposable"


def test_enrich_personal_email_guesses_no_company() -> None:
    lead = Lead(client_id="acme", source_plugin="wpforms", email="jane@gmail.com")
    info = enrich_lead(lead)
    assert info["is_personal_email"] is True
    assert "company_guess" not in info


def test_enrich_company_email_guesses_company() -> None:
    lead = Lead(client_id="acme", source_plugin="wpforms", email="ceo@acme-corp.com")
    info = enrich_lead(lead)
    assert info["is_personal_email"] is False
    assert info["company_guess"] == "Acme Corp"
