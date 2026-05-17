"""WPForms adapter.

WPForms webhooks (the "Webhooks" addon) post a JSON envelope that typically
looks like:

    {
      "form_id": 42,
      "entry_id": 123,
      "fields": {
        "name":   {"value": "Jane Doe"},
        "email":  {"value": "jane@acme.com"},
        "phone":  {"value": "+1..."},
        "message":{"value": "Hello"}
      }
    }

Some installs flatten `fields` to `{key: value}` strings. We accept both.
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


def _flatten_fields(fields: Any) -> dict[str, Any]:
    """Normalize the two common WPForms field shapes."""
    if not isinstance(fields, dict):
        return {}
    flat: dict[str, Any] = {}
    for key, val in fields.items():
        k = str(key).lower().strip()
        if isinstance(val, dict) and "value" in val:
            flat[k] = val.get("value")
        else:
            flat[k] = val
    return flat


@register_form_adapter
class WPFormsAdapter(FormAdapter):
    name = "wpforms"

    def normalize(self, raw: dict[str, Any], *, client_id: str) -> Lead:
        fields = _flatten_fields(raw.get("fields", raw))

        first = first_present(fields, FIRST_NAME_KEYS)
        last = first_present(fields, LAST_NAME_KEYS)
        if not (first or last):
            first, last = split_name(first_present(fields, NAME_KEYS))

        email = first_present(fields, EMAIL_KEYS)
        if not email:
            raise ValueError("WPForms payload has no email field")

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
