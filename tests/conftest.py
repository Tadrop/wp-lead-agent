"""Shared pytest fixtures.

* Redirect `client_config_dir` to a temp directory so we never touch
  real configs.
* Inject `fakeredis` so tests don't need a Redis server.
* Inject `AuditTrail` -> JSONL backed by tmp_path so each test is isolated.
* Stub the Claude drafter + Gmail sender so no network calls happen.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import fakeredis
import pytest

from src import config_loader
from src.audit import writer as audit_writer
from src.drafter import set_drafter
from src.drafter.drafter import DraftedEmail
from src.models import ClientConfig, Lead
from src.queue import queue as queue_mod
from src.scheduler.followup import set_reply_checker
from src.sender import set_sender
from src.sender.gmail import GmailSendResult
from src.settings import get_settings


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg_dir = tmp_path / "clients"
    cfg_dir.mkdir()
    settings = get_settings()
    monkeypatch.setattr(settings, "client_config_dir", cfg_dir)
    # Always run offline in tests — no live DNS for email validation.
    monkeypatch.setattr(settings, "email_check_deliverability", False)
    config_loader.clear_config_cache()
    yield cfg_dir
    config_loader.clear_config_cache()


@pytest.fixture(autouse=True)
def _fakeredis() -> Iterator[fakeredis.FakeRedis]:
    r = fakeredis.FakeRedis()
    queue_mod.set_redis(r)
    yield r
    queue_mod.set_redis(None)


@pytest.fixture(autouse=True)
def _isolated_audit(tmp_path: Path) -> Iterator[None]:
    trail = audit_writer.JsonlAuditTrail(tmp_path / "audit")
    audit_writer.set_audit_trail(trail)
    yield
    audit_writer.set_audit_trail(audit_writer.JsonlAuditTrail())


@pytest.fixture(autouse=True)
def _stub_drafter() -> Iterator[None]:
    def fake(lead: Lead, *, config: ClientConfig) -> DraftedEmail:
        return DraftedEmail(
            subject=f"Hi {lead.first_name or 'there'}",
            body=f"[{config.tone_profile.id}] We got your message.",
            model="stub",
        )

    set_drafter(fake)
    yield
    set_drafter(None)


@pytest.fixture(autouse=True)
def _stub_sender() -> Iterator[None]:
    sent_log: list[dict] = []

    def fake(*, lead, config, subject, body):
        sent_log.append(
            {
                "lead_id": lead.lead_id,
                "client_id": config.client_id,
                "subject": subject,
                "body": body,
                "policy": config.auto_response_policy.value,
            }
        )
        return GmailSendResult(
            ok=True,
            mode=config.auto_response_policy.value,
            message_id=f"msg_{lead.lead_id}",
            thread_id=f"thr_{lead.lead_id}",
        )

    set_sender(fake)
    # expose log on the fixture
    fake.log = sent_log  # type: ignore[attr-defined]
    yield
    set_sender(None)


@pytest.fixture
def write_client(_isolated_config_dir: Path):
    """Helper to write a client YAML and return its client_id."""

    def _write(client_id: str, *, crm: str = "hubspot", plugin: str = "wpforms", policy: str = "draft") -> str:
        yaml = f"""\
client_id: {client_id}
domain: {client_id}.com
form_plugin: {plugin}
crm: {crm}
crm_credentials_secret_id: {client_id}_creds
sender_email: hello@{client_id}.com
tone_profile:
  id: {client_id}_tone
  description: friendly
  sample_phrases:
    - "Thanks!"
  signature: "The {client_id} team"
auto_response_policy: {policy}
followup_steps:
  - id: f1
    delay_days: 3
    subject: "Following up {{first_name}}"
    body_template: "Hi {{first_name}}, just checking in."
"""
        (_isolated_config_dir / f"{client_id}.yaml").write_text(yaml, encoding="utf-8")
        config_loader.clear_config_cache()
        return client_id

    return _write


@pytest.fixture(autouse=True)
def _no_real_reply_check() -> Iterator[None]:
    set_reply_checker(lambda *, thread_id, since: False)
    yield
    set_reply_checker(None)
