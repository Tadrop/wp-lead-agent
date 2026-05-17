"""HubSpot CRM adapter.

Uses the v3 `/crm/v3/objects/contacts` endpoint with bearer-token auth.
Credentials shape: `{"access_token": "..."}` or `{"api_key": "..."}` (legacy).
"""

from __future__ import annotations

from typing import Any

import httpx

from ...models import ClientConfig, Lead, PushResult
from .base import CRMAdapter, is_retryable_status, register_crm_adapter

HUBSPOT_BASE = "https://api.hubapi.com"


def _bearer(credentials: dict[str, Any]) -> str:
    tok = credentials.get("access_token") or credentials.get("api_key")
    if not tok:
        raise ValueError("HubSpot credentials missing access_token/api_key")
    return f"Bearer {tok}"


def _lead_to_properties(lead: Lead) -> dict[str, Any]:
    props: dict[str, Any] = {
        "email": str(lead.email),
        "lifecyclestage": "lead",
        "hs_lead_source": f"web_form_{lead.source_plugin}",
    }
    if lead.first_name:
        props["firstname"] = lead.first_name
    if lead.last_name:
        props["lastname"] = lead.last_name
    if lead.phone:
        props["phone"] = lead.phone
    if lead.company:
        props["company"] = lead.company
    if lead.message:
        # HubSpot has no first-class "message" property — stash on notes.
        props["message"] = lead.message[:65000]
    return props


@register_crm_adapter
class HubSpotAdapter(CRMAdapter):
    name = "hubspot"

    async def push(
        self,
        lead: Lead,
        *,
        credentials: dict[str, Any],
        config: ClientConfig,
    ) -> PushResult:
        headers = {
            "Authorization": _bearer(credentials),
            "Content-Type": "application/json",
        }
        body = {"properties": _lead_to_properties(lead)}

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    f"{HUBSPOT_BASE}/crm/v3/objects/contacts",
                    headers=headers,
                    json=body,
                )
        except httpx.RequestError as e:
            return PushResult(ok=False, crm=self.name, error=f"network: {e}", retryable=True)

        # HubSpot returns 409 when the contact already exists — treat as success
        # and try to upsert via /upsert endpoint? Simpler: just return success.
        if r.status_code in (200, 201):
            data = r.json()
            return PushResult(
                ok=True,
                crm=self.name,
                external_id=str(data.get("id")),
                raw_response=data,
            )

        if r.status_code == 409:
            return PushResult(
                ok=True,
                crm=self.name,
                external_id=None,
                raw_response={"note": "contact already existed", "status": 409},
            )

        return PushResult(
            ok=False,
            crm=self.name,
            error=f"{r.status_code}: {r.text[:500]}",
            retryable=is_retryable_status(r.status_code),
            raw_response={"status": r.status_code, "body": r.text[:2000]},
        )
