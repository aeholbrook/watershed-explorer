"""Clients for the public hydrography services, shaped into what the map needs.

Sources:
  WBD      USGS Watershed Boundary Dataset (HUC2-HUC12), hydro.nationalmap.gov
  NLDI     USGS Network-Linked Data Index: raindrop trace, split catchment,
           network navigation and upstream basins, on NHDPlus V2
  WFS      NHDPlus V2 flowline value-added attributes and catchments
  HR       NHDPlus High Resolution catchments (finer than V2, but a slow service)
  Geocode  OpenStreetMap Nominatim (building-level when available), Census fallback
"""

from __future__ import annotations

import asyncio
import math
import re
from typing import Any

from shapely import make_valid
from shapely.geometry import LineString, MultiPolygon, Point, mapping, shape
from shapely.ops import substring, unary_union

from . import geo
from .http import UpstreamError, fetch_json

WBD = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer"
HR = "https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer"
NLDI = "https://api.water.usgs.gov/nldi"
WFS = "https://api.water.usgs.gov/geoserver/wmadata/ows"
CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM = "https://nominatim.openstreetmap.org/search"

# offset: generalization tolerance (degrees) for the polygon around the selected point.
# min_zoom: the lowest map zoom at which all units of that level are drawn as context.
LEVELS: list[dict[str, Any]] = [
    {"key": "huc2", "layer": 1, "label": "Region", "offset": 0.01, "min_zoom": 0, "tile_zoom": 2},
    {"key": "huc4", "layer": 2, "label": "Subregion", "offset": 0.004, "min_zoom": 3, "tile_zoom": 3},
    {"key": "huc6", "layer": 3, "label": "Basin", "offset": 0.002, "min_zoom": 4.5, "tile_zoom": 4},
    {"key": "huc8", "layer": 4, "label": "Subbasin", "offset": 0.001, "min_zoom": 5.5, "tile_zoom": 5},
    {"key": "huc10", "layer": 5, "label": "Watershed", "offset": 0.0004, "min_zoom": 7.5, "tile_zoom": 7},
    {"key": "huc12", "layer": 6, "label": "Subwatershed", "offset": 0.00015, "min_zoom": 9, "tile_zoom": 8},
]
LEVELS_BY_KEY = {lvl["key"]: lvl for lvl in LEVELS}

FT_TO_M = 0.3048
CFS_TO_CMS = 0.0283168


# --------------------------------------------------------------------------- geocoding

STRUCTURES = ("https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/"
              "USA_Structures_View/FeatureServer/0/query")


async def geocode(q: str) -> dict:
    """Address -> roof. Both free geocoders interpolate along the street (and can disagree
    by 100 m+), so when FEMA's USA Structures has a footprint carrying the same house number
    and street nearby, the pin goes on that footprint instead."""
    osm, census = await asyncio.gather(_nominatim(q), _census(q), return_exceptions=True)
    candidates = [c for c in (census, osm) if isinstance(c, dict)]
    if not candidates:
        if isinstance(osm, Exception) and isinstance(census, Exception):
            raise UpstreamError("geocoder", f"both geocoders failed ({osm}; {census})")
        raise LookupError("no match for that address")
    # A real OSM address point/building beats interpolation; otherwise the Census match.
    candidates.sort(key=lambda c: 0 if c["precision"] == "address point" else 1 if c["source"] == "census" else 2)
    best = dict(candidates[0])
    best["alternatives"] = [c for c in candidates[1:]]

    number = re.match(r"\s*(\d+[A-Za-z]?)\b", q)
    street = next((c.get("street") for c in candidates if c.get("street")), None)
    if number and street:
        try:
            roof = await _footprint(best["lon"], best["lat"], number.group(1), street)
        except UpstreamError:
            roof = None
        if roof:
            best["alternatives"].insert(0, {k: best[k] for k in ("lat", "lon", "source", "precision", "label")})
            best.update(lat=roof["lat"], lon=roof["lon"], precision="building",
                        source="FEMA USA Structures footprint", footprint=roof)
    return best


