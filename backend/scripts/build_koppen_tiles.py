"""Build static Köppen-Geiger climate-zone map tiles for North America, past to 2099.

Source: Beck et al. (2023), High-resolution (1 km) Köppen-Geiger maps for 1901–2099
based on constrained CMIP6 projections, Scientific Data 10, 724. CC BY 4.0.
https://figshare.com/articles/dataset/21789074  (koppen_geiger_tif.zip, 125 MB)

Writes frontend/public/data/koppen/<period>/<z>/<x>/<y>.png (z0–7, Web Mercator, 256 px).
Each tile is a paletted PNG in Beck's colours, which are unique per class, so the browser
can read the climate zone under the cursor straight from the tile with no API call.

    .venv/bin/python scripts/build_koppen_tiles.py /path/to/koppen_geiger_tif.zip
"""

import io
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject
from rasterio.windows import from_bounds as window_from_bounds

OUT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "data" / "koppen"
PERIODS = ["1901_1930", "1931_1960", "1961_1990", "1991_2020",
           "2041_2070/ssp126", "2041_2070/ssp245", "2041_2070/ssp585",
           "2071_2099/ssp126", "2071_2099/ssp245", "2071_2099/ssp585"]
BOUNDS = (-170.0, 15.0, -52.0, 72.0)           # North America incl. Alaska, Hawaii, Puerto Rico
MAX_ZOOM = 7                                   # ~1 km per pixel at mid-latitudes, matching the data
R = 6378137.0

CLASSES = [
    ("Af", "Tropical, rainforest", (0, 0, 255)), ("Am", "Tropical, monsoon", (0, 120, 255)),
    ("Aw", "Tropical, savannah", (70, 170, 250)), ("BWh", "Arid, desert, hot", (255, 0, 0)),
    ("BWk", "Arid, desert, cold", (255, 150, 150)), ("BSh", "Arid, steppe, hot", (245, 165, 0)),
    ("BSk", "Arid, steppe, cold", (255, 220, 100)), ("Csa", "Temperate, dry summer, hot summer", (255, 255, 0)),
    ("Csb", "Temperate, dry summer, warm summer", (200, 200, 0)), ("Csc", "Temperate, dry summer, cold summer", (150, 150, 0)),
    ("Cwa", "Temperate, dry winter, hot summer", (150, 255, 150)), ("Cwb", "Temperate, dry winter, warm summer", (100, 200, 100)),
    ("Cwc", "Temperate, dry winter, cold summer", (50, 150, 50)), ("Cfa", "Temperate, no dry season, hot summer", (200, 255, 80)),
    ("Cfb", "Temperate, no dry season, warm summer", (100, 255, 80)), ("Cfc", "Temperate, no dry season, cold summer", (50, 200, 0)),
    ("Dsa", "Cold, dry summer, hot summer", (255, 0, 255)), ("Dsb", "Cold, dry summer, warm summer", (200, 0, 200)),
    ("Dsc", "Cold, dry summer, cold summer", (150, 50, 150)), ("Dsd", "Cold, dry summer, very cold winter", (150, 100, 150)),
    ("Dwa", "Cold, dry winter, hot summer", (170, 175, 255)), ("Dwb", "Cold, dry winter, warm summer", (90, 120, 220)),
    ("Dwc", "Cold, dry winter, cold summer", (75, 80, 180)), ("Dwd", "Cold, dry winter, very cold winter", (50, 0, 135)),
    ("Dfa", "Cold, no dry season, hot summer", (0, 255, 255)), ("Dfb", "Cold, no dry season, warm summer", (55, 200, 255)),
    ("Dfc", "Cold, no dry season, cold summer", (0, 125, 125)), ("Dfd", "Cold, no dry season, very cold winter", (0, 70, 95)),
    ("ET", "Polar, tundra", (178, 178, 178)), ("EF", "Polar, frost", (102, 102, 102)),
]


def tile_range(z: int):
    def tx(lon):
        return int((lon + 180) / 360 * 2 ** z)

    def ty(lat):
        r = math.radians(lat)
        return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2 ** z)

    w, s, e, n = BOUNDS
    return range(tx(w), tx(e) + 1), range(ty(n), ty(s) + 1)


def tile_bounds(z: int, x: int, y: int):
    size = 2 * math.pi * R / 2 ** z
    x0 = -math.pi * R + x * size
    y1 = math.pi * R - y * size
    return x0, y1 - size, x0 + size, y1


def mercator_to_lonlat(x, y):
    return math.degrees(x / R), math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)


def main(zip_path: str) -> None:
    palette = [0, 0, 0] + [c for _, _, rgb in CLASSES for c in rgb]
    palette += [0] * (768 - len(palette))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "legend.json").write_text(json.dumps(
        [{"value": i + 1, "code": c, "name": n, "color": "#%02x%02x%02x" % rgb} for i, (c, n, rgb) in enumerate(CLASSES)]))
    meta = {"periods": PERIODS, "max_zoom": MAX_ZOOM, "bounds": BOUNDS,
            "source": "Beck et al. (2023), Scientific Data 10, 724. CC BY 4.0"}
    (OUT / "meta.json").write_text(json.dumps(meta))
    for period in PERIODS:
        t0, written = time.time(), 0
        with rasterio.open(f"/vsizip/{zip_path}/{period}/koppen_geiger_0p00833333.tif") as src:
            for z in range(MAX_ZOOM + 1):
                xs, ys = tile_range(z)
                for x in xs:
                    for y in ys:
                        b = tile_bounds(z, x, y)
                        w, s = mercator_to_lonlat(b[0], b[1])
                        e, n = mercator_to_lonlat(b[2], b[3])
                        pad = (e - w) / 64
                        win = window_from_bounds(w - pad, s - pad, e + pad, n + pad, src.transform).round_offsets().round_lengths()
                        data = src.read(1, window=win, boundless=True, fill_value=0)
                        if not data.any():
                            continue
                        dst = np.zeros((256, 256), dtype=np.uint8)
                        reproject(data, dst, src_transform=src.window_transform(win), src_crs="EPSG:4326",
                                  dst_transform=from_bounds(*b, 256, 256), dst_crs="EPSG:3857",
                                  resampling=Resampling.nearest, src_nodata=0, dst_nodata=0)
                        if not dst.any():
                            continue
                        img = Image.fromarray(dst, mode="P")
                        img.putpalette(palette)
                        buf = io.BytesIO()
                        img.save(buf, format="PNG", optimize=True, transparency=0)
                        path = OUT / period / str(z) / str(x) / f"{y}.png"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(buf.getvalue())
                        written += 1
        print(f"{period}: {written} tiles in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
