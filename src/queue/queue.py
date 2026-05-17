"""Redis-backed lead queue + scheduled-retry index.

Two structures:

  list  `queue:leads`              — FIFO of pending Lead JSON envelopes
  zset  `queue:retries`            — score = unix-ts when the lead becomes
                                     eligible to retry; member = envelope
  list  `queue:dead`               — terminal failures, persisted for ops
  hash  `lead:{lead_id}`           — last-known full Lead JSON for dashboard
  set   `client:{client_id}:leads` — lead_ids per client (dashboard index)

The envelope is `{"lead": <json>, "retries": int, "kind": "new"|"retry"|"followup"}`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from functools import lru_cache

import redis

from ..models import Lead
from ..settings import get_settings

QUEUE_KEY = "queue:leads"
RETRY_KEY = "queue:retries"
DEAD_KEY = "queue:dead"


def _envelope(lead: Lead, *, retries: int = 0, kind: str = "new") -> str:
    return json.dumps(
        {"lead": lead.model_dump(mode="json"), "retries": retries, "kind": kind}
    )


def _decode(raw: bytes | str) -> tuple[Lead, int, str]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    obj = json.loads(raw)
    lead = Lead.model_validate(obj["lead"])
    return lead, int(obj.get("retries", 0)), obj.get("kind", "new")


@lru_cache(maxsize=1)
def get_redis() -> redis.Redis:
    """Real Redis. Tests should call `set_redis(fakeredis.FakeRedis())`."""
    return redis.from_url(get_settings().redis_url)


_override: redis.Redis | None = None


def set_redis(client: redis.Redis | None) -> None:
    """For tests: inject a fakeredis or None to fall back to env-configured."""
    global _override
    _override = client


def _r() -> redis.Redis:
    return _override or get_redis()


class LeadQueue:
    """Thin wrapper for queue ops. Keeps the redis key strings in one place."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        self.r = client or _r()

    # --- producer side -----------------------------------------------------

    def enqueue(self, lead: Lead, *, kind: str = "new") -> None:
        self.r.rpush(QUEUE_KEY, _envelope(lead, kind=kind))
        self._index(lead)

    def schedule_retry(self, lead: Lead, *, retries: int, delay_seconds: int) -> None:
        score = time.time() + max(0, delay_seconds)
        self.r.zadd(RETRY_KEY, {_envelope(lead, retries=retries, kind="retry"): score})
        self._index(lead)

    def dead_letter(self, lead: Lead, *, reason: str) -> None:
        obj = {
            "lead": lead.model_dump(mode="json"),
            "reason": reason,
            "ts": time.time(),
        }
        self.r.rpush(DEAD_KEY, json.dumps(obj))
        self._index(lead)

    # --- consumer side -----------------------------------------------------

    def pop(self, *, timeout: int = 5) -> tuple[Lead, int, str] | None:
        """Blocking pop from the main queue. Returns None on timeout."""
        result = self.r.blpop([QUEUE_KEY], timeout=timeout)
        if not result:
            return None
        _key, raw = result
        return _decode(raw)

    def drain_due_retries(self, *, now: float | None = None) -> list[tuple[Lead, int, str]]:
        """Return + remove all retry envelopes whose score has passed."""
        cutoff = now if now is not None else time.time()
        # ZRANGEBYSCORE then ZREM is good enough; we don't need a Lua script
        # for the throughput we expect.
        raw_items: list[bytes] = self.r.zrangebyscore(RETRY_KEY, 0, cutoff)
        if not raw_items:
            return []
        # Atomically remove the items we're about to handle.
        pipe = self.r.pipeline()
        for item in raw_items:
            pipe.zrem(RETRY_KEY, item)
        pipe.execute()
        return [_decode(item) for item in raw_items]

    # --- dashboard helpers -------------------------------------------------

    def _index(self, lead: Lead) -> None:
        self.r.hset(f"lead:{lead.lead_id}", mapping={"json": lead.model_dump_json()})
        self.r.sadd(f"client:{lead.client_id}:leads", lead.lead_id)

    def get_lead(self, lead_id: str) -> Lead | None:
        raw = self.r.hget(f"lead:{lead_id}", "json")
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return Lead.model_validate_json(raw)

    def list_leads_for_client(self, client_id: str, *, limit: int = 200) -> list[Lead]:
        ids: Iterable[bytes] = self.r.smembers(f"client:{client_id}:leads") or set()
        leads = [self.get_lead(i.decode() if isinstance(i, bytes) else i) for i in ids]
        leads = [lead for lead in leads if lead is not None]
        leads.sort(key=lambda lead: lead.received_at, reverse=True)
        return leads[:limit]

    def queue_lengths(self) -> dict[str, int]:
        return {
            "pending": int(self.r.llen(QUEUE_KEY)),
            "scheduled_retries": int(self.r.zcard(RETRY_KEY)),
            "dead_letter": int(self.r.llen(DEAD_KEY)),
        }


def get_queue() -> LeadQueue:
    return LeadQueue()
