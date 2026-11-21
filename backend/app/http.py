"""HTTP access to the upstream services, with retries and a SQLite response cache.

The USGS services are slow and occasionally return 502/504 from their gateways,
so every call retries with backoff and successful responses are cached on disk.
Watershed boundaries and the NHDPlus network change rarely, so the default TTL is long.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import httpx

CACHE_PATH = Path(__file__).resolve().parent.parent / "cache" / "http.sqlite"
DEFAULT_TTL = 60 * 60 * 24 * 30
USER_AGENT = "watershed-explorer/0.1 (personal project; adam@aeholbrook.com)"
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_AGE = 60 * 60 * 24 * 365  # longest TTL any caller asks for; older rows are dead weight

# Services whose usage policy caps the request rate (seconds between requests).
# Nominatim: at most 1/s, https://operations.osmfoundation.org/policies/nominatim/
PACE = {"nominatim.openstreetmap.org": 1.1}
PACE_MAX_QUEUE = 8  # beyond this a paced call gives up rather than wait (the caller has fallbacks)


class UpstreamError(Exception):
    def __init__(self, service: str, detail: str):
        super().__init__(f"{service}: {detail}")
        self.service = service
        self.detail = detail

    def __reduce__(self):
        # Lidar traces run in worker processes; keep the error intact across the pickle.
        return (UpstreamError, (self.service, self.detail))


class _Cache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS responses (k TEXT PRIMARY KEY, t REAL, body BLOB)"
        )
        self._db.commit()

    def get(self, key: str, ttl: float) -> bytes | None:
        with self._lock:
            row = self._db.execute("SELECT t, body FROM responses WHERE k = ?", (key,)).fetchone()
        if row and time.time() - row[0] < ttl:
            return row[1]
        return None

    def put(self, key: str, body: bytes) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO responses (k, t, body) VALUES (?, ?, ?)",
                (key, time.time(), body),
            )
            self._db.commit()

    def prune(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM responses WHERE t < ?", (time.time() - MAX_AGE,))
            self._db.commit()


cache = _Cache(CACHE_PATH)

_pace_locks: dict[str, asyncio.Lock] = {}
_pace_last: dict[str, float] = {}
_pace_waiting: dict[str, int] = {}


async def _pace(host: str) -> None:
    gap = PACE.get(host)
    if gap is None:
        return
    if _pace_waiting.get(host, 0) >= PACE_MAX_QUEUE:
        raise UpstreamError(host, "too many queued requests")
    lock = _pace_locks.setdefault(host, asyncio.Lock())
    _pace_waiting[host] = _pace_waiting.get(host, 0) + 1
    try:
        async with lock:
            wait = _pace_last.get(host, 0) + gap - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            _pace_last[host] = time.monotonic()
    finally:
        _pace_waiting[host] -= 1


def _key(method: str, url: str, params: Any, body: Any) -> str:
    raw = json.dumps([method, url, params, body], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _service(url: str) -> str:
    return httpx.URL(url).host


_async_client: httpx.AsyncClient | None = None


def async_client() -> httpx.AsyncClient:
    global _async_client
    if _async_client is None:
        _async_client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(120.0, connect=15.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=24),
        )
    return _async_client


async def fetch_json(
    url: str,
    *,
    params: dict | None = None,
    json_body: Any = None,
    form: dict | None = None,
    ttl: float = DEFAULT_TTL,
    retries: int = 3,
) -> Any:
    """GET (or POST when a body is given) and decode JSON, cached on success."""
    method = "POST" if json_body is not None or form is not None else "GET"
    key = _key(method, url, params, json_body if json_body is not None else form)
    hit = cache.get(key, ttl)
    if hit is not None:
        return json.loads(hit)

    last = "no response"
    for attempt in range(retries + 1):
        if attempt:
            await asyncio.sleep(1.5 * 2 ** (attempt - 1))
        await _pace(_service(url))
        try:
            r = await async_client().request(
                method, url, params=params, json=json_body, data=form
            )
        except httpx.TransportError as e:
            last = f"{type(e).__name__}: {e}"
            continue
        if r.status_code in RETRY_STATUS:
            last = f"HTTP {r.status_code}"
            continue
        if r.status_code == 400 and "Error executing process" in r.text:
            # NLDI's pygeoapi processes report their own internal timeouts as 400s; those pass on retry.
            last = f"HTTP 400: {r.text[:200]}"
            continue
        if r.status_code >= 400:
            raise UpstreamError(_service(url), f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
        except ValueError:
            # Gateways sometimes answer 200 with an HTML error page.
            last = "non-JSON response"
            continue
        if isinstance(data, dict) and "error" in data and isinstance(data["error"], dict):
            # ArcGIS reports failures inside a 200 response.
            last = f"ArcGIS error: {data['error'].get('message')}"
            continue
        cache.put(key, r.content)
        return data
    raise UpstreamError(_service(url), last)


async def fetch_text(url: str, *, params: dict | None = None, must_contain: str = "",
                     ttl: float = DEFAULT_TTL, retries: int = 2) -> str:
    """GET a non-JSON body, cached on success (success = 200 and contains must_contain)."""
    key = _key("GET", url, params, None)
    hit = cache.get(key, ttl)
    if hit is not None:
        return hit.decode()
    last = "no response"
    for attempt in range(retries + 1):
        if attempt:
            await asyncio.sleep(1.5 * 2 ** (attempt - 1))
        try:
            r = await async_client().get(url, params=params)
        except httpx.TransportError as e:
            last = f"{type(e).__name__}: {e}"
            continue
        if r.status_code == 200 and must_contain in r.text:
            cache.put(key, r.content)
            return r.text
        last = f"HTTP {r.status_code}"
        if r.status_code not in RETRY_STATUS:
            break
    raise UpstreamError(_service(url), last)


_sync_client: httpx.Client | None = None


def sync_client() -> httpx.Client:
    global _sync_client
    if _sync_client is None:
        _sync_client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(120.0, connect=15.0),
            follow_redirects=True,
        )
    return _sync_client


def fetch_json_sync(url: str, *, params: dict | None = None, ttl: float = DEFAULT_TTL,
                    retries: int = 3) -> Any:
    key = _key("GET", url, params, None)
    hit = cache.get(key, ttl)
    if hit is not None:
        return json.loads(hit)
    last = "no response"
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(1.5 * 2 ** (attempt - 1))
        try:
            r = sync_client().get(url, params=params)
        except httpx.TransportError as e:
            last = f"{type(e).__name__}: {e}"
            continue
        if r.status_code in RETRY_STATUS:
            last = f"HTTP {r.status_code}"
            continue
        try:
            data = r.json()
        except ValueError:
            last = "non-JSON response"
            continue
        if r.status_code >= 400 or (isinstance(data, dict) and "error" in data):
            last = f"HTTP {r.status_code}: {str(data)[:200]}"
            continue
        cache.put(key, r.content)
        return data
    raise UpstreamError(_service(url), last)


def fetch_bytes_sync(url: str, *, expect: bytes, retries: int = 3) -> bytes:
    """Download a binary file, retrying until it starts with the expected magic bytes."""
    last = "no response"
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(1.5 * 2 ** (attempt - 1))
        try:
            r = sync_client().get(url)
        except httpx.TransportError as e:
            last = f"{type(e).__name__}: {e}"
            continue
        if r.status_code == 200 and r.content.startswith(expect):
            return r.content
        last = f"HTTP {r.status_code}, {len(r.content)} bytes"
    raise UpstreamError(_service(url), last)
