"""Gravity Forms adapter.

Gravity webhooks post fields keyed by their numeric `id` (e.g. "1", "2.3").
The Gravity admin lets you assign a `adminLabel` per field; we accept either
the numeric id (mapped via a payload-level `fieldMap`) OR a labelled payload
where the integration has already substituted human keys. Both shapes are
common in the wild.

Example shape:
    {
      "form_id": 7,
      "entry": {
        "1.3": "Jane",          # First name
        "1.6": "Doe",           # Last name
        "2":   "jane@acme.com",
        "3":   "+1 555 0100",
        "4":   "Hello"
      },
      "fieldMap": {
        "1.3": "first_name",
        "1.6": "last_name",
        "2":   "email",
        "3":   "phone",
        "4":   "message"
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


def _apply_field_map(entry: dict[str, Any], field_map: dict[str, str] | None) -> dict[str, Any]:
    if not field_map:
        return {str(k).lower(): v for k, v in entry.items()}
    out: dict[str, Any] = {}
    for raw_key, val in entry.items():
        mapped = field_map.get(str(raw_key), str(raw_key))
        out[mapped.lower()] = val
    return out


@register_form_adapter
class GravityFormsAdapter(FormAdapter):
    name = "gravity"

    def normalize(self, raw: dict[str, Any], *, client_id: str) -> Lead:
        entry = raw.get("entry") or raw.get("fields") or raw
        if not isinstance(entry, dict):
            raise ValueError("Gravity payload has no entry/fields object")
        fields = _apply_field_map(entry, raw.get("fieldMap") or raw.get("field_map"))

        first = first_present(fields, FIRST_NAME_KEYS)
        last = first_present(fields, LAST_NAME_KEYS)
        if not (first or last):
            first, last = split_name(first_present(fields, NAME_KEYS))

        email = first_present(fields, EMAIL_KEYS)
        if not email:
            raise ValueError("Gravity payload has no email field")

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
            },
        )
