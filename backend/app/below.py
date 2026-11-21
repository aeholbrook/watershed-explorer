"""What happens to rain that soaks in: soil, surface, aquifer, karst, groundwater.

Sources (all public, no keys):
  Soil      USDA NRCS Soil Data Access (SSURGO), point-in-map-unit SQL
  Surface   Annual NLCD 2025 fractional impervious + land cover, WMS GetMap on the 30 m grid
  Storms    NOAA Atlas 14 24-hour depths (runoff.py)
  Aquifer   USGS Principal Aquifers (2003, 1:2.5M), Esri Living Atlas copy
  Karst     USGS Karst in the US (OFR 2014-1156) + state sinkhole inventories (MO, IN/KY)
  Wells     USGS Water Data OGC API, latest continuous depth-to-water (parameter 72019)
Each part is independent; a failing source leaves its row out instead of failing the panel.
"""

from __future__ import annotations

import asyncio
import io
import math
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image

from . import albers, geo, runoff
from .http import UpstreamError, async_client, fetch_json

SDA = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
NLCD = "https://dmsdata.cr.usgs.gov/geoserver"
NLCD_YEAR = "2025-01-01T00:00:00.000Z"
AQUIFERS = ("https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
            "USA_Aquifers_Feature_Layer_view/FeatureServer/0/query")
KARST = "https://services.arcgis.com/hoKRg7d6zCP8hwp2/arcgis/rest/services/{}/FeatureServer/0/query"
KARST_LAYERS = ["Carbonate_Karst", "Evaporite_Karst", "Sandstone_Karst"]
SINKHOLES = [
    # (service, bounding box it covers: w, s, e, n)
    ("https://gis.dnr.mo.gov/host/rest/services/geology/sinkholes/MapServer/1/query", (-95.8, 35.9, -89.0, 40.7)),
    ("https://portal.igs.indiana.edu/arcgis/rest/services/Sinkhole_Inventory_IN_KY_2011/MapServer/0/query",
     (-89.6, 36.4, -81.9, 41.8)),
]
WATERDATA = "https://api.waterdata.usgs.gov/ogcapi/v0/collections"

LAND_RADIUS_PX = 5            # 11 x 11 NLCD pixels: ~330 m square centred on the pin
SINKHOLE_RADIUS_M = 2000
WELL_RADIUS_DEG = 0.3         # ~30 km
FT = 0.3048

NLCD_CLASSES = {
    (70, 107, 159): (11, "open water"), (209, 222, 248): (12, "ice/snow"),
    (222, 197, 197): (21, "developed, open space"), (217, 146, 130): (22, "developed, low intensity"),
    (235, 0, 0): (23, "developed, medium intensity"), (171, 0, 0): (24, "developed, high intensity"),
    (179, 172, 159): (31, "barren"), (104, 171, 95): (41, "deciduous forest"),
    (28, 95, 44): (42, "evergreen forest"), (181, 197, 143): (43, "mixed forest"),
    (204, 184, 121): (52, "shrubland"), (223, 223, 194): (71, "grassland"),
    (220, 217, 57): (81, "pasture"), (171, 108, 40): (82, "cropland"),
    (184, 217, 235): (90, "woody wetland"), (108, 159, 184): (95, "herbaceous wetland"),
}


async def _quiet(coro):
    """Run one source; a failure becomes None so the rest of the panel still renders."""
    try:
        return await coro
    except (UpstreamError, KeyError, ValueError, IndexError, TypeError):
        return None


# --------------------------------------------------------------------------- soil

async def soil(lon: float, lat: float) -> dict | None:
    sql = f"""
    SELECT mu.muname, ma.wtdepannmin, c.compname, c.comppct_r, c.majcompflag, c.hydgrp, c.drainagecl,
      (SELECT TOP 1 h.ksat_r FROM chorizon h WHERE h.cokey = c.cokey ORDER BY h.hzdept_r) AS ksat_surface,
      (SELECT MIN(x.ksat_r) FROM chorizon x WHERE x.cokey = c.cokey AND x.hzdept_r < 150) AS ksat_min_150
    FROM mapunit mu JOIN muaggatt ma ON ma.mukey = mu.mukey JOIN component c ON c.mukey = mu.mukey
    WHERE mu.mukey IN (SELECT * FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('POINT({lon:.7f} {lat:.7f})'))
    ORDER BY c.comppct_r DESC"""
    data = await fetch_json(SDA, json_body={"query": sql, "format": "JSON+COLUMNNAME"}, retries=4)
    table = data.get("Table") or []
    if len(table) < 2:
        return None
    cols, rows = table[0], [dict(zip(table[0], r)) for r in table[1:]]
    urban = any((r["compname"] or "").lower() == "urban land" for r in rows)
    # Urban land has no soil properties; describe the largest real soil component instead.
    real = [r for r in rows if r.get("hydgrp")]
    main = real[0] if real else rows[0]

    def num(v):
        return float(v) if v not in (None, "") else None

    return {
        "mapunit": rows[0]["muname"],
        "component": main.get("compname"),
        "component_pct": num(main.get("comppct_r")),
        "hydgrp": main.get("hydgrp"),
        "drainage": main.get("drainagecl"),
        "ksat_um_s": num(main.get("ksat_surface")),
        "ksat_min_um_s": num(main.get("ksat_min_150")),
        "water_table_cm": num(rows[0].get("wtdepannmin")),
        "urban": urban,
    }


