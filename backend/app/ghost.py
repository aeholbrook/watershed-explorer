"""Ghost streams: where the land says a creek should run but no stream is mapped.

On the lidar window around the site (the same 1 m tiles the raindrop trace uses,
averaged to 2 m):

  1. priority-flood routing (Barnes et al. 2014, with a FIFO pit queue): every cell
     drains to the neighbour that flooded it, which routes water through depressions
     and across flats toward their spill points;
  2. flow accumulation in reverse flood order;
  3. channel cells where the contributing area exceeds CHANNEL_AREA_M2;
  4. channel lines traced head-to-junction and split into runs that are near a mapped
     NHDPlus stream (or river area) or not. The "not" runs are the ghost streams:
     buried creeks, swales, ditches, and in dense street grids the streets that now
     carry the water.

The heavy loops are numba-compiled; a 3 km window takes a few seconds.
"""

from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor

import numba as nb
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from shapely.geometry import LineString, Point

from .lidar import NODATA, TILE, Grid, _water

RES_FACTOR = 2                 # 1 m lidar averaged to 2 m cells
CHANNEL_AREA_M2 = 60_000       # 6 ha draining through a cell makes a channel
MAPPED_DISTANCE_M = 60         # NHDPlus V2 lines can sit tens of metres off the real channel
MIN_RUN_M = 80

_D8 = np.array([(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)], dtype=np.int64)


@nb.njit(cache=True)
def _heap_push(keys, vals, size, k, v):
    i = size
    keys[i] = k
    vals[i] = v
    while i > 0:
        p = (i - 1) >> 1
        if keys[p] <= keys[i]:
            break
        keys[p], keys[i] = keys[i], keys[p]
        vals[p], vals[i] = vals[i], vals[p]
        i = p
    return size + 1


@nb.njit(cache=True)
def _heap_pop(keys, vals, size):
    v = vals[0]
    size -= 1
    keys[0] = keys[size]
    vals[0] = vals[size]
    i = 0
    while True:
        l = 2 * i + 1
        r = l + 1
        m = i
        if l < size and keys[l] < keys[m]:
            m = l
        if r < size and keys[r] < keys[m]:
            m = r
        if m == i:
            break
        keys[m], keys[i] = keys[i], keys[m]
        vals[m], vals[i] = vals[i], vals[m]
        i = m
    return v, size


@nb.njit(cache=True)
def _route(dem, outlet):
    """Priority-flood with a pit queue. Returns (parent index per cell, processing order)."""
    h, w = dem.shape
    n = h * w
    z = dem.ravel().copy()
    out = outlet.ravel()
    parent = np.full(n, -1, dtype=np.int64)
    closed = np.zeros(n, dtype=np.bool_)
    order = np.empty(n, dtype=np.int64)
    keys = np.empty(n, dtype=np.float64)
    vals = np.empty(n, dtype=np.int64)
    pit = np.empty(n, dtype=np.int64)
    size = 0
    for i in range(n):
        if out[i]:
            closed[i] = True
            size = _heap_push(keys, vals, size, z[i], i)
    pit_head = 0
    pit_tail = 0
    k = 0
    while size > 0 or pit_head < pit_tail:
        if pit_head < pit_tail:
            c = pit[pit_head]
            pit_head += 1
        else:
            c, size = _heap_pop(keys, vals, size)
        order[k] = c
        k += 1
        r = c // w
        q = c % w
        for d in range(8):
            rr = r + _D8[d, 0]
            qq = q + _D8[d, 1]
            if rr < 0 or rr >= h or qq < 0 or qq >= w:
                continue
            nb_ = rr * w + qq
            if closed[nb_]:
                continue
            closed[nb_] = True
            parent[nb_] = c
            if z[nb_] <= z[c]:
                z[nb_] = z[c]
                pit[pit_tail] = nb_
                pit_tail += 1
            else:
                size = _heap_push(keys, vals, size, z[nb_], nb_)
    return parent, order[:k]


@nb.njit(cache=True)
def _accumulate(parent, order):
    acc = np.ones(parent.shape[0], dtype=np.float64)
    for k in range(order.shape[0] - 1, -1, -1):
        c = order[k]
        p = parent[c]
        if p >= 0:
            acc[p] += acc[c]
    return acc


@nb.njit(cache=True)
def _channels(parent, acc, threshold):
    """Trace channel lines from each head down to the first junction already traced."""
    n = parent.shape[0]
    is_ch = acc >= threshold
    has_up = np.zeros(n, dtype=np.bool_)
    for c in range(n):
        if is_ch[c] and parent[c] >= 0 and is_ch[parent[c]]:
            has_up[parent[c]] = True
    visited = np.zeros(n, dtype=np.bool_)
    cells = np.empty(n, dtype=np.int64)
    starts = np.empty(n + 1, dtype=np.int64)
    m = 0
    s = 0
    # Heads first, then any channel cell not yet reached (rare: loops around flats).
    for pass_ in range(2):
        for c0 in range(n):
            if not is_ch[c0] or visited[c0] or (pass_ == 0 and has_up[c0]):
                continue
            starts[s] = m
            s += 1
            c = c0
            while True:
                cells[m] = c
                m += 1
                if visited[c]:
                    break
                visited[c] = True
                p = parent[c]
                if p < 0 or not is_ch[p]:
                    break
                c = p
    starts[s] = m
    return cells[:m], starts[: s + 1]


