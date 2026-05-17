"""Load and cache per-client YAML configs.

Two invariants this module enforces:

1. **Fail loud at load.** If a YAML file is missing a required ClientConfig
   field, we raise immediately — never silently swallow.
2. **No config bleed.** Loading client A's config never reads client B's file.
   The loader keys on `client_id` and reads exactly one file per call.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import ValidationError

from .logging_setup import get_logger
from .models import ClientConfig
from .settings import get_settings

log = get_logger(__name__)


class ClientConfigError(RuntimeError):
    """Raised when a client config is missing, invalid, or ambiguous."""


def _config_path(client_id: str, base: Path | None = None) -> Path:
    base = base or get_settings().client_config_dir
    return base / f"{client_id}.yaml"


@lru_cache(maxsize=256)
def load_client_config(client_id: str) -> ClientConfig:
    """Load exactly one client's YAML. Cached for the life of the process."""
    if not client_id or "/" in client_id or ".." in client_id:
        raise ClientConfigError(f"Refusing suspicious client_id: {client_id!r}")

    path = _config_path(client_id)
    if not path.exists():
        raise ClientConfigError(f"Client config not found for {client_id!r} at {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # Guard against config bleed: the file must claim its own client_id.
    if raw.get("client_id") != client_id:
        raise ClientConfigError(
            f"Config file at {path} declares client_id={raw.get('client_id')!r} "
            f"but was loaded as {client_id!r}"
        )

    try:
        return ClientConfig(**raw)
    except ValidationError as e:
        # Fail LOUD at boot — never silently mid-flight.
        raise ClientConfigError(
            f"Invalid config for {client_id!r} at {path}:\n{e}"
        ) from e


def resolve_client_by_domain(domain: str) -> ClientConfig:
    """Find a client config whose `domain` matches. Scans the config dir once."""
    settings = get_settings()
    base = settings.client_config_dir
    if not base.exists():
        raise ClientConfigError(f"Client config dir does not exist: {base}")

    norm = domain.lower().lstrip(".")
    for path in sorted(base.glob("*.yaml")):
        if path.stem.startswith("_"):
            continue  # skip _example.yaml
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            log.warning("config_parse_error", path=str(path), error=str(e))
            continue
        cfg_domain = (raw.get("domain") or "").lower()
        if cfg_domain and (norm == cfg_domain or norm.endswith("." + cfg_domain)):
            return load_client_config(raw["client_id"])

    raise ClientConfigError(f"No client config matches domain {domain!r}")


def clear_config_cache() -> None:
    """For tests — drop the LRU cache so a fresh config file is re-read."""
    load_client_config.cache_clear()