async def _footprint(lon: float, lat: float, number: str, street: str) -> dict | None:
    data = await fetch_json(STRUCTURES, params={
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "distance": 250, "units": "esriSRUnit_Meter",
        "where": f"PROP_ADDR LIKE '{number.upper()} %'", "outFields": "PROP_ADDR,SQMETERS,HEIGHT",
        "returnGeometry": "true", "outSR": 4326, "f": "geojson",
    })
    token = street.upper().split()[0]
    hits = [f for f in data.get("features", [])
            if token in (f["properties"].get("PROP_ADDR") or "").upper().split()]
    if not hits:
        return None
    # A house plus a detached garage share an address: take the larger footprint.
    f = max(hits, key=lambda f: f["properties"].get("SQMETERS") or 0)
    g = shape(f["geometry"])
    c = g.representative_point()
    return {"lon": round(c.x, 7), "lat": round(c.y, 7), "area_m2": round(f["properties"]["SQMETERS"], 1),
            "height_m": f["properties"].get("HEIGHT"), "address": f["properties"]["PROP_ADDR"],
            "geometry": geo.round_coords(f["geometry"], 7)}


async def _nominatim(q: str) -> dict | None:
    data = await fetch_json(NOMINATIM, params={
        "q": q, "format": "jsonv2", "countrycodes": "us", "limit": 1, "addressdetails": 1,
    })
    if not data:
        return None
    hit = data[0]
    # Nominatim labels TIGER street-range interpolations "place/house" on the street's way;
    # only a node or a building is an actual mapped address.
    real = hit.get("osm_type") == "node" or hit.get("category") == "building"
    precise = int(hit.get("place_rank", 0)) >= 30
    return {
        "lat": float(hit["lat"]), "lon": float(hit["lon"]),
        "label": hit.get("display_name", q),
        "source": "openstreetmap",
        "precision": "address point" if precise and real else "street-interpolated" if precise else hit.get("addresstype", "area"),
        "street": (hit.get("address") or {}).get("road"),
    }


async def _census(q: str) -> dict | None:
    data = await fetch_json(CENSUS, params={"address": q, "benchmark": "Public_AR_Current", "format": "json"})
    matches = data.get("result", {}).get("addressMatches", [])
    if not matches:
        return None
    m = matches[0]
    return {
        "lat": m["coordinates"]["y"], "lon": m["coordinates"]["x"],
        "label": m["matchedAddress"].title(),
        "source": "census",
        "precision": "street-interpolated",
        "street": m.get("addressComponents", {}).get("streetName"),
    }


# --------------------------------------------------------------------------- watershed hierarchy

async def wbd_unit_at(level: dict, lon: float, lat: float) -> dict | None:
    key = level["key"]
    data = await fetch_json(f"{WBD}/{level['layer']}/query", params={
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outFields": f"{key},name,areasqkm,states",
        "returnGeometry": "true", "maxAllowableOffset": level["offset"], "outSR": 4326,
        "f": "geojson",
    })
    feats = data.get("features") or []
    if not feats:
        return None
    f = feats[0]
    p = f["properties"]
    return {
        "level": key, "label": level["label"], "code": p[key], "name": p.get("name"),
        "areasqkm": p.get("areasqkm"), "states": p.get("states"),
        "source": "USGS Watershed Boundary Dataset",
        "geometry": geo.round_coords(f["geometry"], 5),
    }


async def comid_at(lon: float, lat: float) -> int:
    data = await fetch_json(f"{NLDI}/linked-data/comid/position",
                            params={"f": "json", "coords": f"POINT({lon} {lat})"})
    feats = data.get("features") or []
    if not feats:
        raise UpstreamError("NLDI", "no NHDPlus catchment at this point (outside the US network?)")
    return int(feats[0]["properties"]["comid"])


async def flowline_attrs(comids: list[int], fields: list[str] | None = None) -> dict[int, dict]:
    """NHDPlus V2 value-added attributes for many flowlines, without geometry."""
    fields = fields or ["comid", "gnis_name", "lengthkm", "hydroseq", "streamorde", "maxelevsmo",
                        "minelevsmo", "ve_ma", "vc_ma", "qe_ma", "pathtimema", "totdasqkm",
                        "terminalfl", "terminalpa", "ftype", "reachcode"]
    out: dict[int, dict] = {}
    chunks = [comids[i:i + 400] for i in range(0, len(comids), 400)]
    results = await asyncio.gather(*[
        fetch_json(WFS, form={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "wmadata:nhdflowline_network", "outputFormat": "application/json",
            "propertyName": ",".join(fields),
            "cql_filter": "comid IN (%s)" % ",".join(map(str, chunk)),
        }) for chunk in chunks
    ])
    for data in results:
        for f in data.get("features", []):
            out[int(f["properties"]["comid"])] = f["properties"]
    return out


