"""Watershed Explorer API.

Each panel in the UI loads from its own endpoint so the slow sources (NHDPlus HR,
lidar) never hold up the fast ones (WBD, NLDI).
"""

from __future__ import annotations

import asyncio
import math
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware

from . import below as below_ground, flows as flows_mod, ghost, lidar, life as life_mod, place as place_mod
from . import jobs, restore as restore_mod, usgs
from .http import UpstreamError, async_client, cache
from .jobs import long_running

# Published at www.aeholbrook.com/watersheds; the routes below answer with or without it.
BASE_PATH = os.environ.get("WATERSHEDS_BASE_PATH", "/watersheds").rstrip("/")


class Edge:
    """Strip BASE_PATH from incoming paths and note the caller's address for the rate limits.

    Through www.aeholbrook.com (Vercel's rewrite, then the Cloudflare tunnel) the visitor is
    X-Vercel-Forwarded-For and CF-Connecting-IP is Vercel. Straight to the tunnel hostname
    the visitor is CF-Connecting-IP. X-Forwarded-For starts with whatever the client sent,
    so it isn't used."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope["path"]
            if BASE_PATH and (path == BASE_PATH or path.startswith(BASE_PATH + "/")):
                path = path[len(BASE_PATH):] or "/"
                scope = {**scope, "path": path, "raw_path": path.encode()}
            headers = dict(scope["headers"])
            ip = (headers.get(b"x-vercel-forwarded-for", b"").split(b",")[0].strip()
                  or headers.get(b"cf-connecting-ip", b"").strip())
            jobs.client_ip.set(ip.decode() if ip else (scope.get("client") or ("?",))[0])
        await self.app(scope, receive, send)


app = FastAPI(title="Watershed Explorer", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.add_middleware(Edge)


@app.on_event("startup")
async def _startup():
    cache.prune()

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@app.exception_handler(UpstreamError)
async def upstream_error(_, exc: UpstreamError):
    return JSONResponse(status_code=502, content={"detail": f"{exc.service}: {exc.detail}"})


def _lonlat(lon: float, lat: float) -> None:
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise HTTPException(400, "coordinates out of range")


@app.get("/api/geocode")
@long_running("geocode")
async def geocode(q: str = Query(min_length=3, max_length=200)):
    try:
        return await usgs.geocode(q)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/api/hierarchy")
@long_running()
async def hierarchy(lon: float, lat: float):
    _lonlat(lon, lat)
    units = await usgs.hierarchy(lon, lat)
    if not units:
        raise HTTPException(404, "no watershed data here (the WBD covers the US and its territories)")
    return {"units": units}


@app.get("/api/hr-catchment")
@long_running()
async def hr_catchment(lon: float, lat: float):
    _lonlat(lon, lat)
    return {"unit": await usgs.hr_catchment(lon, lat)}


@app.get("/api/raindrop")
@long_running()
async def raindrop(lon: float, lat: float):
    _lonlat(lon, lat)
    return await usgs.raindrop(lon, lat)


@app.get("/api/downstream")
@long_running()
async def downstream(lon: float, lat: float):
    """Raindrop entry point, then the whole route to the outlet."""
    _lonlat(lon, lat)
    drop = await usgs.entry_point(lon, lat)
    entry = drop["entry"]
    route = await usgs.downstream(entry["comid"], entry.get("measure"), drop["entry_flowline"])
    entry = {**entry, "stream": usgs.entry_name(entry, route)}
    return {"entry": entry, **route, "approximate": drop.get("approximate")}


@app.get("/api/below")
@long_running()
async def below(lon: float, lat: float):
    _lonlat(lon, lat)
    return await below_ground.below(lon, lat)


@app.get("/api/place")
@long_running()
async def place(lon: float, lat: float):
    _lonlat(lon, lat)
    return await place_mod.place(lon, lat)


@app.get("/api/life")
@long_running()
async def life(lon: float, lat: float):
    _lonlat(lon, lat)
    ds = None
    try:
        ft = await usgs.entry_point(lon, lat)
        ds = await usgs.downstream(ft["entry"]["comid"], ft["entry"].get("measure"), ft["entry_flowline"])
    except UpstreamError:
        pass  # species lists still work without the route
    return await life_mod.life(lon, lat, ds)


@app.get("/api/flows")
@long_running()
async def flows(lon: float, lat: float):
    _lonlat(lon, lat)
    return await flows_mod.flows(lon, lat)


@app.get("/api/restore")
@long_running()
async def restore(lon: float, lat: float):
    _lonlat(lon, lat)
    return await restore_mod.restore(lon, lat)


@app.get("/api/basin/{comid}")
@long_running()
async def basin(comid: int):
    return await usgs.basin(comid)


# Lidar traces are CPU-bound Python. Run them in separate processes so a long trace
# can't hold the GIL and stall every other request in this event loop.
_pool: ProcessPoolExecutor | None = None


def _lidar_pool() -> ProcessPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn"))
    return _pool


@app.get("/api/lidar-trace")
@long_running("heavy")
async def lidar_trace(lon: float, lat: float):
    global _pool
    _lonlat(lon, lat)
    try:
        return await asyncio.get_running_loop().run_in_executor(_lidar_pool(), lidar.trace, lon, lat)
    except UpstreamError:
        raise
    except BrokenProcessPool as e:
        _pool = None
        raise HTTPException(500, "lidar worker crashed; try again") from e
    except Exception as e:  # surface trace failures to the UI
        raise HTTPException(500, f"lidar trace failed: {e}") from e


@app.get("/api/ghost-streams")
@long_running("heavy")
async def ghost_streams(lon: float, lat: float):
    global _pool
    _lonlat(lon, lat)
    try:
        return await asyncio.get_running_loop().run_in_executor(_lidar_pool(), ghost.ghost_streams, lon, lat)
    except UpstreamError:
        raise
    except BrokenProcessPool as e:
        _pool = None
        raise HTTPException(500, "analysis worker crashed; try again") from e
    except Exception as e:
        raise HTTPException(500, f"ghost-stream analysis failed: {e}") from e


TILE_CACHE = Path(__file__).resolve().parent.parent / "cache" / "tiles"
TILE_LAYERS = {
    "bps": (restore_mod.LANDFIRE, {"layers": restore_mod.BPS_LAYER}),
    "nlcd": (restore_mod.NLCD_WMS, {"layers": restore_mod.NLCD_LAYER, "time": restore_mod.NLCD_TIME}),
}
_tile_slots = asyncio.Semaphore(4)


@app.get("/api/tiles/{layer}/{z}/{x}/{y}.png")
async def overlay_tile(layer: str, z: int, x: int, y: int):
    """WMS overlays as cached XYZ tiles. LANDFIRE's server drops CORS headers and
    connections when it throttles a burst of map tiles, so the browser can't use it directly."""
    if layer not in TILE_LAYERS or not (0 <= z <= 16):
        raise HTTPException(404, "unknown tile")
    path = TILE_CACHE / layer / str(z) / str(x) / f"{y}.png"
    if not path.exists():
        url, extra = TILE_LAYERS[layer]
        size = 2 * math.pi * 6378137 / 2 ** z
        x0 = -math.pi * 6378137 + x * size
        y1 = math.pi * 6378137 - y * size
        params = {"service": "WMS", "version": "1.1.1", "request": "GetMap", "styles": "", "srs": "EPSG:3857",
                  "bbox": f"{x0},{y1 - size},{x0 + size},{y1}", "width": 256, "height": 256,
                  "format": "image/png", "transparent": "true", **extra}
        content = None
        async with _tile_slots:
            for attempt in range(3):
                if attempt:
                    await asyncio.sleep(0.8 * attempt)
                try:
                    r = await async_client().get(url, params=params)
                except httpx.TransportError:
                    continue
                if r.status_code == 200 and r.content.startswith(b"\x89PNG"):
                    content = r.content
                    break
        if content is None:
            raise HTTPException(502, "overlay tile unavailable")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})


