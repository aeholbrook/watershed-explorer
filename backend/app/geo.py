"""Small geometry helpers on lon/lat coordinates."""

from __future__ import annotations

import math
from typing import Iterable, Sequence

from shapely.geometry import shape, mapping
from shapely.geometry.base import BaseGeometry

EARTH_R = 6_371_008.8


def haversine_m(a: Sequence[float], b: Sequence[float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(h)))


def line_length_m(coords: Sequence[Sequence[float]]) -> float:
    return sum(haversine_m(p, q) for p, q in zip(coords, coords[1:]))


def _ring_area_m2(ring: Sequence[Sequence[float]]) -> float:
    # Spherical excess approximation; good from lot scale up to continental basins.
    total = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        total += math.radians(x2 - x1) * (2 + math.sin(math.radians(y1)) + math.sin(math.radians(y2)))
    return abs(total) * EARTH_R ** 2 / 2


def polygon_area_km2(geometry: dict) -> float:
    def poly(rings):
        return _ring_area_m2(rings[0]) - sum(_ring_area_m2(r) for r in rings[1:])

    if geometry["type"] == "Polygon":
        a = poly(geometry["coordinates"])
    elif geometry["type"] == "MultiPolygon":
        a = sum(poly(p) for p in geometry["coordinates"])
    else:
        return 0.0
    return a / 1e6


def bbox(geometry: dict) -> list[float]:
    return list(shape(geometry).bounds)


def simplify(geometry: dict, tolerance_deg: float) -> dict:
    g: BaseGeometry = shape(geometry)
    if tolerance_deg > 0:
        g = g.simplify(tolerance_deg, preserve_topology=True)
    return round_coords(mapping(g))


def round_coords(geometry: dict, ndigits: int = 6) -> dict:
    def r(c):
        if isinstance(c[0], (int, float)):
            return [round(c[0], ndigits), round(c[1], ndigits), *c[2:]]
        return [r(x) for x in c]

    return {"type": geometry["type"], "coordinates": r(geometry["coordinates"])}


def label_point(geometry: dict) -> list[float]:
    """A point guaranteed inside the polygon (its largest part), for placing a name."""
    g = shape(geometry)
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda p: p.area)
    pt = g.representative_point()
    return [round(pt.x, 5), round(pt.y, 5)]


def merge_bounds(boxes: Iterable[Sequence[float]]) -> list[float]:
    boxes = list(boxes)
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]