def _clean_name(name: str | None) -> str | None:
    name = (name or "").strip()
    # NLDI's raindrop trace reports an unnamed flowline as the string "none".
    return None if name.lower() in ("", "none", "null") else name


def entry_name(entry: dict, route: dict) -> str | None:
    """The stream the raindrop enters; an unnamed one is described by the first named
    stream it flows into ("an unnamed tributary of Sugar Creek")."""
    if entry.get("stream"):
        return entry["stream"]
    for s in route.get("segments", []):
        name = s["name"]
        if not s.get("gap") and name and name != "Unnamed tributary":
            the = "the " if name.endswith(" River") and not name.startswith("The ") else ""
            return f"an unnamed tributary of {the}{name}"
    return None


async def nhdplus_catchment(lon: float, lat: float) -> dict | None:
    comid = await comid_at(lon, lat)
    cat, attrs = await asyncio.gather(
        fetch_json(WFS, params={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "wmadata:catchmentsp", "outputFormat": "application/json",
            "srsName": "EPSG:4326", "cql_filter": f"featureid={comid}",
        }),
        flowline_attrs([comid]),
    )
    feats = cat.get("features") or []
    if not feats:
        return None
    a = attrs.get(comid, {})
    stream = _clean_name(a.get("gnis_name"))
    return {
        "level": "catchment", "label": "Stream catchment", "code": str(comid),
        "name": f"{stream} catchment" if stream else "Unnamed stream catchment",
        "areasqkm": feats[0]["properties"].get("areasqkm"),
        "source": "NHDPlus V2 catchment (COMID %d)" % comid,
        "geometry": geo.simplify(feats[0]["geometry"], 0.00005),
    }


# Region-scale outlines are generalized (up to 0.01° ≈ 1 km) to keep them small, so near
# the site they're rebuilt from detailed HUC12s: HUC codes nest (HUC12 071401010401 is
# inside HUC2 07), so a unit's exact local shape is the union of the HUC12s carrying its
# code as a prefix.
LOCAL_DX, LOCAL_DY = 0.15, 0.12      # half-size of the detailed window, degrees
LOCAL_OFFSET = 0.00008


async def _local_huc12s(lon: float, lat: float) -> list[tuple[str, Any]]:
    data = await fetch_json(f"{WBD}/6/query", params={
        "geometry": f"{lon - LOCAL_DX:.4f},{lat - LOCAL_DY:.4f},{lon + LOCAL_DX:.4f},{lat + LOCAL_DY:.4f}",
        "geometryType": "esriGeometryEnvelope", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
        "outFields": "huc12", "returnGeometry": "true", "maxAllowableOffset": LOCAL_OFFSET,
        "outSR": 4326, "f": "geojson", "resultRecordCount": 500,
    })
    return [(f["properties"]["huc12"], make_valid(shape(f["geometry"]))) for f in data.get("features", [])]


def _refine(units: list[dict], huc12s: list[tuple[str, Any]]) -> None:
    if not huc12s:
        return
    # Inside this zone every point belongs to a fetched HUC12, so boundaries there are exact.
    trusted = unary_union([g for _, g in huc12s]).buffer(-0.0001)
    for u in units:
        if not u["level"].startswith("huc"):
            continue
        local = unary_union([g for code, g in huc12s if code.startswith(u["code"])])
        if local.is_empty:
            continue
        coarse = make_valid(shape(u["geometry"]))
        refined = coarse.difference(trusted).union(local.intersection(trusted))
        refined = refined.simplify(LOCAL_OFFSET / 2, preserve_topology=True)
        polys = [g for g in getattr(refined, "geoms", [refined]) if g.geom_type == "Polygon" and g.area > 1e-7]
        if polys:
            u["geometry"] = geo.round_coords(mapping(MultiPolygon(polys) if len(polys) > 1 else polys[0]), 5)


async def hierarchy(lon: float, lat: float) -> list[dict]:
    *units, huc12s = await asyncio.gather(
        *[wbd_unit_at(lvl, lon, lat) for lvl in LEVELS],
        nhdplus_catchment(lon, lat),
        _local_huc12s(lon, lat),
        return_exceptions=True,
    )
    out = []
    for u in units:
        if isinstance(u, Exception):
            if isinstance(u, UpstreamError):
                continue
            raise u
        if u:
            out.append(u)
    if not isinstance(huc12s, Exception):
        _refine(out, huc12s)
    for u in out:
        u["bbox"] = geo.bbox(u["geometry"])
        u["label_at"] = geo.label_point(u["geometry"])
    return out


