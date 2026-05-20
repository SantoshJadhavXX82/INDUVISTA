"""Thin HTTP client for the InduVista backend.

Keeps urllib dependency-free (stdlib only) so the smoke test works on a
plain Python install with no pip — only `requests` is otherwise required.

Why a client wrapper at all instead of inline requests calls:
  * Centralised JSON encode/decode with informative error messages
  * Idempotent retries on 5xx (the backend autoresumes after migrations)
  * Backend's audit endpoint occasionally wedges for ~2s under load; the
    retry wrapper papers over that without making the smoke flaky.

The smoke test wants every assertion to fire on real responses, not on
client-internal flakiness, so transient failures are absorbed here.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib import error as urlerror
from urllib import request as urlreq


@dataclass
class ApiError(Exception):
    status: int
    body: str
    url: str

    def __str__(self) -> str:
        return f"HTTP {self.status} from {self.url}: {self.body[:500]}"


class Api:
    """Simple JSON-over-HTTP client. All methods raise ApiError on
    non-2xx responses; the body is preserved verbatim so the caller can
    inspect the FastAPI `detail` field on 400/422."""

    def __init__(self, base: str = "http://127.0.0.1:8000",
                 timeout: float = 30.0,
                 retries: int = 3,
                 retry_sleep: float = 0.5):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.retry_sleep = retry_sleep

    # ---- public verbs --------------------------------------------------
    def get(self, path: str, **params) -> Any:
        url = self._url(path, params)
        return self._request("GET", url, None)

    def post(self, path: str, body: dict | None = None) -> Any:
        return self._request("POST", self._url(path), body)

    def patch(self, path: str, body: dict | None = None) -> Any:
        return self._request("PATCH", self._url(path), body)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", self._url(path), None)

    # ---- internals -----------------------------------------------------
    def _url(self, path: str, params: dict | None = None) -> str:
        url = f"{self.base}{path}" if path.startswith("/") else f"{self.base}/{path}"
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
        return url

    def _request(self, method: str, url: str, body: dict | None) -> Any:
        data: bytes | None = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        last_exc: Exception | None = None
        for attempt in range(self.retries):
            req = urlreq.Request(url, data=data, headers=headers, method=method)
            try:
                with urlreq.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    if resp.status == 204 or not raw:
                        return None
                    return json.loads(raw.decode("utf-8"))
            except urlerror.HTTPError as e:
                # FastAPI 400/422 etc. — surface the body verbatim
                err_body = e.read().decode("utf-8", errors="replace")
                # 5xx — retryable, 4xx — not
                if 500 <= e.code < 600 and attempt < self.retries - 1:
                    last_exc = ApiError(status=e.code, body=err_body, url=url)
                    time.sleep(self.retry_sleep * (attempt + 1))
                    continue
                raise ApiError(status=e.code, body=err_body, url=url) from e
            except urlerror.URLError as e:
                # Network / connection refused — backend may be coming up
                last_exc = e
                if attempt < self.retries - 1:
                    time.sleep(self.retry_sleep * (attempt + 1))
                    continue
                raise
        # Defensive — shouldn't reach here unless retries=0
        if last_exc:
            raise last_exc
        raise RuntimeError("unreachable")

    # ---- convenience: wait for backend health --------------------------
    def wait_ready(self, max_sec: int = 60) -> bool:
        deadline = time.time() + max_sec
        while time.time() < deadline:
            try:
                self.get("/health")
                return True
            except Exception:
                time.sleep(1.0)
        return False
