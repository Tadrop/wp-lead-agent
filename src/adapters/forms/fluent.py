"""Fluent Forms adapter.

Fluent Forms webhooks (the "Webhook" integration) post a flat key→value
JSON object plus some envelope metadata:

    {
      "form_id": 12,
      "submission_id": 555,
      "data": {
        "names": {"first_name": "Jane", "last_name": "Doe"},
        "email": "jane@acme.com",
        "phone": "+1 555 0100",
        "message": "Hello"
      }
    }
"""

from __future__ import annotations

from typing import Any

from ...models import Lead
from .base import (
    COMPANY_KEYS,
    EMAIL_KEYS,
    FIRST_NAME_KEYS,
    LAST_NAME_KEYS,
    MESSAGE_KEYS,
    NAME_KEYS,
    PHONE_KEYS,
    SUBJECT_KEYS,
    FormAdapter,
    first_present,
    register_form_adapter,
    split_name,
)


def _flatten(data: Any, *, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(data, dict):
        return out
    for key, val in data.items():
        k = (prefix + str(key)).lower()
        if isinstance(val, dict):
            # one level of nesting (Fluent's "names" group, address group, etc.)
            for sub_k, sub_v in val.items():
                out[str(sub_k).lower()] = sub_v
            out[k] = val
        else:
            out[k] = val
    return out


@register_form_adapter
class FluentFormsAdapter(FormAdapter):
    name = "fluent"

    def normalize(self, raw: dict[str, Any], *, client_id: str) -> Lead:
        data = raw.get("data") or raw
        fields = _flatten(data)

        first = first_present(fields, FIRST_NAME_KEYS)
        last = first_present(fields, LAST_NAME_KEYS)
        if not (first or last):
            first, last = split_name(first_present(fields, NAME_KEYS))

        email = first_present(fields, EMAIL_KEYS)
        if not email:
            raise ValueError("Fluent payload has no email field")

        return Lead(
            client_id=client_id,
            source_plugin=self.name,
            source_form_id=str(raw.get("form_id") or raw.get("formId") or "") or None,
            email=email,
            first_name=first,
            last_name=last,
            phone=first_present(fields, PHONE_KEYS),
            company=first_present(fields, COMPANY_KEYS),
            message=first_present(fields, MESSAGE_KEYS),
            subject=first_present(fields, SUBJECT_KEYS),
            extra_fields={
                k: v
                for k, v in fields.items()
                if k
                not in set(
                    NAME_KEYS
                    + FIRST_NAME_KEYS
                    + LAST_NAME_KEYS
                    + EMAIL_KEYS
                    + PHONE_KEYS
                    + COMPANY_KEYS
                    + MESSAGE_KEYS
                    + SUBJECT_KEYS
                )
                and not isinstance(v, dict)
            },
        )