async def hr_catchment(lon: float, lat: float) -> dict | None:
    """NHDPlus HR catchment: roughly 10x finer than V2, but the service can take ~45 s cold."""
    data = await fetch_json(f"{HR}/10/query", params={
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outFields": "nhdplusid,areasqkm",
        "returnGeometry": "true", "outSR": 4326, "maxAllowableOffset": 0.00003, "f": "geojson",
    })
    feats = data.get("features") or []
    if not feats:
        return None
    f = feats[0]
    nid = int(f["properties"]["nhdplusid"])
    name = None
    try:
        fl = await fetch_json(f"{HR}/3/query", params={
            "where": f"nhdplusid={nid}", "outFields": "gnis_name,streamorde",
            "returnGeometry": "false", "f": "json",
        })
        if fl.get("features"):
            name = _clean_name(fl["features"][0]["attributes"].get("gnis_name"))
    except UpstreamError:
        pass
    geometry = geo.round_coords(f["geometry"], 6)
    return {
        "level": "hr_catchment", "label": "Fine catchment", "code": str(nid),
        "name": f"{name} fine catchment" if name else "Unnamed-tributary catchment",
        "areasqkm": f["properties"].get("areasqkm"),
        "source": "NHDPlus High Resolution catchment",
        "geometry": geometry, "bbox": geo.bbox(geometry), "label_at": geo.label_point(geometry),
    }


# --------------------------------------------------------------------------- raindrop + split catchment

def _process_inputs(**kw) -> dict:
    return {"inputs": [{"id": k, "type": "text/plain", "value": str(v).lower() if isinstance(v, bool) else str(v)}
                       for k, v in kw.items()]}


# Above this drainage area, "everything upstream of where your water enters" is a
# continental basin (slow to delineate, and not what this level is for); show only the
# local part of the catchment instead. The full basin is one click away on the route.
FULL_UPSTREAM_MAX_KM2 = 1500


async def flowtrace(lon: float, lat: float) -> dict:
    """NLDI raindrop trace: overland path on the 30 m grid and where it meets a stream."""
    trace = await fetch_json(f"{NLDI}/pygeoapi/processes/nldi-flowtrace/execution",
                             json_body=_process_inputs(lat=lat, lon=lon, direction="down"))
    feats = {f.get("id"): f for f in trace.get("features", [])}
    if "downstreamFlowline" not in feats:
        raise UpstreamError("NLDI", "raindrop trace did not reach a stream")
    dfl = feats["downstreamFlowline"]
    props = dfl["properties"]
    entry_lon, entry_lat = props["intersection_point"]
    path = feats.get("raindropPath", {}).get("geometry") or {
        "type": "LineString", "coordinates": [[lon, lat], [entry_lon, entry_lat]]}
    return {
        "path": path,
        "path_m": props.get("raindrop_pathDist") or geo.line_length_m(path["coordinates"]),
        "entry": {
            "lon": entry_lon, "lat": entry_lat, "comid": int(props["comid"]),
            "stream": _clean_name(props.get("gnis_name")), "reachcode": props.get("reachcode"),
            "measure": props.get("measure"),
        },
        "entry_flowline": dfl["geometry"],
    }


async def _flowline(comid: int) -> dict | None:
    data = await fetch_json(WFS, form={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "wmadata:nhdflowline_network", "outputFormat": "application/json",
        "srsName": "EPSG:4326", "propertyName": "comid,gnis_name,reachcode,terminalpa,minelevsmo,the_geom",
        "cql_filter": f"comid={comid}",
    })
    feats = data.get("features") or []
    return feats[0] if feats else None


def _nearest_on(geometry: dict, lon: float, lat: float) -> tuple[list[float], float, dict]:
    """The point of a flowline nearest lon/lat, its NHDPlus measure (percent of the reach still
    to go; flowlines are digitized downstream) and the part of the line below it."""
    line = LineString(_coords(geometry))
    d = line.project(Point(lon, lat))
    at = line.interpolate(d)
    below = substring(line, d, line.length)
    if below.geom_type != "LineString":   # entry at the very end of the reach
        below = LineString([at, at])
    measure = 100 * (1 - d / line.length) if line.length else 0.0
    return [round(at.x, 6), round(at.y, 6)], measure, mapping(below)


