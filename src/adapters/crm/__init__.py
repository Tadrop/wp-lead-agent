"""CRM adapters. Importing this package registers all built-in CRMs."""

from . import hubspot, mailchimp, pipedrive  # noqa: F401 — registers adapters
from .base import CRMAdapter, UnknownCRMError, get_crm_adapter, register_crm_adapter

__all__ = [
    "CRMAdapter",
    "UnknownCRMError",
    "get_crm_adapter",
    "register_crm_adapter",
]
