"""Long lookups, answered in pieces short enough for the proxies in front of the app.

Cloudflare ends a proxied request after 100 s, and some lookups (NHDPlus HR, upstream
basins, lidar traces) take longer. Each lookup runs as a task keyed by its endpoint and
arguments. A request waits up to WAIT seconds for it; if it isn't done the answer is
202 and the browser asks again with the same URL, which picks up the same task. The
same key also means two visitors asking for the same place share one lookup.

Every new task counts against the caller's budget (per IP, sliding window), and the
CPU-heavy ones (lidar, ghost streams) have a small global queue as well.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi import HTTPException
from fastapi.responses import JSONResponse

WAIT = float(os.environ.get("WATERSHEDS_WAIT_SECONDS", "20"))
KEEP = 180.0          # how long a finished result waits for the browser to collect it
KEEP_FAILED = 10.0    # failures are dropped quickly so a retry really retries
MAX_TASKS = 60        # everything in flight, across all visitors
MAX_HEAVY = 4         # lidar / ghost-stream tasks queued or running (the pool has 2 workers)

# (requests, window in seconds) per IP, counted only when a request starts new work.
BUDGETS = {
    "geocode": (20, 60),
    "lookup": (240, 600),   # a search fires ~12 lookups, so ~20 places per 10 minutes
    "heavy": (12, 600),
}

client_ip: contextvars.ContextVar[str] = contextvars.ContextVar("client_ip", default="?")


@dataclass
class _Entry:
    task: asyncio.Task
    heavy: bool
    done_at: float | None = None


@dataclass
class _Window:
    hits: deque = field(default_factory=deque)


_tasks: dict[tuple, _Entry] = {}
_windows: dict[tuple[str, str], _Window] = defaultdict(_Window)


def _prune(now: float) -> None:
    for key, e in list(_tasks.items()):
        if e.task.done():
            if e.done_at is None:
                e.done_at = now
            failed = e.task.cancelled() or e.task.exception() is not None
            if now - e.done_at > (KEEP_FAILED if failed else KEEP):
                del _tasks[key]


def _charge(cost: str, now: float) -> None:
    limit, window = BUDGETS[cost]
    w = _windows[(client_ip.get(), cost)]
    while w.hits and now - w.hits[0] > window:
        w.hits.popleft()
    if len(w.hits) >= limit:
        retry = int(window - (now - w.hits[0])) + 1
        raise HTTPException(429, "Too many lookups from your address; try again in a few minutes.",
                            headers={"Retry-After": str(retry)})
    w.hits.append(now)
    if len(_windows) > 20000:  # forget idle visitors
        for k in [k for k, v in _windows.items() if not v.hits or now - v.hits[-1] > 900]:
            del _windows[k]


def long_running(cost: str = "lookup") -> Callable:
    heavy = cost == "heavy"

    def wrap(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def endpoint(**kwargs):
            now = time.monotonic()
            _prune(now)
            key = (fn.__name__, tuple(sorted(kwargs.items())))
            entry = _tasks.get(key)
            if entry is None:
                if len(_tasks) >= MAX_TASKS or (heavy and sum(e.heavy and not e.task.done() for e in _tasks.values()) >= MAX_HEAVY):
                    raise HTTPException(503, "The server is busy; try again in a minute.", headers={"Retry-After": "30"})
                _charge(cost, now)
                entry = _tasks[key] = _Entry(asyncio.create_task(fn(**kwargs)), heavy)
            done, _ = await asyncio.wait({entry.task}, timeout=WAIT)
            if not done:
                return JSONResponse({"pending": True}, status_code=202, headers={"Cache-Control": "no-store"})
            return entry.task.result()  # re-raises the lookup's own HTTPException / UpstreamError

        return endpoint

    return wrap


def stats() -> dict:
    return {"tasks": len(_tasks), "running": sum(not e.task.done() for e in _tasks.values()),
            "heavy_running": sum(e.heavy and not e.task.done() for e in _tasks.values())}
