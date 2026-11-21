"""Build static national watershed layers (HUC2–HUC8) for the "colour the map" view.

One-time download from the USGS Watershed Boundary Dataset map service (~70 requests),
generalized per level, with a label point and a map colour per unit. Neighbouring units
get different colours (DSATUR graph colouring over five validated hues), so the map
reads like a political map of watersheds without needing a hue per region.

Writes frontend/public/data/wbd/huc{2,4,6,8}.geojson.

    .venv/bin/python scripts/build_wbd_national.py
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from shapely import STRtree, make_valid
from shapely.geometry import mapping, shape

WBD = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer"
OUT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "data" / "wbd"
LEVELS = [  # key, layer, generalization (degrees)
    ("huc2", 1, 0.02), ("huc4", 2, 0.01), ("huc6", 3, 0.005), ("huc8", 4, 0.0025),
]
REGIONS = [f"{i:02d}" for i in range(1, 23)]
N_COLOURS = 5


def query(layer: int, where: str, key: str, offset: float) -> list[dict]:
    feats, start = [], 0
    while True:
        params = {"where": where, "outFields": f"{key},name,areasqkm,states", "returnGeometry": "true",
                  "outSR": 4326, "maxAllowableOffset": offset, "geometryPrecision": 4, "f": "geojson",
                  "resultOffset": start, "resultRecordCount": 1000, "orderByFields": key}
        url = f"{WBD}/{layer}/query?" + urllib.parse.urlencode(params)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=180) as r:
                    data = json.loads(r.read())
                break
            except Exception as e:  # flaky gateway: retry with backoff
                if attempt == 3:
                    raise
                print(f"  retry {where}: {e}", flush=True)
                time.sleep(2 * (attempt + 1))
        page = data.get("features", [])
        feats += page
        if len(page) < 1000:
            return feats
        start += len(page)


def dsatur(neighbours: list[set[int]]) -> list[int]:
    """Graph colouring: always colour the vertex with the most differently-coloured neighbours."""
    n = len(neighbours)
    colour = [-1] * n
    sat = [set() for _ in range(n)]
    for _ in range(n):
        v = max((i for i in range(n) if colour[i] < 0), key=lambda i: (len(sat[i]), len(neighbours[i])))
        free = [c for c in range(N_COLOURS) if c not in sat[v]]
        if free:
            colour[v] = free[0]
        else:  # rare with five colours: take the colour fewest neighbours use
            used = [sum(1 for u in neighbours[v] if colour[u] == c) for c in range(N_COLOURS)]
            colour[v] = used.index(min(used))
        for u in neighbours[v]:
            sat[u].add(colour[v])
    return colour


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for key, layer, offset in LEVELS:
        t0 = time.time()
        feats = []
        if key == "huc2":
            feats = query(layer, "1=1", key, offset)
        else:
            for reg in REGIONS:
                feats += query(layer, f"{key} LIKE '{reg}%'", key, offset)
        geoms = [make_valid(shape(f["geometry"])) for f in feats]
        tree = STRtree(geoms)
        # Generalized neighbours don't share edges exactly: "touching" = within 2x the tolerance.
        neighbours = [set() for _ in geoms]
        for i, g in enumerate(geoms):
            for j in tree.query(g.buffer(offset * 2), predicate="intersects"):
                if j != i:
                    neighbours[i].add(int(j))
                    neighbours[int(j)].add(i)
        colours = dsatur(neighbours)
        out = []
        for f, g, c in zip(feats, geoms, colours):
            p = f["properties"]
            part = max(g.geoms, key=lambda q: q.area) if g.geom_type == "MultiPolygon" else g
            lp = part.representative_point()
            out.append({"type": "Feature", "id": int(p[key]), "geometry": mapping(g),
                        "properties": {"code": p[key], "name": p.get("name"), "areasqkm": round(p.get("areasqkm") or 0),
                                       "states": p.get("states"), "c": c,
                                       "lx": round(lp.x, 4), "ly": round(lp.y, 4)}})
        path = OUT / f"{key}.geojson"
        path.write_text(json.dumps({"type": "FeatureCollection", "features": out}, separators=(",", ":")))
        print(f"{key}: {len(out)} units, {path.stat().st_size // 1024} KB, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
