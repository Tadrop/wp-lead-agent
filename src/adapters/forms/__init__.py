"""Form plugin adapters.

Import this package to register all built-in adapters. New plugins drop a
file in here, subclass `FormAdapter`, and `@register("name")` themselves.
"""

from . import fluent, gravity, wpforms  # noqa: F401 — registers adapters
from .base import (
    FormAdapter,
    UnknownPluginError,
    get_form_adapter,
    register_form_adapter,
    registered_form_plugins,
)

__all__ = [
    "FormAdapter",
    "UnknownPluginError",
    "get_form_adapter",
    "register_form_adapter",
    "registered_form_plugins",
]