def _window(lon: float, lat: float) -> tuple[Grid, np.ndarray, int, int]:
    grid = Grid(lat, 1.0)
    gx, gy = grid.cell(lon, lat)
    tx0, ty0 = gx // TILE - 1, gy // TILE - 1
    coords = [(tx0 + i, ty0 + j) for j in range(3) for i in range(3)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        tiles = dict(zip(coords, pool.map(lambda t: grid.tile(*t), coords)))
    z = np.block([[tiles[(tx0 + i, ty0 + j)] for i in range(3)] for j in range(3)]).astype(np.float64)
    nodata = z <= NODATA + 1
    f = RES_FACTOR
    h, w = z.shape[0] // f, z.shape[1] // f
    zb = z[: h * f, : w * f].reshape(h, f, w, f)
    nb_ = nodata[: h * f, : w * f].reshape(h, f, w, f).any(axis=(1, 3))
    z2 = zb.mean(axis=(1, 3))
    z2[nb_] = -1000.0
    return grid, z2, tx0 * TILE, ty0 * TILE


def ghost_streams(lon: float, lat: float) -> dict:
    t0 = time.time()
    grid, dem, ox, oy = _window(lon, lat)
    h, w = dem.shape
    cell = grid.ground_res * RES_FACTOR

    outlet = np.zeros(dem.shape, dtype=np.bool_)
    outlet[0, :] = outlet[-1, :] = outlet[:, 0] = outlet[:, -1] = True
    outlet |= dem <= -999
    parent, order = _route(dem, outlet)
    acc = _accumulate(parent, order)
    cells, starts = _channels(parent, acc, CHANNEL_AREA_M2 / cell ** 2)

    # Mapped water on the same grid: NHDPlus lines grown by MAPPED_DISTANCE_M, plus river areas.
    def to_px(x, y):
        gx, gy = grid.cell(x, y)
        return (gx - ox) / RES_FACTOR, (gy - oy) / RES_FACTOR

    lines, areas = _water(lon, lat, 0.05)
    canvas = Image.new("1", (w, h), 0)
    pen = ImageDraw.Draw(canvas)
    for g in lines:
        for part in ([g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]):
            pen.line([to_px(*c[:2]) for c in part], fill=1, width=1)
    mapped_lines = np.array(canvas, dtype=bool)
    mapped = ndimage.distance_transform_edt(~mapped_lines) * cell <= MAPPED_DISTANCE_M if mapped_lines.any() \
        else np.zeros((h, w), dtype=bool)
    if areas:
        canvas = Image.new("1", (w, h), 0)
        pen = ImageDraw.Draw(canvas)
        for g in areas:
            for poly in ([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]):
                pen.polygon([to_px(*c[:2]) for c in poly[0]], fill=1)
        mapped |= np.array(canvas, dtype=bool)
    mapped_flat = mapped.ravel()

    def lonlat(c):
        r, q = divmod(int(c), w)
        return grid.lonlat(ox + RES_FACTOR * q + 0.5, oy + RES_FACTOR * r + 0.5)

    features = []
    ghost_m = 0.0
    site = Point(lon, lat)
    nearest = None
    for a, b in zip(starts[:-1], starts[1:]):
        seg = cells[a:b]
        if len(seg) < 2:
            continue
        # Split into runs of mapped / not mapped.
        run_start = 0
        for i in range(1, len(seg) + 1):
            if i < len(seg) and mapped_flat[seg[i]] == mapped_flat[seg[run_start]]:
                continue
            run = seg[run_start: i + 1 if i < len(seg) else i]
            is_ghost = not mapped_flat[seg[run_start]]
            run_start = i
            if len(run) < 2:
                continue
            rc = [divmod(int(c), w) for c in run]
            length = sum(math.hypot(r2 - r1, q2 - q1) for (r1, q1), (r2, q2) in zip(rc, rc[1:])) * cell
            if length < MIN_RUN_M or not is_ghost:
                continue
            simple = LineString([(q, r) for r, q in rc]).simplify(1.2)
            coords = [grid.lonlat(ox + RES_FACTOR * x + 0.5, oy + RES_FACTOR * y + 0.5) for x, y in simple.coords]
            area_ha = acc[run[-1]] * cell ** 2 / 1e4
            ghost_m += length
            line = LineString(coords)
            dist = site.distance(line) * 111_320 * math.cos(math.radians(lat))
            if nearest is None or dist < nearest["dist_m"]:
                nearest = {"dist_m": round(dist), "area_ha": round(float(area_ha), 1)}
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[round(x, 7), round(y, 7)] for x, y in coords]},
                "properties": {"area_ha": round(float(area_ha), 1), "length_m": round(length)},
            })
    return {
        "lines": {"type": "FeatureCollection", "features": features},
        "ghost_km": round(ghost_m / 1000, 2),
        "count": len(features),
        "nearest": nearest,
        "window_km": round(w * cell / 1000, 1),
        "threshold_ha": CHANNEL_AREA_M2 / 1e4,
        "seconds": round(time.time() - t0, 1),
    }
