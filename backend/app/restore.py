"""Restoration: what the land was, what your roof sheds, and what you could do with it.

Sources (keyless):
  USA Structures (FEMA/ORNL)        building footprint and roof area
  NOAA 1991–2020 normals            annual precipitation (local station index, see place.py)
  WDNR rain garden manual (2018)    sizing factors, Table 1
  LANDFIRE BpS 2020 / EVT 2025      vegetation before settlement vs. now (USGS EROS geoserver)
  USGS historical topo maps         Esri Living Atlas image service (editions at the point)
  MSD Project Clear                 rainscaping grant program areas (St. Louis only)
"""

from __future__ import annotations

import asyncio
import io
import math
import os
from collections import Counter

import numpy as np
from PIL import Image
from shapely.geometry import Point, shape

from . import below, place
from .http import UpstreamError, async_client, fetch_json

STRUCTURES = ("https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/"
              "USA_Structures_View/FeatureServer/0/query")
LANDFIRE = "https://edcintl.cr.usgs.gov/geoserver/landfire/wms"
BPS_LAYER, EVT_LAYER = "landfire:LF2020_BPS_CONUS", "landfire:LF2025_EVT_CONUS"
NLCD_WMS = "https://dmsdata.cr.usgs.gov/geoserver/mrlc_Land-Cover-Native_conus_year_data/wms"
NLCD_LAYER = "Land-Cover-Native_conus_year_data"
NLCD_TIME = "2025-01-01T00:00:00.000Z"
HISTORIC_TOPO = "https://historical1.arcgis.com/arcgis/rest/services/USGS_Historical_Topographic_Maps/ImageServer"
# Esri's terms ask for an ArcGIS location-services key for this service (it answers
# anonymously, but a public site shouldn't rely on that). No key, no historic maps.
ARCGIS_KEY = os.environ.get("ARCGIS_API_KEY", "")
MSD_GRANTS = "https://services1.arcgis.com/plGX8WiX9ylnkcee/arcgis/rest/services/GI_Program_Area/FeatureServer/{}/query"
MSD_RAINSCAPING = "https://msdprojectclear.org/what-we-do/rainscaping/"

WEEK = 60 * 60 * 24 * 7
GAL_PER_FT2_IN = 0.623
ROOF_RUNOFF = 0.9                 # share of rain on a pitched roof that reaches the gutters
FT2_PER_M2 = 10.7639

# WDNR "Rain Gardens: A Guide for Homeowners and Landscapers" (PUB-WT-776, 2018), Table 1:
# garden area as a fraction of the area draining to it, for gardens < 30 ft from the downspout.
RAIN_GARDEN = {
    "sandy": {"3–5 in": 0.17, "6–7 in": 0.13, "8 in": 0.11},
    "silty": {"3–5 in": 0.35, "6–7 in": 0.30, "8 in": 0.23},
    "clayey": {"3–5 in": 0.44, "6–7 in": 0.35, "8 in": 0.25},
}
RAIN_GARDEN_FAR = {"sandy": 0.03, "silty": 0.08, "clayey": 0.10}   # > 30 ft from the downspout


async def _quiet(coro):
    try:
        return await coro
    except (UpstreamError, KeyError, ValueError, IndexError, TypeError):
        return None


# --------------------------------------------------------------------------- roof

async def roof(lon: float, lat: float) -> dict | None:
    data = await fetch_json(STRUCTURES, ttl=WEEK * 52, params={
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "distance": 40, "units": "esriSRUnit_Meter",
        "outFields": "PROP_ADDR,SQMETERS,HEIGHT,OCC_CLS,PRIM_OCC", "returnGeometry": "true",
        "outSR": 4326, "f": "geojson",
    })
    pt = Point(lon, lat)
    best, best_d = None, 1e9
    for f in data.get("features", []):
        g = shape(f["geometry"])
        d = 0.0 if g.contains(pt) else g.distance(pt) * 111_320 * math.cos(math.radians(lat))
        if d < best_d:
            best, best_d = f, d
    if not best or best_d > 35:
        return None
    p = best["properties"]
    return {"area_m2": round(p["SQMETERS"], 1), "area_ft2": round(p["SQMETERS"] * FT2_PER_M2),
            "address": p.get("PROP_ADDR"), "use": p.get("PRIM_OCC"), "height_m": p.get("HEIGHT"),
            "match": "contains" if best_d == 0 else "nearest", "distance_m": round(best_d),
            "geometry": best["geometry"], "source": "FEMA/ORNL USA Structures"}