async def entry_point(lon: float, lat: float) -> dict:
    """Where the raindrop meets the stream network: NLDI's raindrop trace when it works.

    The trace fails for whole areas (in St. Louis: Forest Park, the zoo, the riverfront), so
    then the route starts from the stream of the NHDPlus catchment the point is in, reached
    in a straight line."""
    try:
        return await flowtrace(lon, lat)
    except UpstreamError as e:
        failure = e
    try:
        comid = await comid_at(lon, lat)
        fl = await _flowline(comid)
    except UpstreamError:
        raise failure from None
    if not fl:
        raise failure
    props = fl["properties"]
    note = ("USGS's raindrop trace has no answer for this spot, so the route starts at the stream of "
            "the catchment it's in, joined by a straight line.")
    hop = await _bridge(props, [lon, lat]) if await _dead_end(props) else None
    if hop:
        # This catchment's stream goes underground and never reaches the sea on the map, so
        # skip it and join the nearest lower stream that does.
        comid, at, measure, below = hop["comid"], hop["at"], hop["measure"], hop["entry_line"]
        props = (await flowline_attrs([comid], ["comid", "gnis_name", "reachcode"])).get(comid, {})
        note = ("The stream here runs underground (in a sewer or culvert USGS doesn't map), so the "
                "route joins the nearest lower stream that reaches the sea, in a straight line.")
    else:
        at, measure, below = _nearest_on(fl["geometry"], lon, lat)
    return {
        "path": {"type": "LineString", "coordinates": [[lon, lat], at]},
        "path_m": geo.haversine_m((lon, lat), at),
        "entry": {
            "lon": at[0], "lat": at[1], "comid": comid, "stream": _clean_name(props.get("gnis_name")),
            "reachcode": props.get("reachcode"), "measure": measure,
        },
        "entry_flowline": below,
        "approximate": note,
    }


async def raindrop(lon: float, lat: float) -> dict:
    """Flow trace plus the area that drains to the same stream entry point."""
    ft = await entry_point(lon, lat)
    entry = ft["entry"]
    attrs = (await flowline_attrs([entry["comid"]], ["comid", "totdasqkm"])).get(entry["comid"], {})
    full = (attrs.get("totdasqkm") or 0) <= FULL_UPSTREAM_MAX_KM2

    split = await fetch_json(f"{NLDI}/pygeoapi/processes/nldi-splitcatchment/execution",
                             json_body=_process_inputs(lat=entry["lat"], lon=entry["lon"],
                                                       upstream=full, simplified=True))
    sfeats = {f.get("id"): f for f in split.get("features", [])}
    basin = sfeats.get("drainageBasin") or sfeats.get("splitCatchment")
    drains = None
    if basin:
        g = geo.simplify(basin["geometry"], 0.00003)
        stream = entry["stream"] or "the stream"
        drains = {
            "level": "drains_to_entry", "label": "Drains to your entry point",
            "code": "",
            "name": f"Upstream of your entry into {stream}" if full
                    else f"Local area draining to your entry into {stream}",
            "areasqkm": round(geo.polygon_area_km2(g), 3),
            "source": "NLDI split catchment at the raindrop's stream entry point"
                      + ("" if full else " (local catchment only; the river's full basin is on the route)"),
            "geometry": g, "bbox": geo.bbox(g), "label_at": geo.label_point(g),
        }
    return {**ft, "drains_to_entry": drains}


# --------------------------------------------------------------------------- downstream route

def _outlet(last: dict, end: list[float]) -> dict:
    lon, lat = end[0], end[1]
    elev = (last.get("minelevsmo") or 0) / 100
    if elev < 3 and lat < 31 and -98 < lon < -80:
        name, kind = "Gulf of Mexico", "sea"
    elif elev < 3 and lon < -114:
        name, kind = "Pacific Ocean", "sea"
    elif elev < 3 and lon > -82:
        name, kind = "Atlantic Ocean", "sea"
    elif elev < 3:
        name, kind = "the sea", "sea"
    else:
        name, kind = "the end of the mapped network", "terminal"
    return {"name": name, "kind": kind, "lon": lon, "lat": lat, "elev_m": elev}


def _velocity_ms(a: dict) -> float:
    for k in ("ve_ma", "vc_ma"):
        v = a.get(k)
        if v is not None and v > 0:
            return v * FT_TO_M
    return 0.3 + 0.08 * (a.get("streamorde") or 1)


def _coords(geometry: dict) -> list[list[float]]:
    if geometry["type"] == "LineString":
        return geometry["coordinates"]
    return [c for part in geometry["coordinates"] for c in part]


