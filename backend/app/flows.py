"""Flows and responsibility: what leaves your watershed, who shares it, where your water comes from and goes.

Sources (keyless):
  SPARROW Midwest 2012          local table built from ScienceBase (scripts/build_sparrow_index.py)
  Census 2020 blocks, places     TIGERweb
  Water system service areas     EPA Community Water System boundaries + SDWIS (Envirofacts)
  Sewersheds                     EPA CWNS 2022 national sewersheds; MO DNR combined-sewer areas
  Impaired waters                EPA ATTAINS geospatial service
  Monitoring sites               Water Quality Portal
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from functools import lru_cache
from pathlib import Path

from shapely.geometry import MultiLineString, Point, shape

from . import usgs
from .http import UpstreamError, async_client, fetch_json

TIGER = "https://tigerweb.geo.census.gov/arcgis/rest/services/Census2020"
WATER_SYSTEMS = "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/Water_System_Boundaries/FeatureServer/0/query"
SDWIS = "https://data.epa.gov/efservice"
SEWERSHEDS = ("https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/"
              "National_Sewershed_Web_Map_Internal_WFL1/FeatureServer/{}/query")
CSO_MO = "https://gis.dnr.mo.gov/host/rest/services/waste/CSO_Areas/MapServer/0/query"
ATTAINS = "https://gispub.epa.gov/arcgis/rest/services/OW/ATTAINS_Assessment/MapServer"
WQP = "https://www.waterqualitydata.us/data/Station/search"

WEEK = 60 * 60 * 24 * 7

ACRONYMS = {"WWTP", "WTP", "STP", "POTW", "PWS", "MSD", "WRF", "WRRF", "MO", "IL", "US", "USA"}


def _name(s: str | None) -> str | None:
    """Federal tables are all caps; title-case them but keep abbreviations like WWTP."""
    if not s:
        return s
    return " ".join(w if w in ACRONYMS else w.capitalize() for w in " ".join(s.split()).upper().split())


def _pt(lon: float, lat: float, fields: str, **extra) -> dict:
    return {"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": fields,
            "returnGeometry": "false", "f": "json", **extra}


def _esri_polygon(geometry: dict) -> str:
    polys = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    rings = [ring for poly in polys for ring in poly]
    return json.dumps({"rings": rings, "spatialReference": {"wkid": 4326}})


async def _quiet(coro):
    try:
        return await coro
    except (UpstreamError, KeyError, ValueError, IndexError, TypeError):
        return None


# --------------------------------------------------------------------------- nutrients

# Built by scripts/build_sparrow_index.py (too big for git); the container keeps it on a volume.
SPARROW_DB = Path(os.environ.get("SPARROW_DB") or Path(__file__).resolve().parent.parent / "data" / "sparrow_midwest.sqlite")
SOURCE_NAMES = {"wwtp": "wastewater plants", "urb": "urban runoff", "fert": "farm fertilizer", "man": "manure",
                "nfix": "nitrogen-fixing crops", "atm": "air deposition", "can": "Canada",
                "geo": "rocks and soils", "geol": "rocks and soils", "chan": "stream channels"}


@lru_cache(maxsize=1)
def _sparrow_db() -> tuple[sqlite3.Connection, dict[str, list[str]]] | None:
    if not SPARROW_DB.exists():
        return None
    db = sqlite3.connect(f"file:{SPARROW_DB}?mode=ro", uri=True, check_same_thread=False)
    names = {k.split("_")[0]: v.split(",") for k, v in db.execute("SELECT key, value FROM meta WHERE key LIKE '%_sources'")}
    return db, names


def _sparrow(comid: int, nutrient: str) -> dict | None:
    got = _sparrow_db()
    if not got:
        return None
    db, names = got
    row = db.execute("SELECT inc_area, cum_area, inc_load, acc_load, del_frac, inc_mix, acc_mix FROM sparrow "
                     "WHERE comid = ? AND nutrient = ?", (comid, nutrient)).fetchone()
    if not row:
        return None
    inc_area, cum_area, inc_load, acc_load, del_frac, inc_mix, acc_mix = row

    def shares(mix: str) -> list[dict]:
        out = [{"source": SOURCE_NAMES.get(k, k), "share": int(v) / 1000} for k, v in zip(names[nutrient], mix.split(","))]
        return sorted([x for x in out if x["share"] >= 0.005], key=lambda x: -x["share"])

    return {
        "comid": str(comid), "stream": None, "area_km2": inc_area, "upstream_km2": cum_area,
        "local_yield_kg_km2": inc_load / inc_area if inc_area else 0, "local_load_kg": inc_load,
        "local_sources": shares(inc_mix), "river_load_kg": acc_load, "river_sources": shares(acc_mix),
        "delivered_frac": del_frac, "target": None, "concentration_mg_l": None,
    }


def nutrients(comid: int | None, river: tuple[int, str] | None, outlet: str | None) -> dict | None:
    if comid is None:
        return None
    tn = _sparrow(comid, "tn")
    if not tn:
        return None
    tp = _sparrow(comid, "tp")
    tn_river = tp_river = None
    if river:
        tn_river, tp_river = _sparrow(river[0], "tn"), _sparrow(river[0], "tp")
        for r in (tn_river, tp_river):
            if r:
                r["stream"] = river[1]
    for r in (tn, tp, tn_river, tp_river):
        if r:
            r["target"] = outlet or "outlet"
    return {"nitrogen": tn, "phosphorus": tp, "river_nitrogen": tn_river, "river_phosphorus": tp_river,
            "model": "USGS SPARROW Midwest 2012 (Robertson & Saad 2019)"}


# --------------------------------------------------------------------------- people in the watershed

async def neighbors(huc12: dict) -> dict | None:
    geom = _esri_polygon(huc12["geometry"])
    poly = shape(huc12["geometry"])
    blocks, places = await asyncio.gather(
        fetch_json(f"{TIGER}/Tracts_Blocks/MapServer/2/query", ttl=WEEK * 52, form={
            "geometry": geom, "geometryType": "esriGeometryPolygon", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": "POP100,INTPTLON,INTPTLAT",
            "returnGeometry": "false", "f": "json",
        }),
        fetch_json(f"{TIGER}/Places_CouSub_ConCity_SubMCD/MapServer/4/query", ttl=WEEK * 52, form={
            "geometry": geom, "geometryType": "esriGeometryPolygon", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": "NAME,GEOID",
            "returnGeometry": "true", "outSR": 4326, "maxAllowableOffset": 0.0005, "f": "geojson",
        }),
    )
    # Count a block when its internal point is inside the watershed (edge blocks split fairly).
    pop = 0
    for f in blocks.get("features", []):
        a = f["attributes"]
        try:
            if poly.contains(Point(float(a["INTPTLON"]), float(a["INTPTLAT"]))):
                pop += int(a["POP100"] or 0)
        except (TypeError, ValueError):
            continue
    munis = []
    for f in places.get("features", []):
        try:
            g = shape(f["geometry"])
            share = g.intersection(poly).area / g.area if g.area else 0
        except Exception:  # invalid generalized geometry: keep it, unknown share
            share = 1.0
        if share >= 0.02:
            munis.append({"name": f["properties"]["NAME"], "share_inside": round(share, 2)})
    munis.sort(key=lambda m: -m["share_inside"])
    return {"population": pop, "municipalities": munis, "huc12": huc12["code"], "huc12_name": huc12["name"],
            "area_km2": huc12.get("areasqkm"), "source": "US Census 2020 blocks and places (TIGERweb)"}


# --------------------------------------------------------------------------- water in, water out

async def tap_water(lon: float, lat: float) -> dict | None:
    rows = [f["attributes"] for f in (await fetch_json(WATER_SYSTEMS, ttl=WEEK * 52, params=_pt(
        lon, lat, "PWSID,PWS_Name,Population_Served_Count"))).get("features", [])]
    if not rows:
        return None
    sys_ = rows[0]
    pwsid = sys_["PWSID"]
    info, facilities = await asyncio.gather(
        _quiet(fetch_json(f"{SDWIS}/WATER_SYSTEM/PWSID/{pwsid}/JSON", ttl=WEEK * 52)),
        _quiet(fetch_json(f"{SDWIS}/WATER_SYSTEM_FACILITY/PWSID/{pwsid}/IS_SOURCE_IND/Y/JSON", ttl=WEEK * 52)),
    )
    source_type = None
    if info:
        code = (info[0].get("gw_sw_code") or info[0].get("primary_source_code") or "").upper()
        source_type = {"SW": "surface water", "GW": "groundwater", "GU": "groundwater under the influence of surface water",
                       "SWP": "purchased surface water", "GWP": "purchased groundwater"}.get(code, code or None)
    primary, emergency = [], []
    for f in facilities or []:
        if (f.get("facility_activity_code") or "A") != "A":
            continue
        kind = {"IN": "intake", "WL": "well", "RS": "reservoir", "SP": "spring", "CC": "connection"}.get(
            f.get("facility_type_code"), None)
        if not kind:
            continue
        name = _name(f.get("facility_name")) or ""
        bucket = emergency if f.get("availability_code") == "E" else primary
        if name and all(i["name"] != name for i in bucket):
            bucket.append({"name": name, "kind": kind})
    return {"pwsid": pwsid, "name": _name(sys_["PWS_Name"]), "population": sys_.get("Population_Served_Count"),
            "source_type": source_type, "primary_sources": primary[:8], "emergency_sources": emergency[:8],
            "source": "EPA Community Water System boundaries; SDWIS"}


async def wastewater(lon: float, lat: float) -> dict | None:
    rows = [f["attributes"] for f in (await fetch_json(SEWERSHEDS.format(0), ttl=WEEK * 52, params=_pt(
        lon, lat, "CWNS_ID,FACILITY_NAME,NPDES_ID,RESIDENTIAL_POP_2022,ECHO_URL"))).get("features", [])]
    if not rows:
        return None
    s = rows[0]
    plant = None
    ends = await _quiet(fetch_json(SEWERSHEDS.format(1), ttl=WEEK * 52, params={
        "where": f"CWNS_ID='{s['CWNS_ID']}'", "outFields": "*", "returnGeometry": "true", "outSR": 4326, "f": "json"}))
    for f in (ends or {}).get("features", []):
        g = f.get("geometry") or {}
        if "x" in g:
            plant = {"lon": g["x"], "lat": g["y"]}
            break
    combined = None
    if -95.8 <= lon <= -89.1 and 35.9 <= lat <= 40.7:
        cso = await _quiet(fetch_json(CSO_MO, ttl=WEEK * 52, params=_pt(lon, lat, "CA_NAME")))
        if cso is not None:
            names = [f["attributes"].get("CA_NAME") for f in cso.get("features", [])]
            combined = {"combined": bool(names), "area": names[0] if names else None}
    return {"facility": _name(s["FACILITY_NAME"]), "npdes": s.get("NPDES_ID"),
            "population": s.get("RESIDENTIAL_POP_2022"), "echo_url": s.get("ECHO_URL"),
            "plant": plant, "combined_sewer": combined,
            "source": "EPA Clean Watersheds Needs Survey 2022 sewersheds" + ("; MO DNR combined-sewer areas" if combined else "")}


# --------------------------------------------------------------------------- water quality

async def impaired(huc12: dict, entry: dict | None, route=None) -> dict | None:
    fields = "assessmentunitidentifier,assessmentunitname,ircategory,overallstatus,isimpaired,reportingcycle"
    geom = _esri_polygon(huc12["geometry"])
    calls = [fetch_json(f"{ATTAINS}/1/query", ttl=WEEK * 4, form={
        "geometry": geom, "geometryType": "esriGeometryPolygon", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outFields": fields,
        "returnGeometry": "true", "outSR": 4326, "maxAllowableOffset": 0.0002, "f": "geojson"})]
    if entry:
        calls.append(fetch_json(f"{ATTAINS}/1/query", ttl=WEEK * 4, params={
            **_pt(entry["lon"], entry["lat"], fields), "distance": 1500, "units": "esriSRUnit_Meter",
            "returnGeometry": "true", "outSR": 4326, "maxAllowableOffset": 0.0002, "f": "geojson"}))
    results = await asyncio.gather(*calls, return_exceptions=True)
    units: dict[str, dict] = {}
    shapes = []
    for r in results:
        if isinstance(r, Exception):
            continue
        for f in r.get("features", []):
            p = f["properties"]
            uid = p["assessmentunitidentifier"]
            shapes.append({"type": "Feature", "geometry": f["geometry"],
                           "properties": {"id": uid, "impaired": p.get("isimpaired") == "Y"}})
            u = units.setdefault(uid, {"id": uid, "name": p.get("assessmentunitname"), "status": p.get("overallstatus"),
                                       "impaired": p.get("isimpaired") == "Y", "category": p.get("ircategory"),
                                       "cycle": p.get("reportingcycle"), "causes": [], "on_route": False})
            # On your water's route if the assessed segment runs within ~150 m of it.
            if route is not None and not u["on_route"]:
                try:
                    u["on_route"] = shape(f["geometry"]).distance(route) < 0.0015
                except Exception:
                    pass
    if not units:
        return {"units": [], "shapes": {"type": "FeatureCollection", "features": []}, "source": "EPA ATTAINS"}
    ids = ",".join(f"'{u}'" for u in units)
    causes = await _quiet(fetch_json(f"{ATTAINS}/8/query", ttl=WEEK * 4, params={
        "where": f"assessmentunitidentifier IN ({ids}) AND parameterstatus='Cause'",
        "outFields": "assessmentunitidentifier,parametername", "returnGeometry": "false", "f": "json"}))
    for f in (causes or {}).get("features", []):
        a = f["attributes"]
        u = units.get(a["assessmentunitidentifier"])
        name = (a.get("parametername") or "").capitalize()
        if u and name and name not in u["causes"]:
            u["causes"].append(name)
    ordered = sorted(units.values(), key=lambda u: (not u["on_route"], not u["impaired"], u["name"] or ""))
    return {"units": ordered, "shapes": {"type": "FeatureCollection", "features": shapes},
            "source": "EPA ATTAINS (state water-quality assessments)"}


async def monitoring(huc12_code: str) -> dict | None:
    r = await async_client().head(WQP, params={"huc": huc12_code, "mimeType": "csv"})
    if r.status_code != 200:
        return None

    def n(h):
        try:
            return int(r.headers.get(h, "0"))
        except ValueError:
            return 0

    return {"total": n("Total-Site-Count"), "usgs": n("NWIS-Site-Count"), "other": n("STORET-Site-Count"),
            "url": f"https://www.waterqualitydata.us/#huc={huc12_code}&mimeType=csv&providers=NWIS&providers=STORET"}


# --------------------------------------------------------------------------- assemble

async def flows(lon: float, lat: float) -> dict:
    units = await usgs.hierarchy(lon, lat)
    huc12 = next((u for u in units if u["level"] == "huc12"), None)
    ft = await _quiet(usgs.flowtrace(lon, lat))
    entry = ft["entry"] if ft else None
    # Where your water joins the big river: the last reach of the first segment with a
    # large drainage area, so the river's nutrient load and sources can be shown.
    route = None
    river = None
    outlet = None
    if ft:
        ds = await _quiet(usgs.downstream(entry["comid"], entry.get("measure"), ft["entry_flowline"]))
        if ds:
            outlet = ds["outlet"]["name"]
            # The first 60 km of the route, for matching assessed waters to it.
            near = [f["geometry"]["coordinates"] for f in ds["reaches"]["features"]
                    if f["properties"]["start_km"] < 60]
            if near:
                route = MultiLineString(near)
            # Where your water joins a big river: show that river's load and sources too.
            big = next((f for f in ds["reaches"]["features"] if (f["properties"].get("da_km2") or 0) > 50_000), None)
            if big:
                river = (big["properties"]["comid"], big["properties"]["name"])
    comid = await _quiet(usgs.comid_at(lon, lat))
    nut = nutrients(comid, river, outlet)
    nb, tap, sewer, imp, wqp = await asyncio.gather(
        _quiet(neighbors(huc12)) if huc12 else asyncio.sleep(0),
        _quiet(tap_water(lon, lat)),
        _quiet(wastewater(lon, lat)),
        _quiet(impaired(huc12, entry, route)) if huc12 else asyncio.sleep(0),
        _quiet(monitoring(huc12["code"])) if huc12 else asyncio.sleep(0),
    )
    return {"nutrients": nut, "neighbors": nb, "tap_water": tap, "wastewater": sewer,
            "impaired": imp, "monitoring": wqp}