def _soil_texture(soil: dict | None) -> tuple[str, str]:
    """WDNR soil classes by infiltration rate, from the most restrictive layer in the top 150 cm."""
    if soil and soil.get("ksat_min_um_s") is not None:
        in_hr = soil["ksat_min_um_s"] * 0.1417          # µm/s → in/hr
        basis = f"most restrictive soil layer drains ~{in_hr:.2f} in/hr"
        if in_hr > 1.0:
            return "sandy", basis
        if in_hr > 0.4:
            return "silty", basis
        return "clayey", basis
    group = ((soil or {}).get("hydgrp") or "C")[:1].upper()
    return {"A": "sandy", "B": "silty"}.get(group, "clayey"), f"hydrologic soil group {group}"


def rain_plan(roof_ft2: float, precip_in: float | None, soil: dict | None) -> dict:
    texture, basis = _soil_texture(soil)
    depth = "6–7 in" if texture != "sandy" else "3–5 in"
    factor = RAIN_GARDEN[texture][depth]
    downspout = roof_ft2 / 4                           # a typical house has four downspouts
    one_inch = roof_ft2 * GAL_PER_FT2_IN * ROOF_RUNOFF
    plan = {
        "texture": texture, "basis": basis, "depth": depth, "factor": factor,
        "garden_ft2_per_downspout": round(downspout * factor),
        "garden_ft2_far": round(downspout * RAIN_GARDEN_FAR[texture]),
        "gallons_per_inch": round(one_inch),
        "barrels_per_downspout_inch": math.ceil(one_inch / 4 / 55),
        "too_slow": soil is not None and soil.get("ksat_min_um_s") is not None and soil["ksat_min_um_s"] * 0.1417 < 0.15,
        "source": "WDNR Rain Gardens: A Guide for Homeowners and Landscapers (2018), Table 1",
    }
    if precip_in:
        plan["annual_gallons"] = round(roof_ft2 * precip_in * GAL_PER_FT2_IN * ROOF_RUNOFF)
    return plan


# --------------------------------------------------------------------------- then and now

_legends: dict[str, list[dict]] = {}


async def _legend_entries(layer: str) -> list[dict]:
    if layer not in _legends:
        data = await fetch_json(LANDFIRE, ttl=WEEK * 52, params={
            "service": "WMS", "version": "1.1.1", "request": "GetLegendGraphic", "layer": layer,
            "format": "application/json"})
        _legends[layer] = data["Legend"][0]["rules"][0]["symbolizers"][0]["Raster"]["colormap"]["entries"]
    return _legends[layer]


async def _legend(layer: str) -> dict[int, str]:
    return {int(float(e["quantity"])): e.get("label") for e in await _legend_entries(layer)}


def _mercator_bbox(lon: float, lat: float, half_m: float) -> str:
    x = math.radians(lon) * 6378137
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * 6378137
    h = half_m / math.cos(math.radians(lat))
    return f"{x - h},{y - h},{x + h},{y + h}"


async def _colour_mix(url: str, params: dict, by_color: dict, px: int = 80) -> list[dict]:
    """Class shares in a small rendered map, by looking each pixel's colour up in a legend."""
    r = await async_client().get(url, params={
        "service": "WMS", "version": "1.1.1", "request": "GetMap", "styles": "", "srs": "EPSG:3857",
        "width": px, "height": px, "format": "image/png", "transparent": "true", **params})
    if r.status_code != 200 or not r.content.startswith(b"\x89PNG"):
        return []
    pixels = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGBA")).reshape(-1, 4)
    pixels = pixels[pixels[:, 3] > 0]
    total = len(pixels) or 1
    out: dict[str, dict] = {}
    for (rr, gg, bb), n in Counter(map(tuple, pixels[:, :3].tolist())).items():
        label = by_color.get((rr, gg, bb))
        if label:
            item = out.setdefault(label, {"name": label, "color": "#%02x%02x%02x" % (rr, gg, bb), "share": 0.0})
            item["share"] += n / total
    return sorted([v for v in out.values() if v["share"] >= 0.02], key=lambda v: -v["share"])[:7]


async def before_around(lon: float, lat: float, half_m: float = 2500) -> list[dict]:
    by_color = {}
    for e in await _legend_entries(BPS_LAYER):
        c = e.get("color", "").lstrip("#").upper()
        if len(c) == 6 and e.get("label") and not e["label"].startswith("Fill-"):
            by_color.setdefault(tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)), e["label"])
    return await _colour_mix(LANDFIRE, {"layers": BPS_LAYER, "bbox": _mercator_bbox(lon, lat, half_m)}, by_color)


async def now_around(lon: float, lat: float, half_m: float = 2500) -> list[dict]:
    by_color = {rgb: name[0].upper() + name[1:] for rgb, (_, name) in below.NLCD_CLASSES.items()}
    return await _colour_mix(NLCD_WMS, {"layers": NLCD_LAYER, "time": NLCD_TIME,
                                        "bbox": _mercator_bbox(lon, lat, half_m)}, by_color)


