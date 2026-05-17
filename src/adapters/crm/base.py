"""CRM adapter interface + registry.

Every CRM (HubSpot, Pipedrive, Mailchimp, ...) implements the same contract:

    class MyCRM(CRMAdapter):
        name = "my_crm"
        async def push(self, lead, *, credentials, config) -> PushResult

`credentials` is whatever the secret-store returns for the client's
`crm_credentials_secret_id` — usually `{"api_key": "..."}` or similar.

A push that returns `ok=False, retryable=True` will be re-queued with
exponential backoff. `ok=False, retryable=False` is dead-letter immediately.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ...models import ClientConfig, Lead, PushResult


class UnknownCRMError(KeyError):
    """Raised when a client config names a CRM with no registered adapter."""


class CRMAdapter(ABC):
    name: str = ""

    @abstractmethod
    async def push(
        self,
        lead: Lead,
        *,
        credentials: dict[str, Any],
        config: ClientConfig,
    ) -> PushResult: ...


_REGISTRY: dict[str, CRMAdapter] = {}


def register_crm_adapter(cls: type[CRMAdapter]) -> type[CRMAdapter]:
    if not cls.name:
        raise ValueError(f"{cls.__name__}.name must be set")
    _REGISTRY[cls.name] = cls()
    return cls


def get_crm_adapter(name: str) -> CRMAdapter:
    try:
        return _REGISTRY[name]
    except KeyError as e:
        raise UnknownCRMError(
            f"No CRM adapter registered for {name!r}. Known: {sorted(_REGISTRY)}"
        ) from e


def registered_crms() -> list[str]:
    return sorted(_REGISTRY)


# 5xx and network errors are retryable; 4xx (except 429) is not.
def is_retryable_status(status: int) -> bool:
    if status == 429:
        return True
    return 500 <= status < 600
