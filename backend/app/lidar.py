"""Lot-scale raindrop trace on USGS 3DEP lidar (bare-earth DEM).

NLDI's raindrop trace follows the 30 m NHDPlus flow-direction grid, which can't
resolve a yard, a driveway or a street crown. This module:

  1. assembles a DEM window around the start point from 3DEP tiles
     (first 3 km at 1 m; if the water leaves that, 15 km at 5 m);
  2. fills every depression in the window to its spill level in one pass
     (morphological reconstruction, compiled in scikit-image), so pits never
     have to be flooded one at a time;
  3. walks downhill on the filled surface: steepest descent, and across flat
     filled ponds by breadth-first search to the nearest spill cell;
  4. stops within STREAM_BUFFER_M of an NHDPlus flowline.

Every cell is visited at most once, so a trace is linear in its length no matter
how pitted the terrain is (flat city blocks used to make the old walk quadratic).

The DEM is bare earth, so buildings are absent: roof water is assumed to reach the
ground at the pin. Storm sewers aren't in the DEM either; deep filled spots in
streets are often inlets where the real water leaves the surface.
"""

from __future__ import annotations

import io
import math
import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from shapely.geometry import LineString
from skimage.draw import line as draw_line
from skimage.morphology import reconstruction

from .http import UpstreamError, fetch_bytes_sync, fetch_json_sync

DEP = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"
WFS = "https://api.water.usgs.gov/geoserver/wmadata/ows"
DEM_DIR = Path(__file__).resolve().parent.parent / "cache" / "dem"
DEM_CACHE_MAX_BYTES = 800 * 1024 * 1024

TILE = 1024
# Window levels: ground resolution and how many tiles out from the start's tile.
LEVELS = [
    {"res_m": 1.0, "radius": 1},   # 3 x 3 tiles, ~3 km across
    {"res_m": 5.0, "radius": 1},   # 3 x 3 tiles, ~15 km across
]
EDGE_MARGIN_M = 250          # when a level fails, keep its path up to this far from the edge
STREAM_BUFFER_M = 25.0
MIN_POND_DEPTH_M = 0.3
TIME_BUDGET_S = 60
NODATA = -9999.0
R_MERC = 6378137.0
FLAT_EPS = 1e-4

NEIGHBOURS = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
NEIGHBOUR_DIST = [math.hypot(dx, dy) for dx, dy in NEIGHBOURS]


def _merc(lon: float, lat: float) -> tuple[float, float]:
    return (math.radians(lon) * R_MERC,
            math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R_MERC)


def _lonlat(x: float, y: float) -> tuple[float, float]:
    return (math.degrees(x / R_MERC),
            math.degrees(2 * math.atan(math.exp(y / R_MERC)) - math.pi / 2))


class _Budget(Exception):
    pass


class Grid:
    """A fixed Web Mercator pixel grid at one ground resolution."""

    def __init__(self, lat: float, res_m: float):
        # Mercator inflates distances by 1/cos(lat); quantize so nearby traces share tiles.
        step = 0.05 * res_m
        self.res = round(res_m / math.cos(math.radians(lat)) / step) * step
        self.ground_res = self.res * math.cos(math.radians(lat))

    def cell(self, lon: float, lat: float) -> tuple[int, int]:
        x, y = _merc(lon, lat)
        return int(math.floor(x / self.res)), int(math.floor(-y / self.res))

    def lonlat(self, gx: float, gy: float) -> tuple[float, float]:
        return _lonlat((gx + 0.5) * self.res, -(gy + 0.5) * self.res)

    def tile(self, tx: int, ty: int) -> np.ndarray:
        path = DEM_DIR / f"r{self.res:.2f}_{tx}_{ty}.npy"
        if path.exists():
            os.utime(path)
            return np.load(path)
        arr = self._download(tx, ty)
        DEM_DIR.mkdir(parents=True, exist_ok=True)
        np.save(path, arr)
        _trim_cache()
        return arr

    def _download(self, tx: int, ty: int) -> np.ndarray:
        xmin, xmax = tx * TILE * self.res, (tx + 1) * TILE * self.res
        ymax, ymin = -ty * TILE * self.res, -(ty + 1) * TILE * self.res
        params = {
            "bbox": f"{xmin},{ymin},{xmax},{ymax}", "bboxSR": 3857, "imageSR": 3857,
            "size": f"{TILE},{TILE}", "format": "tiff", "pixelType": "F32",
            "noData": NODATA, "interpolation": "RSP_BilinearInterpolation", "f": "json",
        }
        last: Exception | None = None
        # The export link occasionally 504s; asking for a fresh export usually works.
        for attempt in range(4):
            if attempt:
                time.sleep(2 * attempt)
            meta = fetch_json_sync(f"{DEP}/exportImage", params=params, ttl=0)
            try:
                raw = fetch_bytes_sync(meta["href"], expect=b"II*\x00", retries=2)
                break
            except UpstreamError as e:
                last = e
        else:
            raise last or UpstreamError("3DEP", "export failed")
        arr = np.asarray(Image.open(io.BytesIO(raw)), dtype=np.float32)
        if arr.shape != (TILE, TILE):
            raise UpstreamError("3DEP", f"unexpected tile shape {arr.shape}")
        return arr


