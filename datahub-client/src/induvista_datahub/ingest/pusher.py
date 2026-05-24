"""HTTP pusher — POSTs sample batches to INDUVISTA's /api/ingest.

Phase OPC.2: stub. The real implementation lands in OPC.4:

  - httpx.AsyncClient with bearer auth
  - tenacity-decorated retry: exponential backoff, max ~5 minutes
  - Status-byte-aware: if /api/ingest returns rejected > 0 with
    "tag_id N does not exist" or "not permitted for this key", the
    affected rows go to dead-letter (retrying won't fix it)
  - Network errors keep retrying forever (client should never give
    up entirely — what else would it do?)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PushSample:
    """One sample shaped exactly as /api/ingest's IngestSample expects."""
    tag_id: int
    time: datetime
    value_double: float | None = None
    value_text: str | None = None
    st: int = 192
    st_reason: str | None = None


@dataclass(frozen=True)
class PushResult:
    accepted: int
    rejected: int
    warned: int
    errors: list[dict]            # per-sample errors from the server


class Pusher:
    """HTTP client for /api/ingest. Currently a stub."""

    def __init__(self, *, base_url: str, api_key: str, timeout_sec: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_sec = timeout_sec

    async def push(self, samples: list[PushSample]) -> PushResult:
        """POST a batch to /api/ingest. Phase OPC.2: stub raises."""
        raise NotImplementedError("HTTP pusher implemented in OPC.4")

    async def health(self) -> bool:
        """Quick reachability check — GET /health. Returns True on 200."""
        raise NotImplementedError("Health check implemented in OPC.4")