@app.get("/api/wbd/{level}")
async def wbd_units(level: str, bbox: str, zoom: float):
    if level not in usgs.LEVELS_BY_KEY:
        raise HTTPException(404, "unknown level")
    try:
        box = [float(v) for v in bbox.split(",")]
        assert len(box) == 4
    except (ValueError, AssertionError):
        raise HTTPException(400, "bbox must be w,s,e,n")
    return await usgs.wbd_units_in_view(level, box, zoom)


@app.get("/api/health")
async def health():
    return {"ok": True, **jobs.stats()}


@app.get("/api/levels")
async def levels():
    return [{k: v for k, v in lvl.items() if k in ("key", "label", "min_zoom")} for lvl in usgs.LEVELS]


# The built front end, when present (production); in development Vite serves it.
class _HashedAssets(StaticFiles):
    def file_response(self, *args, **kwargs):
        r = super().file_response(*args, **kwargs)
        r.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # names carry a content hash
        return r


if DIST.exists():
    app.mount("/assets", _HashedAssets(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        file = DIST / path
        if path and file.is_file() and DIST in file.resolve().parents:
            return FileResponse(file, headers={"Cache-Control": "public, max-age=86400"})
        if path.startswith(("data/", "api/", "assets/")):
            raise HTTPException(404)   # a missing tile or file is a 404, not the app shell
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
