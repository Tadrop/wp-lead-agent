"""Form adapter interface + registry.

Every form plugin (WPForms, Gravity, Fluent, ...) implements the same shape:

    class MyAdapter(FormAdapter):
        name = "my_plugin"
        def normalize(self, raw, *, client_id): -> Lead

Adding a new plugin means: implement, `@register_form_adapter`, done.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ...models import Lead


class UnknownPluginError(KeyError):
    """Raised when a webhook arrives for a plugin we don't have an adapter for."""


class FormAdapter(ABC):
    """Abstract base for every form plugin adapter."""

    name: str = ""

    @abstractmethod
    def normalize(self, raw: dict[str, Any], *, client_id: str) -> Lead:
        """Convert a plugin-specific webhook payload into a `Lead`."""


_REGISTRY: dict[str, FormAdapter] = {}


def register_form_adapter(cls: type[FormAdapter]) -> type[FormAdapter]:
    """Class decorator — register an adapter by its `name`."""
    if not cls.name:
        raise ValueError(f"{cls.__name__}.name must be set")
    _REGISTRY[cls.name] = cls()
    return cls


def get_form_adapter(name: str) -> FormAdapter:
    try:
        return _REGISTRY[name]
    except KeyError as e:
        raise UnknownPluginError(
            f"No form adapter registered for plugin {name!r}. "
            f"Known: {sorted(_REGISTRY)}"
        ) from e


def registered_form_plugins() -> list[str]:
    return sorted(_REGISTRY)


# ---------------------------------------------------------------------------
# Helpers shared by adapters
# ---------------------------------------------------------------------------


def split_name(full: str | None) -> tuple[str | None, str | None]:
    if not full:
        return None, None
    parts = full.strip().split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]


def first_present(d: dict[str, Any], keys: list[str]) -> Any:
    """Return the first value in `d` for any of `keys` that is non-empty."""
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


# Field key aliases we accept across plugins. Lowercased before lookup.
NAME_KEYS = ["name", "your_name", "full_name", "fullname", "contact_name"]
FIRST_NAME_KEYS = ["first_name", "firstname", "fname", "given_name"]
LAST_NAME_KEYS = ["last_name", "lastname", "lname", "family_name", "surname"]
EMAIL_KEYS = ["email", "your_email", "email_address", "contact_email"]
PHONE_KEYS = ["phone", "your_phone", "phone_number", "tel", "mobile"]
COMPANY_KEYS = ["company", "company_name", "organization", "org", "business"]
MESSAGE_KEYS = ["message", "your_message", "comments", "comment", "details", "inquiry"]
SUBJECT_KEYS = ["subject", "topic", "your_subject"]