async def _route(comid: int, measure: float | None, entry_line: dict | None) -> dict:
    nav = await fetch_json(f"{NLDI}/linked-data/comid/{comid}/navigation/DM/flowlines",
                           params={"distance": 9999, "f": "json"})
    geoms = {int(f["properties"]["nhdplus_comid"]): f["geometry"] for f in nav.get("features", [])}
    if not geoms:
        raise UpstreamError("NLDI", "no downstream flowlines")
    attrs = await flowline_attrs(list(geoms))
    ids = sorted(geoms, key=lambda c: -(attrs.get(c, {}).get("hydroseq") or 0))

    reaches, segments, profile = [], [], []
    km = 0.0
    hours = 0.0
    for i, c in enumerate(ids):
        a = attrs.get(c, {})
        length = a.get("lengthkm") or geo.line_length_m(_coords(geoms[c])) / 1000
        up = (a.get("maxelevsmo") or 0) / 100
        down = (a.get("minelevsmo") or 0) / 100
        coords = _coords(geoms[c])
        if i == 0 and measure is not None:
            frac = max(0.0, min(1.0, measure / 100))
            length *= frac
            up = down + (up - down) * frac
            if entry_line:
                coords = _coords(entry_line)
        name = _clean_name(a.get("gnis_name"))
        if not segments or (name and name != segments[-1]["name"]):
            if name is None and not segments:
                name = "Unnamed tributary"
            segments.append({"name": name, "start_km": km, "start_hours": hours, "reach_ids": [],
                             "start_elev_m": up, "max_order": 0})
        seg = segments[-1]
        v = _velocity_ms(a)
        dt = length * 1000 / v / 3600
        seg["reach_ids"].append(c)
        seg["max_order"] = max(seg["max_order"], a.get("streamorde") or 0)
        seg["end_comid"] = c
        seg["end_da_km2"] = a.get("totdasqkm")
        seg["end_flow_cms"] = (a.get("qe_ma") or 0) * CFS_TO_CMS
        seg["end_elev_m"] = down
        profile.append([round(km, 3), round(up, 2), coords[0][0], coords[0][1]])
        reaches.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[round(x, 6), round(y, 6)] for x, y, *_ in coords]},
            "properties": {
                "comid": c, "name": seg["name"], "seg": len(segments) - 1,
                "order": a.get("streamorde") or 1, "start_km": round(km, 3),
                "length_km": round(length, 3), "hours": round(hours, 2),
                "flow_cms": round((a.get("qe_ma") or 0) * CFS_TO_CMS, 2),
                "da_km2": a.get("totdasqkm"),
            },
        })
        km += length
        hours += dt
        seg["end_km"] = km
        seg["end_hours"] = hours

    last = attrs.get(ids[-1], {})
    end = _coords(geoms[ids[-1]])[-1]
    profile.append([round(km, 3), round((last.get("minelevsmo") or 0) / 100, 2), end[0], end[1]])
    for s in segments:
        s["length_km"] = round(s["end_km"] - s["start_km"], 3)
        s["hours"] = round(s["end_hours"] - s["start_hours"], 2)
        s["drop_m"] = round(s["start_elev_m"] - s["end_elev_m"], 1)
        s["reach_count"] = len(s.pop("reach_ids"))
        for k in ("start_km", "end_km", "start_hours", "end_hours", "end_flow_cms"):
            s[k] = round(s[k], 3)
    return {
        "reaches": {"type": "FeatureCollection", "features": reaches},
        "segments": segments,
        "profile": profile,
        "outlet": _outlet(last, end),
        "totals": {"length_km": round(km, 2), "hours": round(hours, 1), "reach_count": len(ids)},
        "_last": last,
    }


# Some networks dead-end where a stream goes underground (St. Louis' River Des Peres
# disappears into a sewer tunnel under Forest Park). The route then jumps, in a straight
# line, to the nearest lower flowline of a network that does reach the sea, and says so.
GAP_SEARCH_DEG = (0.02, 0.05, 0.1)     # ~2, 5 and 10 km
PIPE_SPEED_MS = 1.0                     # assumed for the unmapped stretch
MAX_HOPS = 3


async def _dead_end(props: dict) -> bool:
    """Does this flowline's network end above sea level (a stream that goes underground)?"""
    if not props.get("terminalpa"):
        return False
    data = await fetch_json(WFS, form={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "wmadata:nhdflowline_network", "outputFormat": "application/json",
        "propertyName": "hydroseq,minelevsmo", "cql_filter": f"hydroseq={int(props['terminalpa'])}",
    })
    feats = data.get("features") or []
    return bool(feats) and (feats[0]["properties"].get("minelevsmo") or 0) >= 300


