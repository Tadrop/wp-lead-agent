"""Append-only audit trail.

Two backends ship in-box:

* **JsonlAuditTrail** — a `data/audit/{client_id}.jsonl` file (default for
  local dev). Append-only, one JSON object per line.
* **RedisAuditTrail** — RPUSHes onto `audit:{client_id}` (for prod-shaped
  deployments where everything else already uses Redis).

The contract is the same: `write(entry)` MUST be called BEFORE the side
effect it describes. If the audit write fails, we abort the pipeline step
rather than commit an un-audited side effect.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Protocol

import redis

from ..models import AuditEntry, AuditStatus


def hash_payload(payload: Any) -> str:
    """Stable SHA-256 of any JSON-serializable payload (sorted keys)."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class AuditTrail(Protocol):
    def write(self, entry: AuditEntry) -> None: ...
    def read(self, client_id: str, *, limit: int = 200) -> list[AuditEntry]: ...
    def read_for_lead(self, client_id: str, lead_id: str) -> list[AuditEntry]: ...


# ---------------------------------------------------------------------------
# JSONL backend
# ---------------------------------------------------------------------------


class JsonlAuditTrail:
    """File-backed audit trail: `data/audit/{client_id}.jsonl`."""

    def __init__(self, base_dir: Path | str = "data/audit") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, client_id: str) -> Path:
        return self.base_dir / f"{client_id}.jsonl"

    def write(self, entry: AuditEntry) -> None:
        line = entry.model_dump_json()
        with self._lock, self._path(entry.client_id).open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read(self, client_id: str, *, limit: int = 200) -> list[AuditEntry]:
        path = self._path(client_id)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        # Newest last in the file; return newest-first for dashboard.
        tail = lines[-limit:]
        return [AuditEntry.model_validate_json(line) for line in reversed(tail)]

    def read_for_lead(self, client_id: str, lead_id: str) -> list[AuditEntry]:
        return [e for e in self.read(client_id, limit=10_000) if e.lead_id == lead_id]


# ---------------------------------------------------------------------------
# Redis backend
# ---------------------------------------------------------------------------


class RedisAuditTrail:
    def __init__(self, client: redis.Redis) -> None:
        self.r = client

    @staticmethod
    def _key(client_id: str) -> str:
        return f"audit:{client_id}"

    def write(self, entry: AuditEntry) -> None:
        self.r.rpush(self._key(entry.client_id), entry.model_dump_json())

    def read(self, client_id: str, *, limit: int = 200) -> list[AuditEntry]:
        raw = self.r.lrange(self._key(client_id), -limit, -1)
        return [
            AuditEntry.model_validate_json(b.decode() if isinstance(b, bytes) else b)
            for b in reversed(raw)
        ]

    def read_for_lead(self, client_id: str, lead_id: str) -> list[AuditEntry]:
        return [e for e in self.read(client_id, limit=10_000) if e.lead_id == lead_id]


# ---------------------------------------------------------------------------
# Singleton resolver
# ---------------------------------------------------------------------------


_singleton: AuditTrail | None = None


def get_audit_trail() -> AuditTrail:
    """Default to JSONL in dev. Production code can call `set_audit_trail` instead."""
    global _singleton
    if _singleton is None:
        _singleton = JsonlAuditTrail()
    return _singleton


def set_audit_trail(trail: AuditTrail) -> None:
    global _singleton
    _singleton = trail


# Convenience: write a step around any pipeline operation.
def audit_step(
    *,
    lead_id: str,
    client_id: str,
    step: str,
    status: AuditStatus,
    payload: Any = None,
    error: str | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditEntry:
    entry = AuditEntry(
        lead_id=lead_id,
        client_id=client_id,
        step=step,
        status=status,
        payload_hash=hash_payload(payload) if payload is not None else None,
        error=error,
        detail=detail or {},
    )
    get_audit_trail().write(entry)
    return entry