async def _landfire_values(layer: str, lon: float, lat: float, n: int) -> list[int]:
    """Pixel values in an n x n window of 30 m cells around the point."""
    x = math.radians(lon) * 6378137
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * 6378137
    half = 30 / math.cos(math.radians(lat)) * n / 2       # 30 m cells, in mercator units
    vals = []
    for i in range(n):
        for j in range(n):
            data = await fetch_json(LANDFIRE, ttl=WEEK * 52, params={
                "service": "WMS", "version": "1.1.1", "request": "GetFeatureInfo",
                "layers": layer, "query_layers": layer, "styles": "", "srs": "EPSG:3857",
                "bbox": f"{x - half},{y - half},{x + half},{y + half}", "width": n, "height": n,
                "x": i, "y": j, "info_format": "application/json", "feature_count": 1,
            })
            for f in data.get("features", []):
                v = f["properties"].get("GRAY_INDEX")
                if v is not None:
                    vals.append(int(v))
    return vals


async def then_and_now(lon: float, lat: float) -> dict | None:
    bps_vals, bps_legend, before, now = await asyncio.gather(
        _landfire_values(BPS_LAYER, lon, lat, 1), _legend(BPS_LAYER),
        _quiet(before_around(lon, lat)), _quiet(now_around(lon, lat)))
    if not bps_vals:
        return None
    # Describe today's block from NLCD: the pin itself often sits on a road pixel.
    here = await _quiet(now_around(lon, lat, half_m=120))
    return {"before": bps_legend.get(bps_vals[0]), "now": here[0]["name"] if here else None,
            "before_around": before or [], "now_around": now or [],
            "source": "LANDFIRE Biophysical Settings 2020 (before); NLCD 2025 land cover (now)"}


async def topo_editions(lon: float, lat: float) -> list[dict]:
    if not ARCGIS_KEY:
        return []
    data = await fetch_json(f"{HISTORIC_TOPO}/query", ttl=WEEK * 52, params={
        "token": ARCGIS_KEY, "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outFields": "OBJECTID,Map_Name,Year,Map_Scale",
        "returnGeometry": "false", "orderByFields": "Year", "f": "json",
    })
    by_year: dict[tuple[int, int], dict] = {}
    for f in data.get("features", []):
        a = f["attributes"]
        if not a.get("Year") or (a.get("Map_Scale") or 0) > 62500:
            continue
        key = (int(a["Year"]), int(a["Map_Scale"]))
        e = by_year.setdefault(key, {"year": key[0], "scale": key[1], "name": a.get("Map_Name"), "ids": []})
        e["ids"].append(a["OBJECTID"])
    return sorted(by_year.values(), key=lambda e: (e["year"], e["scale"]))


async def msd_program(lon: float, lat: float) -> dict | None:
    if not (-90.8 <= lon <= -90.1 and 38.4 <= lat <= 38.95):      # MSD's service area, roughly
        return None
    counts = await asyncio.gather(*[
        _quiet(fetch_json(MSD_GRANTS.format(layer), ttl=WEEK * 4, params={
            "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "returnCountOnly": "true", "f": "json"}))
        for layer in (0, 1)])
    areas = [name for name, c in zip(("Mississippi River", "River Des Peres"), counts) if (c or {}).get("count")]
    return {"eligible_areas": areas, "url": MSD_RAINSCAPING,
            "note": "MSD Project Clear rainscaping grants (run with Missouri Botanical Garden; orientation required, "
                    "don't install before a Notice to Proceed)."}


# --------------------------------------------------------------------------- assemble

async def restore(lon: float, lat: float) -> dict:
    rf, soil, tn, eds, msd = await asyncio.gather(
        _quiet(roof(lon, lat)), _quiet(below.soil(lon, lat)), _quiet(then_and_now(lon, lat)),
        _quiet(topo_editions(lon, lat)), _quiet(msd_program(lon, lat)))
    normal = place.precip_normal(lon, lat)
    plan = rain_plan(rf["area_ft2"], normal["normal_in"] if normal else None, soil) if rf else None
    return {"roof": rf, "precip_normal_in": normal["normal_in"] if normal else None, "rain_plan": plan,
            "then_now": tn, "topo_editions": eds or [], "msd": msd,
            "overlays": {
                "historic_topo": f"{HISTORIC_TOPO}/exportImage?token={ARCGIS_KEY}" if ARCGIS_KEY else None,
                "landfire": LANDFIRE, "bps_layer": BPS_LAYER, "evt_layer": EVT_LAYER,
            }}
