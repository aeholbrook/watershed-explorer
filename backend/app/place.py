"""Place identity: the ecological and cultural regions a point belongs to.

Sources (all keyless):
  EPA ecoregions L I–IV        geodata.epa.gov ORD/USEPA_Ecoregions_Level_III_and_IV
  One Earth bioregions         Living Atlas copy (CC-BY 4.0)
  RESOLVE Ecoregions 2017      Esri Living Atlas (biome, realm, Nature Needs Half status)
  Native Land territories      Native Land Digital's own Living Atlas layer (CC0); their API needs a key
  Land cessions (Royce)        USDA Forest Service EDW TribalCessionLands
  Plant hardiness zone 2023    PRISM/USDA PHZM shapefile, community-hosted feature layer
  Flyways                      USFWS waterfowl flyway boundaries
  Frost dates, precip normal   NOAA 1991–2020 normals, nearest station (local index, see scripts/)
  Leaf-out                     USA National Phenology Network spring index (geoserver WMS)
  Last year's precipitation    Open-Meteo ERA5 archive
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
from functools import lru_cache
from pathlib import Path

from . import geo
from .http import UpstreamError, fetch_json

EPA_ECO = "https://geodata.epa.gov/arcgis/rest/services/ORD/USEPA_Ecoregions_Level_III_and_IV/MapServer"
ONE_EARTH = "https://services7.arcgis.com/poOcx60xJtGtoR7g/arcgis/rest/services/OE_Bioregions_land/FeatureServer/0/query"
RESOLVE = "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/Resolve_Ecoregions/FeatureServer/0/query"
NATIVE_LAND = ("https://services6.arcgis.com/SC70xY1nSLm15pYn/arcgis/rest/services/"
               "Native_Land_Digital_territories/FeatureServer/0/query")
CESSIONS = "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_TribalCessionLands_01/MapServer/0/query"
HARDINESS = ("https://services1.arcgis.com/rKbpcgHXWYYaP4pQ/arcgis/rest/services/"
             "phzm_us_zones_shp_2023_view/FeatureServer/0/query")
FLYWAYS = ("https://services.arcgis.com/QVENGdaPbd4LUkLV/arcgis/rest/services/"
           "FWS_HQ_MB_Waterfowl_Flyway_Boundaries/FeatureServer/0/query")
NPN = "https://geoserver.usanpn.org/geoserver/si-x/wms"
OPEN_METEO = "https://archive-api.open-meteo.com/v1/archive"
NORMALS_INDEX = Path(__file__).resolve().parent.parent / "data" / "normals_index.json"

WEEK = 60 * 60 * 24 * 7
CESSION_PREFIX = "edw.s_usa.BdyPol_TribalCededLandsTable."


def _pt(lon: float, lat: float, fields: str, **extra) -> dict:
    return {"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": fields,
            "returnGeometry": "false", "f": "json", **extra}


def _attrs(data: dict) -> list[dict]:
    return [f["attributes"] for f in (data or {}).get("features", [])]


def _title(s: str | None) -> str | None:
    if not s:
        return s
    small = {"and", "of", "the", "in", "&"}
    acronyms = {"usa": "USA", "us": "US"}
    return " ".join(acronyms.get(w) or (w if w in small and i else w.capitalize())
                    for i, w in enumerate(s.lower().split()))


async def _quiet(coro):
    try:
        return await coro
    except (UpstreamError, KeyError, ValueError, IndexError, TypeError):
        return None


# --------------------------------------------------------------------------- ecoregions

async def ecoregions(lon: float, lat: float) -> dict | None:
    data = await fetch_json(f"{EPA_ECO}/7/query", ttl=WEEK * 52, params=_pt(
        lon, lat, "US_L4CODE,US_L4NAME,US_L3CODE,US_L3NAME,NA_L3CODE,NA_L2CODE,NA_L2NAME,NA_L1CODE,NA_L1NAME"))
    rows = _attrs(data)
    if not rows:
        return None
    a = rows[0]
    levels = [
        {"level": "I", "code": a["NA_L1CODE"], "name": _title(a["NA_L1NAME"])},
        {"level": "II", "code": a["NA_L2CODE"], "name": _title(a["NA_L2NAME"])},
        {"level": "III", "code": a["US_L3CODE"], "name": a["US_L3NAME"]},
        {"level": "IV", "code": a["US_L4CODE"], "name": a["US_L4NAME"]},
    ]
    geoms = await asyncio.gather(
        _quiet(fetch_json(f"{EPA_ECO}/11/query", ttl=WEEK * 52, params={
            "where": f"US_L3CODE='{a['US_L3CODE']}'", "outFields": "US_L3CODE", "returnGeometry": "true",
            "outSR": 4326, "maxAllowableOffset": 0.004, "geometryPrecision": 5, "f": "geojson"})),
        _quiet(fetch_json(f"{EPA_ECO}/7/query", ttl=WEEK * 52, params={
            **_pt(lon, lat, "US_L4CODE"), "returnGeometry": "true", "outSR": 4326,
            "maxAllowableOffset": 0.0008, "geometryPrecision": 5, "f": "geojson"})),
    )
    shapes = []
    for lvl, g in zip(("III", "IV"), geoms):
        for f in (g or {}).get("features", []):
            shapes.append({"type": "Feature", "geometry": f["geometry"], "properties": {"level": lvl}})
    return {"levels": levels, "shapes": {"type": "FeatureCollection", "features": shapes},
            "source": "US EPA Level I–IV ecoregions (Omernik)"}


async def bioregion(lon: float, lat: float) -> dict | None:
    oe, rs = await asyncio.gather(
        _quiet(fetch_json(ONE_EARTH, ttl=WEEK * 52, params=_pt(lon, lat, "BIOREGION_,BIOREGIO_1")))
        , _quiet(fetch_json(RESOLVE, ttl=WEEK * 52, params=_pt(
            lon, lat, "ECO_NAME,ECO_ID,BIOME_NAME,REALM,NNH_NAME"))))
    oe_rows, rs_rows = _attrs(oe), _attrs(rs)
    if not oe_rows and not rs_rows:
        return None
    out: dict = {}
    if oe_rows:
        name = oe_rows[0]["BIOREGIO_1"]
        code = oe_rows[0]["BIOREGION_"]
        out["bioregion"] = name.replace(f" ({code})", "").strip()
        out["bioregion_code"] = code
    if rs_rows:
        r = rs_rows[0]
        out.update({"ecoregion": r["ECO_NAME"], "biome": r["BIOME_NAME"], "realm": r["REALM"],
                    "conservation": r.get("NNH_NAME")})
    out["source"] = "One Earth bioregions; RESOLVE Ecoregions 2017 (CC-BY 4.0)"
    return out


# --------------------------------------------------------------------------- people

async def homelands(lon: float, lat: float) -> dict | None:
    data = await fetch_json(NATIVE_LAND, ttl=WEEK, params=_pt(lon, lat, "Name,Slug,description"))
    rows = _attrs(data)
    if not rows:
        return None
    return {
        "territories": [{"name": r["Name"], "url": r.get("description") or f"https://native-land.ca/maps/territories/{r['Slug']}"}
                        for r in rows],
        "source": "Native Land Digital (native-land.ca). Not an authoritative or legal map; "
                  "nations' own sources come first.",
    }


def _cession_date(ms) -> str | None:
    if ms in (None, ""):
        return None
    try:
        return (dt.datetime(1970, 1, 1) + dt.timedelta(milliseconds=float(ms))).date().isoformat()
    except (OverflowError, ValueError):
        return None


async def cessions(lon: float, lat: float) -> dict | None:
    f = CESSION_PREFIX
    fields = ",".join(f + k for k in ("cessnum", "cessdate1", "schdtrb", "presdaytrb",
                                      "date1link_royce_schedule", "date1link_kappler_treaty"))
    data = await fetch_json(CESSIONS, ttl=WEEK * 52, params=_pt(lon, lat, fields))
    nearby = False
    if not _attrs(data):
        # Digitizing gaps along big rivers: fall back to cessions within 5 km.
        data = await fetch_json(CESSIONS, ttl=WEEK * 52, params=_pt(
            lon, lat, fields, distance=5000, units="esriSRUnit_Meter"))
        nearby = True
    rows = _attrs(data)
    if not rows:
        return None
    out, seen = [], set()
    for r in rows:
        num = r.get(f + "cessnum")
        if num in seen:
            continue
        seen.add(num)
        out.append({
            "number": num, "date": _cession_date(r.get(f + "cessdate1")),
            "tribes": r.get(f + "schdtrb"), "present_day": r.get(f + "presdaytrb"),
            "schedule_url": r.get(f + "date1link_royce_schedule"), "treaty_url": r.get(f + "date1link_kappler_treaty"),
        })
    out.sort(key=lambda c: c["date"] or "9999")
    return {"cessions": out, "nearby": nearby,
            "source": "Royce, Indian Land Cessions in the United States (1899), digitized by USDA Forest Service"}


# --------------------------------------------------------------------------- climate & seasons

async def hardiness(lon: float, lat: float) -> dict | None:
    rows = _attrs(await fetch_json(HARDINESS, ttl=WEEK * 52, params=_pt(lon, lat, "zone,trange")))
    if not rows:
        return None
    return {"zone": rows[0]["zone"], "range_f": rows[0]["trange"], "source": "USDA Plant Hardiness Zone Map 2023"}


async def flyway(lon: float, lat: float) -> str | None:
    rows = _attrs(await fetch_json(FLYWAYS, ttl=WEEK * 52, params=_pt(lon, lat, "NAME")))
    return rows[0]["NAME"] if rows else None


@lru_cache(maxsize=1)
def _normals() -> list[dict]:
    return json.loads(NORMALS_INDEX.read_text()) if NORMALS_INDEX.exists() else []


def _nearest(lon: float, lat: float, key: str) -> tuple[dict, float] | None:
    best, best_d = None, 1e18
    k = math.cos(math.radians(lat))
    for s in _normals():
        if key not in s:
            continue
        d = (s["lat"] - lat) ** 2 + ((s["lon"] - lon) * k) ** 2
        if d < best_d:
            best, best_d = s, d
    if best is None:
        return None
    return best, geo.haversine_m((lon, lat), (best["lon"], best["lat"])) / 1000


def _md(s: str) -> str:
    m, d = s.split("/")
    return dt.date(2001, int(m), int(d)).strftime("%b %-d")


def frost(lon: float, lat: float) -> dict | None:
    hit = _nearest(lon, lat, "gsl")
    if not hit or hit[1] > 60:
        return None
    s, dist = hit
    days = int(float(s["gsl"]))
    frost_free = days >= 365
    return {
        "last_spring": None if frost_free else _md(s["lst"]),
        "first_fall": None if frost_free else _md(s["fst"]),
        "season_days": days,
        "frost_free": frost_free,
        "station": s["name"].title().replace(" Us", "").replace(" Intl Ap", " Int'l Airport"),
        "dist_km": round(dist, 1),
        "source": "NOAA 1991–2020 climate normals (32°F, 50% probability)",
    }


def precip_normal(lon: float, lat: float) -> dict | None:
    hit = _nearest(lon, lat, "prcp")
    if not hit or hit[1] > 60:
        return None
    s, dist = hit
    return {"normal_in": float(s["prcp"]), "station": s["name"].title().replace(" Us", ""), "dist_km": round(dist, 1)}


async def precip_last_year(lon: float, lat: float) -> dict | None:
    year = dt.date.today().year - 1
    data = await fetch_json(OPEN_METEO, ttl=WEEK * 52, params={
        "latitude": round(lat, 2), "longitude": round(lon, 2),
        "start_date": f"{year}-01-01", "end_date": f"{year}-12-31",
        "daily": "precipitation_sum", "precipitation_unit": "inch", "timezone": "auto",
    })
    vals = [v for v in data.get("daily", {}).get("precipitation_sum", []) if v is not None]
    if len(vals) < 300:
        return None
    return {"year": year, "total_in": round(sum(vals), 1), "source": "Open-Meteo ERA5 reanalysis"}


async def _npn(lon: float, lat: float, layer: str, prop: str) -> float | None:
    data = await fetch_json(NPN, ttl=60 * 60 * 24, params={
        "service": "WMS", "version": "1.1.1", "request": "GetFeatureInfo",
        "layers": f"si-x:{layer}", "query_layers": f"si-x:{layer}", "srs": "EPSG:4326",
        "bbox": f"{lon - 0.01},{lat - 0.01},{lon + 0.01},{lat + 0.01}",
        "width": 3, "height": 3, "x": 1, "y": 1, "info_format": "application/json",
    })
    feats = data.get("features") or []
    if not feats:
        return None
    v = feats[0]["properties"].get(prop)
    return float(v) if v is not None and float(v) > -9000 else None


async def leaf_out(lon: float, lat: float) -> dict | None:
    avg, anomaly = await asyncio.gather(
        _quiet(_npn(lon, lat, "30yr_avg_4k_leaf", "GRAY_INDEX")),
        _quiet(_npn(lon, lat, "leaf_anomaly", "LEAF_OUT_DAY_DIFF")),
    )
    if avg is None:
        return None

    def doy(d: float) -> str:
        return (dt.date(2001, 1, 1) + dt.timedelta(days=round(d) - 1)).strftime("%b %-d")

    return {"average": doy(avg), "anomaly_days": round(anomaly) if anomaly is not None else None,
            "year": dt.date.today().year,
            "source": "USA National Phenology Network spring leaf index (1991–2020 average; this year vs. average)"}


# --------------------------------------------------------------------------- assemble

async def place(lon: float, lat: float) -> dict:
    eco, bio, home, ces, hz, fly, leaf, last_year = await asyncio.gather(
        _quiet(ecoregions(lon, lat)), _quiet(bioregion(lon, lat)), _quiet(homelands(lon, lat)),
        _quiet(cessions(lon, lat)), _quiet(hardiness(lon, lat)), _quiet(flyway(lon, lat)),
        _quiet(leaf_out(lon, lat)), _quiet(precip_last_year(lon, lat)),
    )
    normal = precip_normal(lon, lat)
    return {
        "ecoregions": eco, "bioregion": bio, "homelands": home, "cessions": ces,
        "hardiness": hz, "flyway": fly, "frost": frost(lon, lat), "leaf_out": leaf,
        "precip": {"normal": normal, "last_year": last_year} if (normal or last_year) else None,
    }
