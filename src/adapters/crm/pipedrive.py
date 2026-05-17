"""Pipedrive CRM adapter.

Pipedrive uses an `api_token` query parameter against `https://{domain}.pipedrive.com/api/v1`.
Credentials shape: `{"api_token": "...", "company_domain": "..."}`.
"""

from __future__ import annotations

from typing import Any

import httpx

from ...models import ClientConfig, Lead, PushResult
from .base import CRMAdapter, is_retryable_status, register_crm_adapter


def _base_url(credentials: dict[str, Any]) -> str:
    domain = credentials.get("company_domain")
    if not domain:
        raise ValueError("Pipedrive credentials missing company_domain")
    return f"https://{domain}.pipedrive.com/api/v1"


def _api_token(credentials: dict[str, Any]) -> str:
    tok = credentials.get("api_token")
    if not tok:
        raise ValueError("Pipedrive credentials missing api_token")
    return tok


def _person_body(lead: Lead) -> dict[str, Any]:
    name_parts = [p for p in (lead.first_name, lead.last_name) if p]
    body: dict[str, Any] = {
        "name": " ".join(name_parts) or str(lead.email),
        "email": [{"value": str(lead.email), "primary": True, "label": "work"}],
    }
    if lead.phone:
        body["phone"] = [{"value": lead.phone, "primary": True, "label": "work"}]
    if lead.company:
        body["org_id"] = None  # caller can map orgs later
    return body


@register_crm_adapter
class PipedriveAdapter(CRMAdapter):
    name = "pipedrive"

    async def push(
        self,
        lead: Lead,
        *,
        credentials: dict[str, Any],
        config: ClientConfig,
    ) -> PushResult:
        try:
            url = f"{_base_url(credentials)}/persons"
            params = {"api_token": _api_token(credentials)}
        except ValueError as e:
            return PushResult(ok=False, crm=self.name, error=str(e), retryable=False)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(url, params=params, json=_person_body(lead))
        except httpx.RequestError as e:
            return PushResult(ok=False, crm=self.name, error=f"network: {e}", retryable=True)

        if r.status_code in (200, 201):
            data = r.json()
            external = str((data.get("data") or {}).get("id"))
            return PushResult(ok=True, crm=self.name, external_id=external, raw_response=data)

        return PushResult(
            ok=False,
            crm=self.name,
            error=f"{r.status_code}: {r.text[:500]}",
            retryable=is_retryable_status(r.status_code),
            raw_response={"status": r.status_code, "body": r.text[:2000]},
        )
