"""Loading client A's config never reads client B's. Required test from CLAUDE.md."""

from __future__ import annotations

import pytest

from src import config_loader
from src.config_loader import ClientConfigError, load_client_config


def test_two_clients_load_independently(write_client) -> None:
    write_client("acme", crm="hubspot")
    write_client("globex", crm="pipedrive")

    a = load_client_config("acme")
    b = load_client_config("globex")

    assert a.client_id == "acme"
    assert a.crm == "hubspot"
    assert b.client_id == "globex"
    assert b.crm == "pipedrive"
    # No cross-contamination
    assert a.tone_profile.id != b.tone_profile.id


def test_missing_required_field_fails_loud(_isolated_config_dir) -> None:
    (_isolated_config_dir / "bad.yaml").write_text(
        # Missing crm, sender_email, tone_profile, ...
        "client_id: bad\ndomain: bad.com\nform_plugin: wpforms\n",
        encoding="utf-8",
    )
    config_loader.clear_config_cache()
    with pytest.raises(ClientConfigError):
        load_client_config("bad")


def test_file_with_wrong_client_id_is_rejected(_isolated_config_dir) -> None:
    """Defends against 'mv acme.yaml zenith.yaml' — file contents must match
    the filename / requested id, otherwise we'd cross-deliver leads."""
    (_isolated_config_dir / "zenith.yaml").write_text(
        """\
client_id: acme
domain: acme.com
form_plugin: wpforms
crm: hubspot
crm_credentials_secret_id: x
sender_email: a@a.com
tone_profile:
  id: t
  description: x
""",
        encoding="utf-8",
    )
    config_loader.clear_config_cache()
    with pytest.raises(ClientConfigError):
        load_client_config("zenith")


def test_suspicious_client_id_rejected() -> None:
    with pytest.raises(ClientConfigError):
        load_client_config("../etc/passwd")
