"""Every plugin variant must normalize to the same Lead shape."""

from __future__ import annotations

import pytest

from src.adapters.forms import UnknownPluginError, get_form_adapter
from src.models import Lead

from .fixtures.payloads import (
    FLUENT,
    GRAVITY_MAPPED,
    WPFORMS_FLAT,
    WPFORMS_NESTED,
)


def _core(lead: Lead) -> dict:
    """The subset every adapter MUST fill identically."""
    return {
        "email": str(lead.email),
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "phone": lead.phone,
        "company": lead.company,
        "message": lead.message,
    }


def test_wpforms_nested_normalizes() -> None:
    lead = get_form_adapter("wpforms").normalize(WPFORMS_NESTED, client_id="acme")
    assert lead.source_plugin == "wpforms"
    assert _core(lead) == {
        "email": "jane@example.com",
        "first_name": "Jane",
        "last_name": "Doe",
        "phone": "+1 555 0100",
        "company": "Example Co",
        "message": "Hello, I'd like to learn more.",
    }


def test_all_plugins_produce_identical_core_shape() -> None:
    a = get_form_adapter("wpforms").normalize(WPFORMS_NESTED, client_id="acme")
    b = get_form_adapter("wpforms").normalize(WPFORMS_FLAT, client_id="acme")
    c = get_form_adapter("gravity").normalize(GRAVITY_MAPPED, client_id="acme")
    d = get_form_adapter("fluent").normalize(FLUENT, client_id="acme")

    shapes = {_core(x)["email"]: _core(x) for x in (a, b, c, d)}
    # All four core dicts must be identical except for nothing — they
    # describe the same submission.
    assert all(s == _core(a) for s in shapes.values()), shapes


def test_unknown_plugin_raises() -> None:
    with pytest.raises(UnknownPluginError):
        get_form_adapter("squarespace")


def test_wpforms_payload_missing_email_rejects() -> None:
    with pytest.raises(ValueError):
        get_form_adapter("wpforms").normalize(
            {"fields": {"name": "Jane"}}, client_id="acme"
        )