async def _bridge(last: dict, end: list[float]) -> dict | None:
    fields = "comid,terminalpa,maxelevsmo,the_geom"
    for d in GAP_SEARCH_DEG:
        data = await fetch_json(WFS, form={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "wmadata:nhdflowline_network", "outputFormat": "application/json",
            "srsName": "EPSG:4326", "propertyName": fields,
            "bbox": f"{end[1] - d},{end[0] - d},{end[1] + d},{end[0] + d},urn:ogc:def:crs:EPSG::4326",
        })
        feats = [f for f in data.get("features", [])
                 if f["properties"].get("terminalpa") and f["properties"]["terminalpa"] != last.get("terminalpa")
                 and (f["properties"].get("maxelevsmo") or 0) <= (last.get("minelevsmo") or 0)]
        if not feats:
            continue
        # Only networks whose own outlet is at sea level.
        paths = sorted({int(f["properties"]["terminalpa"]) for f in feats})
        outs = await fetch_json(WFS, form={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "wmadata:nhdflowline_network", "outputFormat": "application/json",
            "propertyName": "hydroseq,minelevsmo",
            "cql_filter": "hydroseq IN (%s)" % ",".join(map(str, paths)),
        })
        sea = {int(o["properties"]["hydroseq"]) for o in outs.get("features", [])
               if o["properties"].get("minelevsmo") is not None and o["properties"]["minelevsmo"] < 300}
        feats = [f for f in feats if int(f["properties"]["terminalpa"]) in sea]
        if feats:
            here = Point(end)
            best = min(feats, key=lambda f: shape(f["geometry"]).distance(here))
            at, measure, below = _nearest_on(best["geometry"], end[0], end[1])
            return {"comid": int(best["properties"]["comid"]), "measure": measure, "entry_line": below, "at": at}
    return None


def _join(route: dict, at: list[float], nxt: dict) -> dict:
    """route, then a straight unmapped stretch to `at`, then nxt."""
    start = [route["outlet"]["lon"], route["outlet"]["lat"]]
    gap_km = geo.haversine_m(start, at) / 1000
    gap_h = gap_km * 1000 / PIPE_SPEED_MS / 3600
    km0, h0 = route["totals"]["length_km"], route["totals"]["hours"]
    top = route["outlet"]["elev_m"]
    bottom = nxt["profile"][0][1] if nxt["profile"] else top
    seg_i = len(route["segments"])
    gap_seg = {
        "name": "Underground (not in the USGS stream map)", "gap": True,
        "start_km": round(km0, 3), "end_km": round(km0 + gap_km, 3), "length_km": round(gap_km, 3),
        "start_hours": round(h0, 3), "end_hours": round(h0 + gap_h, 3), "hours": round(gap_h, 2),
        "start_elev_m": top, "end_elev_m": bottom, "drop_m": round(top - bottom, 1),
        "max_order": 0, "reach_count": 0, "end_comid": None, "end_da_km2": None, "end_flow_cms": 0,
    }
    gap_reach = {
        "type": "Feature", "geometry": {"type": "LineString", "coordinates": [start, at]},
        "properties": {"comid": 0, "name": gap_seg["name"], "seg": seg_i, "order": 1, "gap": True,
                       "start_km": round(km0, 3), "length_km": round(gap_km, 3), "hours": round(h0, 2),
                       "flow_cms": 0, "da_km2": None},
    }
    off_km, off_h = km0 + gap_km, h0 + gap_h
    reaches = []
    for f in nxt["reaches"]["features"]:
        pr = f["properties"]
        reaches.append({**f, "properties": {**pr, "seg": pr["seg"] + seg_i + 1,
                                            "start_km": round(pr["start_km"] + off_km, 3),
                                            "hours": round(pr["hours"] + off_h, 2)}})
    segments = [{**s, "start_km": round(s["start_km"] + off_km, 3), "end_km": round(s["end_km"] + off_km, 3),
                 "start_hours": round(s["start_hours"] + off_h, 3), "end_hours": round(s["end_hours"] + off_h, 3)}
                for s in nxt["segments"]]
    t = nxt["totals"]
    return {
        "reaches": {"type": "FeatureCollection",
                    "features": route["reaches"]["features"] + [gap_reach] + reaches},
        "segments": route["segments"] + [gap_seg] + segments,
        "profile": route["profile"] + [[round(p[0] + off_km, 3), *p[1:]] for p in nxt["profile"]],
        "outlet": nxt["outlet"],
        "totals": {"length_km": round(off_km + t["length_km"], 2), "hours": round(off_h + t["hours"], 1),
                   "reach_count": route["totals"]["reach_count"] + t["reach_count"]},
        "_last": nxt["_last"],
    }