# --------------------------------------------------------------------------- surface

async def _nlcd_image(layer: str, bbox: tuple[float, float, float, float], n: int) -> Image.Image:
    r = await async_client().get(f"{NLCD}/mrlc_{layer}/wms", params={
        "service": "WMS", "version": "1.3.0", "request": "GetMap", "layers": layer, "styles": "",
        "crs": "EPSG:5070", "bbox": ",".join(f"{v:.0f}" for v in bbox), "width": n, "height": n,
        "format": "image/geotiff", "time": NLCD_YEAR,
    })
    if r.status_code != 200 or not r.content.startswith((b"II*\x00", b"MM\x00*")):
        raise UpstreamError("NLCD", f"HTTP {r.status_code}")
    return Image.open(io.BytesIO(r.content))


async def land(lon: float, lat: float) -> dict | None:
    x, y = albers.forward(lon, lat)
    # NLCD pixel edges sit at 15 m mod 30 m; snap so each requested pixel is one source pixel.
    x0 = math.floor((x - 15) / 30) * 30 + 15
    y0 = math.floor((y - 15) / 30) * 30 + 15
    r = LAND_RADIUS_PX
    bbox = (x0 - r * 30, y0 - r * 30, x0 + (r + 1) * 30, y0 + (r + 1) * 30)
    n = 2 * r + 1
    imp_img, lc_img = await asyncio.gather(
        _nlcd_image("Fractional-Impervious-Surface-Native_conus_year_data", bbox, n),
        _nlcd_image("Land-Cover-Native_conus_year_data", bbox, n),
        return_exceptions=True,
    )
    if isinstance(imp_img, Exception):
        return None
    imp = np.array(imp_img.convert("P") if imp_img.mode != "P" else imp_img).astype(float)
    imp = imp[imp != 250]                     # 250 = no data
    if imp.size == 0:
        return None
    cover = None
    if not isinstance(lc_img, Exception):
        px = np.array(lc_img.convert("RGB")).reshape(-1, 3)
        counts = Counter(NLCD_CLASSES.get(tuple(int(v) for v in p), (None, None))[1] for p in px)
        counts.pop(None, None)
        if counts:
            total = sum(counts.values())
            cover = ", ".join(f"{name} {round(100 * c / total)}%" for name, c in counts.most_common(2))
    return {
        "impervious_pct": round(float(imp.mean()), 1),
        "radius_m": r * 30 + 15,
        "land_cover": cover,
        "source": "Annual NLCD 2025 (USGS), 30 m",
    }


# --------------------------------------------------------------------------- aquifer + karst

def _point_query(lon: float, lat: float, fields: str, **extra) -> dict:
    return {"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": fields, "returnGeometry": "false",
            "f": "json", **extra}


async def aquifer(lon: float, lat: float) -> dict | None:
    fields = "AQ_NAME,ROCK_NAME"
    here = await fetch_json(AQUIFERS, params=_point_query(lon, lat, fields))
    feats = here.get("features") or []
    if feats:
        a = feats[0]["attributes"]
        return {"name": a["AQ_NAME"], "rock_type": a.get("ROCK_NAME"),
                "source": "USGS Principal Aquifers (shallowest principal aquifer, 1:2.5M)"}
    # Nothing mapped here ("other rocks"): name the nearest principal aquifer instead.
    near = await fetch_json(AQUIFERS, params=_point_query(
        lon, lat, fields, distance=15000, units="esriSRUnit_Meter"))
    feats = near.get("features") or []
    if not feats:
        return None
    a = feats[0]["attributes"]
    return {"name": "No principal aquifer directly beneath (minor or 'other' rocks)",
            "rock_type": f"Nearest: {a['AQ_NAME']} ({(a.get('ROCK_NAME') or '').lower()}), within 15 km",
            "source": "USGS Principal Aquifers (1:2.5M)"}


