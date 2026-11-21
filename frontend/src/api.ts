import type {
  Basin, BelowGround, ContextCollection, FlowsData, GhostStreams, LifeData, PlaceData, RestoreData, Downstream, Geocode, LidarTrace, Raindrop, Unit,
} from "./types";

/** Where the app is published (Vite's base, e.g. "/watersheds/"). */
export const BASE = import.meta.env.BASE_URL;

const pause = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((res, rej) => {
    const t = setTimeout(res, ms);
    signal?.addEventListener("abort", () => { clearTimeout(t); rej(new DOMException("aborted", "AbortError")); }, { once: true });
  });

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const url = BASE + path;
  let r = await fetch(url, { signal });
  // Slow lookups answer 202 while they're still running; asking again picks up the same work.
  for (let i = 0; r.status === 202 && i < 60; i++) r = await fetch(url, { signal });
  if (r.status >= 502 && r.status <= 504) {
    // The USGS services hiccup now and then; one retry after a pause usually gets through.
    await pause(1500, signal);
    r = await fetch(url, { signal });
    for (let i = 0; r.status === 202 && i < 60; i++) r = await fetch(url, { signal });
  }
  if (r.status === 202) throw new Error("this lookup is taking too long; try again later");
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}

const ll = (lon: number, lat: number) => `lon=${lon.toFixed(7)}&lat=${lat.toFixed(7)}`;

export const api = {
  geocode: (q: string, s?: AbortSignal) => get<Geocode>(`api/geocode?q=${encodeURIComponent(q)}`, s),
  hierarchy: (lon: number, lat: number, s?: AbortSignal) =>
    get<{ units: Unit[] }>(`api/hierarchy?${ll(lon, lat)}`, s).then((d) => d.units),
  hrCatchment: (lon: number, lat: number, s?: AbortSignal) =>
    get<{ unit: Unit | null }>(`api/hr-catchment?${ll(lon, lat)}`, s).then((d) => d.unit),
  raindrop: (lon: number, lat: number, s?: AbortSignal) => get<Raindrop>(`api/raindrop?${ll(lon, lat)}`, s),
  downstream: (lon: number, lat: number, s?: AbortSignal) => get<Downstream>(`api/downstream?${ll(lon, lat)}`, s),
  lidar: (lon: number, lat: number, s?: AbortSignal) => get<LidarTrace>(`api/lidar-trace?${ll(lon, lat)}`, s),
  basin: (comid: number, s?: AbortSignal) => get<Basin>(`api/basin/${comid}`, s),
  below: (lon: number, lat: number, s?: AbortSignal) => get<BelowGround>(`api/below?${ll(lon, lat)}`, s),
  ghost: (lon: number, lat: number, s?: AbortSignal) => get<GhostStreams>(`api/ghost-streams?${ll(lon, lat)}`, s),
  place: (lon: number, lat: number, s?: AbortSignal) => get<PlaceData>(`api/place?${ll(lon, lat)}`, s),
  life: (lon: number, lat: number, s?: AbortSignal) => get<LifeData>(`api/life?${ll(lon, lat)}`, s),
  flows: (lon: number, lat: number, s?: AbortSignal) => get<FlowsData>(`api/flows?${ll(lon, lat)}`, s),
  restore: (lon: number, lat: number, s?: AbortSignal) => get<RestoreData>(`api/restore?${ll(lon, lat)}`, s),
  context: (level: string, bbox: number[], zoom: number, s?: AbortSignal) =>
    get<ContextCollection>(
      `api/wbd/${level}?bbox=${bbox.map((v) => v.toFixed(4)).join(",")}&zoom=${zoom.toFixed(1)}`, s),
};