def _trim_cache() -> None:
    files = sorted(DEM_DIR.glob("*.npy"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    while files and total > DEM_CACHE_MAX_BYTES:
        p = files.pop(0)
        total -= p.stat().st_size
        p.unlink(missing_ok=True)


def _water(lon: float, lat: float, d: float) -> tuple[list[dict], list[dict]]:
    """NHDPlus flowlines, plus NHD river-area polygons (wide rivers are drawn as areas)."""
    def query(type_name: str, props: str) -> list[dict]:
        data = fetch_json_sync(WFS, params={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": type_name, "outputFormat": "application/json",
            "srsName": "EPSG:4326", "propertyName": props,
            # WFS 2.0 with an EPSG URN takes latitude first.
            "bbox": f"{lat - d},{lon - d},{lat + d},{lon + d},urn:ogc:def:crs:EPSG::4326",
        })
        return data.get("features", [])

    lines = [f["geometry"] for f in query("wmadata:nhdflowline_network", "comid,the_geom")]
    areas = [f["geometry"] for f in query("wmadata:nhdarea", "comid,ftype,the_geom")
             if f["properties"].get("ftype") == "StreamRiver"]
    return lines, areas


class Window:
    """A filled DEM window plus a mask of 'reached a stream' cells."""

    def __init__(self, grid: Grid, center: tuple[int, int], radius: int,
                 water: tuple[list[dict], list[dict]], deadline: float):
        self.grid = grid
        ctx, cty = center[0] // TILE, center[1] // TILE
        self.tx0, self.ty0 = ctx - radius, cty - radius
        n = 2 * radius + 1
        coords = [(self.tx0 + i, self.ty0 + j) for j in range(n) for i in range(n)]
        def fetch(t):
            # A tile 3DEP won't serve becomes a gap (water drains off into it) rather than
            # failing the whole trace.
            try:
                return grid.tile(*t)
            except UpstreamError:
                self.missing_tiles += 1
                return np.full((TILE, TILE), NODATA, dtype=np.float32)

        self.missing_tiles = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            tiles = dict(zip(coords, pool.map(fetch, coords)))
        if time.time() > deadline:
            raise _Budget()
        z = np.block([[tiles[(self.tx0 + i, self.ty0 + j)] for i in range(n)] for j in range(n)])
        self.ox, self.oy = self.tx0 * TILE, self.ty0 * TILE
        self.h, self.w = z.shape
        nodata = z <= NODATA + 1
        z = np.where(nodata, np.float32(-1000.0), z).astype(np.float64)
        self.z = z

        # Fill depressions: reconstruction by erosion from the window border and any
        # gaps in the lidar, which act as outlets.
        seed = np.full_like(z, z.max())
        border = np.zeros_like(nodata)
        border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
        outlets = border | nodata
        seed[outlets] = z[outlets]
        self.f = reconstruction(seed, z, method="erosion")
        self.nodata = nodata

        # Ponds: connected filled areas, labelled once so each pond's size is a lookup.
        self.pond_labels, _ = ndimage.label(self.f - z > 0.01)
        self.pond_sizes = np.bincount(self.pond_labels.ravel())

        # Stream mask: NHDPlus flowlines drawn into the window and grown by the buffer,
        # plus river-area polygons as they are.
        lines, areas = water
        lines_mask = np.zeros(z.shape, dtype=bool)
        for g in lines:
            parts = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
            for part in parts:
                px = [self.to_px(*c[:2]) for c in part]
                for (c0, r0), (c1, r1) in zip(px, px[1:]):
                    rr, cc = draw_line(r0, c0, r1, c1)
                    ok = (rr >= 0) & (rr < self.h) & (cc >= 0) & (cc < self.w)
                    lines_mask[rr[ok], cc[ok]] = True
        if lines_mask.any():
            dist = ndimage.distance_transform_edt(~lines_mask)
            self.stream = dist * grid.ground_res <= STREAM_BUFFER_M
        else:
            self.stream = lines_mask
        if areas:
            # Pillow's scanline fill: one pass, however many vertices the river outline has.
            canvas = Image.new("1", (self.w, self.h), 0)
            pen = ImageDraw.Draw(canvas)
            for g in areas:
                polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
                for poly in polys:
                    for k, ring in enumerate(poly):
                        pen.polygon([self.to_px(*c[:2]) for c in ring], fill=0 if k else 1)
            self.stream |= np.array(canvas, dtype=bool)

    def to_px(self, lon: float, lat: float) -> tuple[int, int]:
        gx, gy = self.grid.cell(lon, lat)
        return gx - self.ox, gy - self.oy

    def lonlat(self, c: int, r: int) -> tuple[float, float]:
        return self.grid.lonlat(c + self.ox, r + self.oy)

    def inside(self, c: int, r: int) -> bool:
        return 0 <= c < self.w and 0 <= r < self.h

    def on_edge(self, c: int, r: int) -> bool:
        return c in (0, self.w - 1) or r in (0, self.h - 1)

    def edge_distance_m(self, c: int, r: int) -> float:
        return min(c, r, self.w - 1 - c, self.h - 1 - r) * self.grid.ground_res

    def walk(self, start: tuple[int, int], deadline: float) -> tuple[list, str]:
        """Steepest descent on the filled surface; BFS across flats. Returns (cells, reason)."""
        f = self.f
        path = [start]
        seen = {start}
        cur = start
        while True:
            if len(path) % 256 == 0 and time.time() > deadline:
                return path, "time_budget"
            c, r = cur
            if self.stream[r, c]:
                return path, "stream"
            if self.nodata[r, c]:
                return path, "no_lidar"
            if self.on_edge(c, r):
                return path, "edge"
            fz = f[r, c]
            best, best_slope = None, 0.0
            for (dx, dy), dist in zip(NEIGHBOURS, NEIGHBOUR_DIST):
                n = (c + dx, r + dy)
                if n in seen:
                    continue
                s = (fz - f[n[1], n[0]]) / dist
                if s > best_slope:
                    best, best_slope = n, s
            if best is not None:
                path.append(best)
                seen.add(best)
                cur = best
                continue
            route = self._cross_flat(cur, seen)
            if not route:
                return path, "depression"
            path.extend(route)
            cur = route[-1]

    def _cross_flat(self, start, seen: set) -> list:
        """Breadth-first across a flat (usually a filled pond) to the nearest cell that drains on."""
        f = self.f
        level = f[start[1], start[0]]
        prev = {start: None}
        q = deque([start])
        target = None
        while q and target is None:
            c, r = q.popleft()
            for dx, dy in NEIGHBOURS:
                n = (c + dx, r + dy)
                if n in prev or n in seen or not self.inside(*n):
                    continue
                nz = f[n[1], n[0]]
                if nz < level - FLAT_EPS or self.stream[n[1], n[0]] or self.on_edge(*n):
                    target = n
                    prev[n] = (c, r)
                    break
                if nz <= level + FLAT_EPS:
                    prev[n] = (c, r)
                    q.append(n)
        seen.update(prev)
        if target is None:
            return []
        route, node = [], target
        while node is not None and node != start:
            route.append(node)
            node = prev[node]
        route.reverse()
        return route

    def ponds(self, path: list) -> list[dict]:
        """Filled spots along the route at least MIN_POND_DEPTH_M deep, one per pond."""
        best: dict[int, tuple[float, tuple[int, int]]] = {}
        for c, r in path:
            lab = int(self.pond_labels[r, c])
            if lab:
                depth = self.f[r, c] - self.z[r, c]
                if depth > best.get(lab, (0.0, (0, 0)))[0]:
                    best[lab] = (depth, (c, r))
        out = []
        for lab, (depth, (c, r)) in best.items():
            if depth >= MIN_POND_DEPTH_M:
                lon, lat = self.lonlat(c, r)
                out.append({"lon": round(lon, 7), "lat": round(lat, 7), "depth_m": round(float(depth), 2),
                            "area_m2": round(float(self.pond_sizes[lab]) * self.grid.ground_res ** 2),
                            "sink": False})
        return out


def trace(lon: float, lat: float) -> dict:
    t0 = time.time()
    deadline = t0 + TIME_BUDGET_S
    water = _water(lon, lat, 0.1)

    pieces: list[dict] = []
    start_ll = (lon, lat)
    reason = "edge"
    try:
        for i, lvl in enumerate(LEVELS):
            grid = Grid(lat, lvl["res_m"])
            win = Window(grid, grid.cell(*start_ll), lvl["radius"], water, deadline)
            start = win.to_px(*start_ll)
            if win.nodata[start[1], start[0]]:
                if not pieces:
                    raise UpstreamError("3DEP", "no lidar elevation at this point")
                reason = "no_lidar"
                break
            cells, reason = win.walk(start, deadline)
            if reason == "edge" and i + 1 < len(LEVELS):
                # Keep the fine path until it nears the edge, then continue at the next level.
                keep = len(cells)
                while keep > 1 and win.edge_distance_m(*cells[keep - 1]) < EDGE_MARGIN_M:
                    keep -= 1
                cells = cells[:keep]
            pieces.append({
                "ll": [win.lonlat(c, r) for c, r in cells],
                "z": [float(win.z[r, c]) for c, r in cells],
                "grid": cells,
                "res": grid.ground_res,
                "ponds": win.ponds(cells),
            })
            if reason != "edge":
                break
            start_ll = pieces[-1]["ll"][-1]
        else:
            reason = "too_far"
    except _Budget:
        reason = "time_budget"
    if not pieces:
        raise UpstreamError("3DEP", "lidar trace ran out of time before the elevation data arrived")
    return _result(pieces, reason, time.time() - t0)


def _result(pieces: list[dict], reason: str, seconds: float) -> dict:
    coords: list[list[float]] = []
    profile: list[list[float]] = []
    ponds: list[dict] = []
    dist = 0.0
    for piece in pieces:
        cells = piece["grid"]
        # D8 steps zig-zag; draw and measure a lightly simplified line (in cell units).
        if len(cells) > 1:
            kept = list(LineString(cells).simplify(1.5, preserve_topology=False).coords)
        else:
            kept = [cells[0]]
        length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(kept, kept[1:])) * piece["res"]
        idx = {cell: i for i, cell in enumerate(cells)}
        for x, y in kept:
            i = idx.get((int(round(x)), int(round(y))))
            if i is not None:
                coords.append([round(piece["ll"][i][0], 7), round(piece["ll"][i][1], 7)])
        raw = [0.0]
        for a, b in zip(cells, cells[1:]):
            raw.append(raw[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        scale = length / raw[-1] if raw[-1] else 0
        step = max(1, len(cells) // 500)
        for i in sorted(set(range(0, len(cells), step)) | {len(cells) - 1}):
            profile.append([round(dist + raw[i] * scale, 1), round(piece["z"][i], 2),
                            round(piece["ll"][i][0], 7), round(piece["ll"][i][1], 7)])
        dist += length
        ponds.extend(piece["ponds"])
    if len(coords) < 2:
        ll = pieces[0]["ll"][0]
        coords = [[round(ll[0], 7), round(ll[1], 7)]] * 2
    first, last = pieces[0], pieces[-1]
    return {
        "path": {"type": "LineString", "coordinates": coords},
        "length_m": round(dist, 1),
        "start_elev_m": round(first["z"][0], 2),
        "end_elev_m": round(last["z"][-1], 2),
        "end": {"lon": round(last["ll"][-1][0], 7), "lat": round(last["ll"][-1][1], 7)},
        "end_reason": reason,
        "ponds": ponds,
        "profile": profile,
        "resolution_m": round(first["res"], 2),
        "levels_used": [round(p["res"], 1) for p in pieces],
        "seconds": round(seconds, 1),
    }
