"""Minimal Honcho v3 REST client for the backfill loader.

Configuration is entirely via environment so this works against any Honcho
(self-hosted, local, or hosted) without code changes:

    HONCHO_BASE_URL   e.g. http://localhost:8000 (no trailing slash)
    HONCHO_API_KEY    optional; sent as Authorization: Bearer when set

Verified against Honcho API 3.2.2:
- POST /v3/workspaces/{id}/sessions takes an explicit session id
- POST /v3/workspaces/{id}/sessions/{sid}/messages takes up to 100 messages,
  each with an optional created_at for true historical backdating
- POST /v3/workspaces/{id}/sessions/{sid}/peers takes {peer_id: {config}}
- Message content cap: 25,000 chars
"""
from __future__ import annotations

import os
import time

import requests

BASE_URL = os.environ.get("HONCHO_BASE_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("HONCHO_API_KEY", "")

MAX_BATCH = 100
MAX_CONTENT_CHARS = 25_000
TRUNCATE_AT = 24_000


class Honcho:
    def __init__(self, workspace: str, timeout: int = 60, retries: int = 3):
        self.base = BASE_URL
        self.ws = workspace
        self.timeout = timeout
        self.retries = retries
        self.s = requests.Session()
        self.s.trust_env = False
        if API_KEY:
            self.s.headers["Authorization"] = f"Bearer {API_KEY}"
        http_proxy = os.environ.get("HONCHO_HTTP_PROXY", "")
        https_proxy = os.environ.get("HONCHO_HTTPS_PROXY", "")
        if http_proxy or https_proxy:
            self.s.proxies.update(
                {k: v for k, v in
                 {"http": http_proxy, "https": https_proxy}.items() if v}
            )

    def _req(self, method: str, path: str, **kw) -> requests.Response:
        url = f"{self.base}/v3/workspaces/{self.ws}{path}"
        last = None
        for attempt in range(self.retries):
            try:
                r = self.s.request(method, url, timeout=self.timeout, **kw)
                if r.status_code == 429 or r.status_code >= 500:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    time.sleep(2 ** attempt)
                    continue
                return r
            except requests.RequestException as e:
                last = str(e)
                time.sleep(2 ** attempt)
        raise RuntimeError(f"{method} {path} failed after {self.retries} tries: {last}")

    # --- peers ---
    def ensure_peer(self, peer_id: str, metadata: dict | None = None) -> dict:
        r = self._req("POST", "/peers",
                      json={"id": peer_id, "metadata": metadata or {}})
        if r.status_code == 409:  # already exists
            return {"id": peer_id, "exists": True}
        r.raise_for_status()
        return r.json()

    # --- sessions ---
    def ensure_session(self, session_id: str, metadata: dict | None = None,
                       scopes: list[str] | None = None) -> dict:
        r = self._req("POST", "/sessions",
                      json={"id": session_id, "metadata": metadata or {},
                            "scopes": scopes or []})
        if r.status_code == 409:  # already exists
            return {"id": session_id, "exists": True}
        r.raise_for_status()
        return r.json()

    def add_peers(self, session_id: str, peer_ids: list[str]) -> dict:
        r = self._req("POST", f"/sessions/{session_id}/peers",
                      json={pid: {} for pid in peer_ids})
        r.raise_for_status()
        return r.json()

    def list_sessions(self, size: int = 100) -> list[dict]:
        out, page = [], 1
        while True:
            r = self._req("POST", f"/sessions/list?page={page}&size={size}", json={})
            r.raise_for_status()
            d = r.json()
            items = d.get("items", [])
            out.extend(items)
            if len(items) < size or page >= d.get("pages", page):
                break
            page += 1
        return out

    # --- messages ---
    def add_messages(self, session_id: str, messages: list[dict]) -> dict:
        """messages: [{peer_id, content, created_at, metadata}]. At most 100."""
        if len(messages) > MAX_BATCH:
            raise ValueError(f"batch of {len(messages)} exceeds {MAX_BATCH}")
        r = self._req("POST", f"/sessions/{session_id}/messages",
                      json={"messages": messages})
        r.raise_for_status()
        return r.json()

    def count_messages(self, session_id: str) -> int:
        total, page = 0, 1
        while True:
            r = self._req("POST",
                          f"/sessions/{session_id}/messages/list?page={page}&size=100",
                          json={})
            r.raise_for_status()
            d = r.json()
            items = d.get("items", [])
            total += len(items)
            if len(items) < 100 or page >= d.get("pages", page):
                break
            page += 1
        return total


def truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_CONTENT_CHARS:
        return text, False
    return text[:TRUNCATE_AT] + "\n\n[…truncated by loader…]", True