async def downstream(comid: int, measure: float | None, entry_line: dict | None) -> dict:
    route = await _route(comid, measure, entry_line)
    for _ in range(MAX_HOPS):
        if route["outlet"]["kind"] != "terminal":
            break
        hop = await _bridge(route["_last"], [route["outlet"]["lon"], route["outlet"]["lat"]])
        if hop is None:
            break
        route = _join(route, hop["at"], await _route(hop["comid"], hop["measure"], hop["entry_line"]))
    route.pop("_last")
    return route


# --------------------------------------------------------------------------- upstream basin

async def basin(comid: int) -> dict:
    data, attrs = await asyncio.gather(
        fetch_json(f"{NLDI}/linked-data/comid/{comid}/basin",
                   params={"f": "json", "simplified": "true", "splitCatchment": "false"}),
        flowline_attrs([comid]),
    )
    feats = data.get("features") or []
    if not feats:
        raise UpstreamError("NLDI", "no basin for that reach")
    a = attrs.get(comid, {})
    g = feats[0]["geometry"]
    # Continental basins come back with tens of thousands of vertices; the map doesn't need them.
    area = a.get("totdasqkm") or geo.polygon_area_km2(g)
    tol = 0.00005 if area < 500 else 0.0005 if area < 50_000 else 0.003
    g = geo.simplify(g, tol)
    return {
        "comid": comid, "name": _clean_name(a.get("gnis_name")), "areasqkm": area,
        "geometry": g, "bbox": geo.bbox(g),
    }


# --------------------------------------------------------------------------- context layers

def _tiles(bbox: list[float], z: int) -> list[tuple[int, int, int]]:
    def tx(lon):
        return int((lon + 180) / 360 * 2 ** z)

    def ty(lat):
        lat = max(-85.0, min(85.0, lat))
        r = math.radians(lat)
        return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2 ** z)

    w, s, e, n = bbox
    n_max = 2 ** z - 1
    xs = range(max(0, tx(w)), min(n_max, tx(e)) + 1)
    ys = range(max(0, ty(n)), min(n_max, ty(s)) + 1)
    return [(z, x, y) for x in xs for y in ys]


def _tile_bbox(z: int, x: int, y: int) -> list[float]:
    def lat(yy):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / 2 ** z))))

    return [x / 2 ** z * 360 - 180, lat(y + 1), (x + 1) / 2 ** z * 360 - 180, lat(y)]


async def wbd_units_in_view(level_key: str, bbox: list[float], zoom: float) -> dict:
    level = LEVELS_BY_KEY[level_key]
    if zoom < level["min_zoom"]:
        return {"type": "FeatureCollection", "features": [], "too_far_out": True,
                "min_zoom": level["min_zoom"]}
    # Fetch on a fixed tile grid so repeated pans hit the cache; tolerance follows tile size.
    tz = max(level["tile_zoom"], min(int(zoom) - 1, 11))
    tiles = _tiles(bbox, tz)[:40]
    tol = 360 / 2 ** tz / 1024
    key = level["key"]

    async def one(t):
        w, s, e, n = _tile_bbox(*t)
        return await fetch_json(f"{WBD}/{level['layer']}/query", params={
            "geometry": f"{w:.6f},{s:.6f},{e:.6f},{n:.6f}", "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
            "outFields": f"{key},name,areasqkm", "returnGeometry": "true",
            "maxAllowableOffset": f"{tol:.6f}", "outSR": 4326, "f": "geojson",
            "resultRecordCount": 2000,
        })

    results = await asyncio.gather(*[one(t) for t in tiles], return_exceptions=True)
    seen: dict[str, dict] = {}
    for r in results:
        if isinstance(r, Exception):
            continue
        for f in r.get("features", []):
            code = f["properties"].get(key)
            if code and code not in seen:
                seen[code] = {
                    "type": "Feature", "id": int(code),
                    "geometry": geo.round_coords(f["geometry"], 5),
                    "properties": {"code": code, "name": f["properties"].get("name"),
                                   "areasqkm": f["properties"].get("areasqkm"), "level": key,
                                   "label_at": geo.label_point(f["geometry"])},
                }
    return {"type": "FeatureCollection", "features": list(seen.values())}
