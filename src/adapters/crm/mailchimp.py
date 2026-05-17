"""Mailchimp adapter (add subscriber to an audience).

Credentials shape: `{"api_key": "abc-us21", "audience_id": "..."}`.
The dc (`us21`) is parsed from the api_key suffix.
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx

from ...models import ClientConfig, Lead, PushResult
from .base import CRMAdapter, is_retryable_status, register_crm_adapter


def _dc_from_key(key: str) -> str:
    if "-" not in key:
        raise ValueError("Mailchimp api_key is missing the '-dc' suffix")
    return key.split("-", 1)[1]


def _subscriber_hash(email: str) -> str:
    return hashlib.md5(email.lower().strip().encode("utf-8")).hexdigest()


@register_crm_adapter
class MailchimpAdapter(CRMAdapter):
    name = "mailchimp"

    async def push(
        self,
        lead: Lead,
        *,
        credentials: dict[str, Any],
        config: ClientConfig,
    ) -> PushResult:
        api_key = credentials.get("api_key")
        audience_id = credentials.get("audience_id")
        if not api_key or not audience_id:
            return PushResult(
                ok=False,
                crm=self.name,
                error="Mailchimp credentials missing api_key or audience_id",
                retryable=False,
            )

        try:
            dc = _dc_from_key(api_key)
        except ValueError as e:
            return PushResult(ok=False, crm=self.name, error=str(e), retryable=False)

        # PUT /lists/{list_id}/members/{subscriber_hash} → upsert
        url = (
            f"https://{dc}.api.mailchimp.com/3.0/lists/{audience_id}"
            f"/members/{_subscriber_hash(str(lead.email))}"
        )
        merge: dict[str, Any] = {}
        if lead.first_name:
            merge["FNAME"] = lead.first_name
        if lead.last_name:
            merge["LNAME"] = lead.last_name
        if lead.phone:
            merge["PHONE"] = lead.phone
        if lead.company:
            merge["COMPANY"] = lead.company
        body: dict[str, Any] = {
            "email_address": str(lead.email),
            "status_if_new": "subscribed",
        }
        if merge:
            body["merge_fields"] = merge

        try:
            async with httpx.AsyncClient(timeout=15.0, auth=("anystring", api_key)) as client:
                r = await client.put(url, json=body)
        except httpx.RequestError as e:
            return PushResult(ok=False, crm=self.name, error=f"network: {e}", retryable=True)

        if r.status_code in (200, 201):
            data = r.json()
            return PushResult(ok=True, crm=self.name, external_id=data.get("id"), raw_response=data)

        return PushResult(
            ok=False,
            crm=self.name,
            error=f"{r.status_code}: {r.text[:500]}",
            retryable=is_retryable_status(r.status_code),
            raw_response={"status": r.status_code, "body": r.text[:2000]},
        )