async def karst(lon: float, lat: float) -> dict:
    layers = await asyncio.gather(*[
        _quiet(fetch_json(KARST.format(name), params=_point_query(
            lon, lat, "KARST_TYPE,ROCKTYPE1,UNIT_NAME"))) for name in KARST_LAYERS
    ])
    description = None
    for name, data in zip(KARST_LAYERS, layers):
        feats = (data or {}).get("features") or []
        if feats:
            a = feats[0]["attributes"]
            rock = (a.get("ROCKTYPE1") or "").lower()
            unit = a.get("UNIT_NAME")
            description = f"{a.get('KARST_TYPE') or name.replace('_', ' ')}" + \
                (f" — {rock}" if rock else "") + (f" ({unit.title()})" if unit else "")
            break

    sinks = []
    for url, (w, s, e, n) in SINKHOLES:
        if not (w <= lon <= e and s <= lat <= n):
            continue
        data = await _quiet(fetch_json(url, params={
            "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "distance": SINKHOLE_RADIUS_M,
            "units": "esriSRUnit_Meter", "outFields": "*", "returnGeometry": "true",
            "outSR": 4326, "f": "json",
        }))
        for f in (data or {}).get("features", []):
            g = f.get("geometry") or {}
            if "x" in g:
                px, py = g["x"], g["y"]
            elif g.get("rings"):
                ring = g["rings"][0]
                px, py = sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)
            else:
                continue
            sinks.append({"lon": round(px, 6), "lat": round(py, 6),
                          "dist_m": round(geo.haversine_m((lon, lat), (px, py))), "area_m2": None})
    sinks.sort(key=lambda s: s["dist_m"])
    return {
        "in_karst": description is not None,
        "description": description,
        "sinkholes": sinks[:200],
        "source": "USGS Karst in the US (2014); state sinkhole inventories",
    }


# --------------------------------------------------------------------------- wells

async def wells(lon: float, lat: float) -> list[dict]:
    d = WELL_RADIUS_DEG
    data = await fetch_json(f"{WATERDATA}/latest-continuous/items", ttl=60 * 60 * 6, params={
        "f": "json", "parameter_code": "72019", "limit": 200,
        "bbox": f"{lon - d:.4f},{lat - d:.4f},{lon + d:.4f},{lat + d:.4f}",
    })
    cutoff = datetime.now(timezone.utc) - timedelta(days=730)
    live = {}
    for f in data.get("features", []):
        p = f["properties"]
        try:
            t = datetime.fromisoformat(p["time"].replace("Z", "+00:00"))
            value = float(p["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if t < cutoff or not f.get("geometry"):
            continue
        wx, wy = f["geometry"]["coordinates"][:2]
        mid = p["monitoring_location_id"]
        live[mid] = {"id": mid, "lon": wx, "lat": wy, "dist_km": round(geo.haversine_m((lon, lat), (wx, wy)) / 1000, 1),
                     "depth_to_water_m": round(value * FT, 2), "measured": t.date().isoformat()}
    if not live:
        return []
    nearest = sorted(live.values(), key=lambda w: w["dist_km"])[:3]
    meta = await _quiet(fetch_json(f"{WATERDATA}/monitoring-locations/items", ttl=60 * 60 * 24 * 30, params={
        "f": "json", "id": ",".join(w["id"] for w in nearest),
    }))
    info = {f["properties"].get("id") or f.get("id"): f["properties"] for f in (meta or {}).get("features", [])}
    for w in nearest:
        m = info.get(w["id"], {})
        w["name"] = (m.get("monitoring_location_name") or w["id"]).title()
        depth = m.get("well_constructed_depth") or m.get("hole_constructed_depth")
        w["well_depth_m"] = round(float(depth) * FT, 1) if depth not in (None, "") else None
        w["url"] = f"https://waterdata.usgs.gov/monitoring-location/{w['id']}/" if w["id"].startswith("USGS-") else None
    return nearest


# --------------------------------------------------------------------------- assemble

async def below(lon: float, lat: float) -> dict:
    s, l, storms, aq, k, w = await asyncio.gather(
        _quiet(soil(lon, lat)), _quiet(land(lon, lat)), _quiet(runoff.design_storms(lon, lat)),
        _quiet(aquifer(lon, lat)), _quiet(karst(lon, lat)), _quiet(wells(lon, lat)),
    )
    cases = []
    group = runoff.soil_group(s["hydgrp"]) if s else None
    if l:
        imp = l["impervious_pct"] / 100
        g = group or "C"
        events = [("Typical 1-inch storm", 1.0)]
        if storms:
            events += [(f"1-year storm ({storms['24h_in']['1']:.1f} in / 24 h)", storms["24h_in"]["1"]),
                       (f"10-year storm ({storms['24h_in']['10']:.1f} in / 24 h)", storms["24h_in"]["10"])]
        for label, p in events:
            cases.append({"label": label, **runoff.split(p, imp, g)})
    notes = []
    if l and not group:
        notes.append("Soil group unknown here; the runoff estimate assumes group C.")
    return {"soil": s, "land": l, "storms": storms, "runoff": cases, "aquifer": aq,
            "karst": k, "wells": w or [], "notes": notes}
