"""Secret-store shim.

For prod, swap this for AWS Secrets Manager / GCP Secret Manager / Vault.
The local impl looks up an env var named after the secret id:

    crm_credentials_secret_id: "acme_hubspot"
    →  env var ACME_HUBSPOT_JSON='{"access_token":"..."}' (JSON payload)
"""

from __future__ import annotations

import json
import os
from typing import Any


class SecretNotFoundError(KeyError):
    pass


def get_secret(secret_id: str) -> dict[str, Any]:
    env_name = f"{secret_id.upper()}_JSON"
    raw = os.environ.get(env_name)
    if not raw:
        raise SecretNotFoundError(
            f"Secret {secret_id!r} not found (expected env var {env_name})"
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SecretNotFoundError(
            f"Secret {secret_id!r} in {env_name} is not valid JSON: {e}"
        ) from e
    if not isinstance(data, dict):
        raise SecretNotFoundError(f"Secret {secret_id!r} must decode to an object")
    return data
