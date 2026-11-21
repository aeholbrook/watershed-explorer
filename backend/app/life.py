"""Living things: who else lives in this place, from iNaturalist research-grade observations.

iNaturalist asks for <= 60 requests/minute; everything here is cached for a week and
fetched through a small semaphore. Photo licences vary, so every photo carries its
attribution string.
"""

from __future__ import annotations

import asyncio

from .http import UpstreamError, fetch_json

INAT = "https://api.inaturalist.org/v1"
WEEK = 60 * 60 * 24 * 7
_inat = asyncio.Semaphore(3)


async def _get(path: str, **params) -> dict:
    async with _inat:
        return await fetch_json(f"{INAT}/{path}", params={**params, "locale": "en"}, ttl=WEEK)


def _species(data: dict, limit: int) -> list[dict]:
    out = []
    for r in (data or {}).get("results", [])[:limit]:
        t = r.get("taxon") or {}
        photo = t.get("default_photo") or {}
        out.append({
            "id": t.get("id"),
            "name": t.get("name"),
            "common": t.get("preferred_common_name"),
            "count": r.get("count"),
            "photo": photo.get("square_url"),
            "attribution": photo.get("attribution"),
            "url": f"https://www.inaturalist.org/taxa/{t.get('id')}" if t.get("id") else None,
        })
    return out


async def county_place(lon: float, lat: float) -> dict | None:
    data = await _get("places/nearby", nelat=lat, nelng=lon, swlat=lat, swlng=lon, per_page=50)
    for p in (data.get("results") or {}).get("standard", []):
        if p.get("admin_level") == 20:
            return {"id": p["id"], "name": p.get("display_name") or p.get("name")}
    return None


async def _counts(place_id: int, limit: int = 8, **filters) -> list[dict]:
    data = await _get("observations/species_counts", place_id=place_id, quality_grade="research",
                      per_page=max(limit, 30), **filters)
    return _species(data, limit)


async def birds_by_season(place_id: int) -> dict:
    """Birds seen in both January and June are year-round; June-only are summer visitors."""
    jan, jun = await asyncio.gather(
        _get("observations/species_counts", place_id=place_id, quality_grade="research",
             iconic_taxa="Aves", month=1, per_page=60),
        _get("observations/species_counts", place_id=place_id, quality_grade="research",
             iconic_taxa="Aves", month=6, per_page=60),
    )
    winter = {r["taxon"]["id"]: r for r in jan.get("results", [])}
    summer = {r["taxon"]["id"]: r for r in jun.get("results", [])}
    both = [r for tid, r in summer.items() if tid in winter]
    summer_only = [r for tid, r in summer.items() if tid not in winter]
    winter_only = [r for tid, r in winter.items() if tid not in summer]
    return {
        "year_round": _species({"results": both}, 6),
        "summer": _species({"results": summer_only}, 6),
        "winter": _species({"results": winter_only}, 6),
    }


async def fish_along(points: list[tuple[str, float, float]]) -> list[dict]:
    async def one(name, lon, lat):
        data = await _get("observations/species_counts", lat=round(lat, 3), lng=round(lon, 3), radius=10,
                          quality_grade="research", iconic_taxa="Actinopterygii", per_page=10)
        return {"segment": name, "lon": lon, "lat": lat, "species": _species(data, 4)}

    results = await asyncio.gather(*[one(*p) for p in points], return_exceptions=True)
    return [r for r in results if isinstance(r, dict) and r["species"]]


def route_samples(downstream: dict, max_points: int = 6) -> list[tuple[str, float, float]]:
    """One point per named segment, plus extra points spread along a very long river."""
    out = []
    feats = downstream["reaches"]["features"]
    for i, seg in enumerate(downstream["segments"]):
        reaches = [f for f in feats if f["properties"]["seg"] == i]
        if not reaches:
            continue
        n = 1 if seg["length_km"] < 300 else min(4, max_points - len(out))
        for k in range(max(1, n)):
            f = reaches[min(len(reaches) - 1, int((k + 0.5) / max(1, n) * len(reaches)))]
            c = f["geometry"]["coordinates"][len(f["geometry"]["coordinates"]) // 2]
            label = seg["name"] if n == 1 else f"{seg['name']}, km {round(f['properties']['start_km'])}"
            out.append((label, c[0], c[1]))
        if len(out) >= max_points:
            break
    return out


async def life(lon: float, lat: float, downstream: dict | None) -> dict:
    county = await county_place(lon, lat)
    if not county:
        raise UpstreamError("iNaturalist", "no county place found for this point")
    pid = county["id"]

    async def quiet(coro):
        try:
            return await coro
        except (UpstreamError, KeyError, TypeError, ValueError):
            return None

    native, introduced, early, birds, fish = await asyncio.gather(
        quiet(_counts(pid, 12, iconic_taxa="Plantae", native="true")),
        quiet(_counts(pid, 8, iconic_taxa="Plantae", introduced="true")),
        # Plant phenology annotation: flowering (term 12, value 13), February–March.
        quiet(_counts(pid, 8, iconic_taxa="Plantae", native="true", term_id=12, term_value_id=13, month="2,3")),
        quiet(birds_by_season(pid)),
        quiet(fish_along(route_samples(downstream))) if downstream else asyncio.sleep(0, result=None),
    )
    return {
        "county": county,
        "native_plants": native or [],
        "introduced_plants": introduced or [],
        "early_bloomers": early or [],
        "birds": birds,
        "fish": fish or [],
        "source": "iNaturalist research-grade observations (CC-licensed; photo credits shown)",
    }

