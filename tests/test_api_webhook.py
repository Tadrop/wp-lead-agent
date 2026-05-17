"""End-to-end webhook → queue test via the FastAPI test client."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from src.api import build_app
from src.queue import get_queue

from .fixtures.payloads import WPFORMS_NESTED


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_app())


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_unknown_client(client: TestClient) -> None:
    r = client.post(
        "/webhook/nobody/wpforms", content=json.dumps(WPFORMS_NESTED).encode()
    )
    assert r.status_code == 404


def test_webhook_invalid_signature(client: TestClient, write_client) -> None:
    write_client("acme")
    body = json.dumps(WPFORMS_NESTED).encode()
    r = client.post(
        "/webhook/acme/wpforms",
        content=body,
        headers={"X-Webhook-Signature": "sha256=deadbeef"},
    )
    assert r.status_code == 401


def test_webhook_unknown_plugin(client: TestClient, write_client, monkeypatch) -> None:
    write_client("acme")
    body = json.dumps(WPFORMS_NESTED).encode()
    # Use the env-default webhook secret
    from src.settings import get_settings

    secret = get_settings().webhook_default_secret
    r = client.post(
        "/webhook/acme/squarespace",
        content=body,
        headers={"X-Webhook-Signature": _sign(secret, body)},
    )
    assert r.status_code == 400


def test_webhook_happy_path_enqueues(client: TestClient, write_client) -> None:
    write_client("acme")
    body = json.dumps(WPFORMS_NESTED).encode()
    from src.settings import get_settings

    secret = get_settings().webhook_default_secret
    r = client.post(
        "/webhook/acme/wpforms",
        content=body,
        headers={"X-Webhook-Signature": _sign(secret, body)},
    )
    assert r.status_code == 202
    body_json = r.json()
    assert body_json["accepted"] is True
    assert body_json["lead_id"].startswith("lead_")

    assert get_queue().queue_lengths()["pending"] == 1


def test_health_endpoint(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    j = r.json()
    assert "wpforms" in j["form_plugins"]
    assert "queue" in j


def test_admin_dashboard_renders(client: TestClient, write_client) -> None:
    write_client("acme")
    r = client.get("/admin")
    assert r.status_code == 200
    assert "acme" in r.text
