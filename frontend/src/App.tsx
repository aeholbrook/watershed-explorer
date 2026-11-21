import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Map as MapLibreMap, LngLatBoundsLike } from "maplibre-gl";
import { api } from "./api";
import { ok, useLoad } from "./useLoad";
import { KOPPEN_PERIODS, SCENARIOS, periodPath, type KoppenClass, type ScenarioId } from "./map/koppen";
import { area, duration, elev, flow, km, metres } from "./format";
import { MapView, type ContextStatus } from "./map/MapView";
import { cumulativeKm, ride, type RideFrame, type RidePlan } from "./map/ride";
import { CSS, LEVEL_NAME, LEVEL_ORDER } from "./palette";
import { useTheme } from "./theme";
import { Profile, type ProfileMark, type ProfilePoint } from "./components/Profile";
import { BelowGround } from "./components/BelowGround";
import { PlaceTab } from "./components/PlaceTab";
import { LifeTab } from "./components/LifeTab";
import { FlowsTab } from "./components/FlowsTab";
import { RestoreTab, type Overlay } from "./components/RestoreTab";
import type {
  Basemap, Basin, BelowGround as BelowData, Downstream, FlowsData, Geocode, GhostStreams, LifeData, PlaceData, RestoreData, LevelKey, LidarTrace, LngLat, Raindrop, Unit,
} from "./types";

const EXAMPLE = "Missouri Botanical Garden, St. Louis, MO";
// Overland pace for the time estimate: sheet flow, gutters and storm pipes, roughly 0.5 m/s.
const OVERLAND_MS = 0.5;
const EXPLORABLE: LevelKey[] = ["huc2", "huc4", "huc6", "huc8", "huc10", "huc12"];
type Tab = "water" | "place" | "life" | "flows" | "restore";
const TABS: [Tab, string][] = [["water", "Water"], ["place", "Place"], ["life", "Life"], ["flows", "Flows"], ["restore", "Restore"]];

function readHash(): LngLat | null {
  const m = location.hash.match(/^#(-?\d+\.\d+),(-?\d+\.\d+)/);
  return m ? { lat: parseFloat(m[1]), lon: parseFloat(m[2]) } : null;
}

export default function App() {
  const [site, setSite] = useState<LngLat | null>(readHash);
  const [place, setPlace] = useState<Geocode | null>(null);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [selected, setSelected] = useState<LevelKey | null>(null);
  const [hovered, setHovered] = useState<LevelKey | null>(null);
  const [explore, setExplore] = useState<LevelKey | null>(null);
  const [view, setView] = useState<"2d" | "3d">("2d");
  const [basemap, setBasemap] = useState<Basemap>("map");
  const [themePref, setThemePref, mode] = useTheme();
  const [exaggeration, setExaggeration] = useState(3);
  const [wbdOverlay, setWbdOverlay] = useState(false);
  const [focusSeg, setFocusSeg] = useState<number | null>(null);
  const [basinComid, setBasinComid] = useState<number | null>(null);
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const [ctxStatus, setCtxStatus] = useState<ContextStatus>({ loading: false, count: 0, tooFarOut: false });
  const [rideFrame, setRideFrame] = useState<RideFrame | null>(null);
  const [tab, setTab] = useState<Tab>("water");
  const [overlay, setOverlay] = useState<Overlay>(null);
  const [overlayOpacity, setOverlayOpacity] = useState(0.8);
  const [climateOn, setClimateOn] = useState(false);
  const [periodIndex, setPeriodIndex] = useState(3);
  const [scenario, setScenario] = useState<ScenarioId>("ssp245");
  const [climateView, setClimateView] = useState<(KoppenClass & { share: number })[]>([]);
  const koppenPath = climateOn ? periodPath(periodIndex, scenario) : null;
  const stopRide = useRef<(() => void) | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);

  const key = site ? `${site.lon.toFixed(7)},${site.lat.toFixed(7)}` : null;
  const hier = useLoad(key, (s) => api.hierarchy(site!.lon, site!.lat, s));
  const hr = useLoad(key, (s) => api.hrCatchment(site!.lon, site!.lat, s));
  const drop = useLoad<Raindrop>(key, (s) => api.raindrop(site!.lon, site!.lat, s));
  const ds = useLoad<Downstream>(key, (s) => api.downstream(site!.lon, site!.lat, s));
  const lidar = useLoad<LidarTrace>(key, (s) => api.lidar(site!.lon, site!.lat, s));
  const below = useLoad<BelowData>(key, (s) => api.below(site!.lon, site!.lat, s));
  const ghost = useLoad<GhostStreams>(key, (s) => api.ghost(site!.lon, site!.lat, s));
  const placeData = useLoad<PlaceData>(key, (s) => api.place(site!.lon, site!.lat, s));
  const lifeData = useLoad<LifeData>(key, (s) => api.life(site!.lon, site!.lat, s));
  const flowsData = useLoad<FlowsData>(key, (s) => api.flows(site!.lon, site!.lat, s));
  const restoreData = useLoad<RestoreData>(key, (s) => api.restore(site!.lon, site!.lat, s));
  const basin = useLoad<Basin>(basinComid != null ? String(basinComid) : null, (s) => api.basin(basinComid!, s));

  // ------------------------------------------------------------------ site changes
  const fit = useCallback((bbox: number[], maxZoom = 16) => {
    mapRef.current?.fitBounds(bbox as LngLatBoundsLike, {
      padding: { top: 60, bottom: 60, left: 60, right: 60 }, maxZoom, duration: 1400,
      pitch: mapRef.current.getPitch(),
    });
  }, []);

  const pick = useCallback((p: LngLat, label?: Geocode) => {
    stopRide.current?.();
    setSite(p);
    setPlace(label ?? null);
    setSelected(null);
    setFocusSeg(null);
    setBasinComid(null);
    history.replaceState(null, "", `#${p.lat.toFixed(6)},${p.lon.toFixed(6)}`);
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (site && map && !place) map.flyTo({ center: [site.lon, site.lat], zoom: Math.max(map.getZoom(), 14.5), duration: 1200 });
    // place-driven moves are handled by search()
  }, [site?.lon, site?.lat]); // eslint-disable-line react-hooks/exhaustive-deps

  const search = async (q: string) => {
    if (q.trim().length < 3) return;
    setSearching(true);
    setSearchError(null);
    try {
      const g = await api.geocode(q.trim());
      pick({ lon: g.lon, lat: g.lat }, g);
      mapRef.current?.flyTo({ center: [g.lon, g.lat], zoom: 15.5, duration: 1600 });
    } catch (e) {
      setSearchError(String((e as Error).message));
    } finally {
      setSearching(false);
    }
  };

  // ------------------------------------------------------------------ derived data
  const units: Unit[] = useMemo(() => {
    const out = [...(ok(hier) ?? [])];
    const h = ok(hr);
    if (h) out.push(h);
    const d = ok(drop)?.drains_to_entry;
    if (d) out.push(d);
    return out.sort((a, b) => LEVEL_ORDER.indexOf(a.level) - LEVEL_ORDER.indexOf(b.level));
  }, [hier, hr, drop]);

  const selectLevel = (u: Unit) => {
    setSelected(u.level);
    setBasinComid(null);
    if ((EXPLORABLE as string[]).includes(u.level)) setExplore(u.level);
    fit(u.bbox);
  };

  const L = ok(lidar);
  const D = ok(ds);
  const R = ok(drop);

  // Overland: prefer the 1 m lidar trace, fall back to NLDI's 30 m raindrop path.
  const overland = useMemo(() => {
    if (L) return { km: L.length_m / 1000, coords: L.path.coordinates as [number, number][], source: "lidar" as const };
    if (R) return { km: R.path_m / 1000, coords: R.path.coordinates as [number, number][], source: "nldi" as const };
    return null;
  }, [L, R]);
  const overlandHours = overland ? (overland.km * 1000) / OVERLAND_MS / 3600 : 0;

  const profile = useMemo(() => {
    const pts: ProfilePoint[] = [];
    const marks: ProfileMark[] = [];
    if (!D) return { pts, marks };
    const off = overland?.km ?? 0;
    if (L) {
      for (const [m, z, lon, lat] of L.profile) pts.push({ km: m / 1000, elev: z, lon, lat, segment: "Overland (1 m lidar)" });
    } else if (overland) {
      pts.push({ km: 0, elev: D.profile[0][1], lon: overland.coords[0][0], lat: overland.coords[0][1], segment: "Overland" });
    }
    for (const [k, z, lon, lat] of D.profile) {
      const seg = D.segments.find((s) => k >= s.start_km - 1e-9 && k <= s.end_km + 1e-9) ?? D.segments[D.segments.length - 1];
      pts.push({ km: off + k, elev: z, lon, lat, segment: seg.name });
    }
    for (const s of D.segments) marks.push({ km: off + s.start_km, label: s.name });
    marks.push({ km: off + D.totals.length_km, label: D.outlet.name });
    return { pts, marks };
  }, [D, L, overland]);

  const totals = useMemo(() => {
    if (!D) return null;
    const startElev = L?.start_elev_m ?? D.profile[0][1];
    return {
      km: (overland?.km ?? 0) + D.totals.length_km,
      hours: overlandHours + D.totals.hours,
      drop: startElev - D.outlet.elev_m,
    };
  }, [D, L, overland, overlandHours]);

  // ------------------------------------------------------------------ ride
  const startRide = () => {
    const map = mapRef.current;
    if (!map || !D || !site) return;
    stopRide.current?.();
    const coords: [number, number][] = [[site.lon, site.lat]];
    if (overland) coords.push(...overland.coords);
    for (const f of D.reaches.features) coords.push(...(f.geometry.coordinates as [number, number][]));
    const kms = cumulativeKm(coords);
    const overlandKm = overland ? kms[overland.coords.length] : 0;
    const plan: RidePlan = {
      coords, km: kms, overlandKm,
      stops: [
        { km: 0, name: overland?.source === "lidar" ? "Overland — yards, gutters, swales" : "Overland", hours: 0 },
        ...D.segments.map((s) => ({ km: overlandKm + s.start_km, name: s.name, hours: overlandHours + s.start_hours })),
        { km: kms[kms.length - 1], name: D.outlet.name, hours: overlandHours + D.totals.hours },
      ],
      profile: profile.pts.map((p) => [p.km, p.elev]),
      overlandHours,
    };
    setSelected(null);
    setExplore(null);
    stopRide.current = ride(map, plan, view === "3d", (f) => {
      setRideFrame(f);
      if (f.done) stopRide.current = null;
    });
  };
  const endRide = () => {
    stopRide.current?.();
    stopRide.current = null;
    setRideFrame(null);
  };
  useEffect(() => () => stopRide.current?.(), []);

  const showBasin = (comid: number, segIndex: number | null) => {
    setFocusSeg(segIndex);
    setBasinComid(comid);
    setSelected(null);
  };
  useEffect(() => {
    const b = ok(basin);
    if (b) fit(b.bbox, 14);
  }, [basin, fit]);

  const riding = rideFrame != null && !rideFrame.done;
  const busy = Object.entries({ hier, hr, drop, ds, lidar, basin, below, ghost, placeData, lifeData, flowsData, restoreData })
    .filter(([, l]) => l.state === "loading").map(([k]) => k).join(" ");

  // ------------------------------------------------------------------ render
  return (
    <div className="app" data-busy={busy || undefined}>
      <aside className="panel">
        <header>
          <h1>Where does your rain go?</h1>
          <p className="lede">Trace a raindrop from any roof in the US to the sea, and see every watershed it belongs to.</p>
          <form className="search" onSubmit={(e) => { e.preventDefault(); search(query); }}>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Street address, city, state"
              aria-label="Address"
            />
            <button type="submit" disabled={searching}>{searching ? "Finding…" : "Trace"}</button>
          </form>
          {searchError && <p className="error">Couldn't find that address ({searchError}).</p>}
          {!site && (
            <p className="hint">
              Or click anywhere on the map. Try{" "}
              <button className="link" onClick={() => { setQuery(EXAMPLE); search(EXAMPLE); }}>{EXAMPLE}</button>.
            </p>
          )}
          {site && (
            <p className="hint">
              {place ? (
                <>
                  <strong>{place.label.split(",").slice(0, 3).join(",")}</strong>
                  {" · "}
                  {place.precision === "building" ? "pin on its building footprint (FEMA USA Structures)"
                    : place.precision === "street-interpolated" ? "position estimated along the street; drag the pin onto the roof"
                      : place.precision}
                  <br />
                </>
              ) : null}
              Drag the pin onto your roof, or click elsewhere to drop a new raindrop.
            </p>
          )}
        </header>

        {site && totals && (
          <section className="stats" aria-label="Summary">
            <div className="stat"><div className="stat-label">Distance to {D!.outlet.kind === "sea" ? "the sea" : "the end"}</div><div className="stat-value">{km(totals.km)}</div></div>
            <div className="stat"><div className="stat-label">Travel time, average flow</div><div className="stat-value">{duration(totals.hours)}</div></div>
            <div className="stat"><div className="stat-label">Elevation drop</div><div className="stat-value">{elev(totals.drop)}</div></div>
          </section>
        )}

        {site && (
          <nav className="tabs" role="tablist" aria-label="Sections">
            {TABS.map(([k, label]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
                {label}
              </button>
            ))}
          </nav>
        )}

        {tab === "water" && (
          <>
          {site && (
            <section>
              <h2>Your watersheds</h2>
              <p className="section-note">From the continental region down to the patch of ground that drains to the same spot as your roof. Click a level to see its neighbors.</p>
              {hier.state === "loading" && <p className="loading">Looking up watershed boundaries…</p>}
              {hier.state === "error" && <p className="error">{hier.error}</p>}
              <ol className="levels">
                {units.map((u) => (
                  <li key={u.level}>
                    <button
                      className={`level ${selected === u.level ? "on" : ""}`}
                      onClick={() => selectLevel(u)}
                      onMouseEnter={() => setHovered(u.level)}
                      onMouseLeave={() => setHovered(null)}
                      onFocus={() => setHovered(u.level)}
                      onBlur={() => setHovered(null)}
                    >
                      <span className="swatch" style={{ borderColor: CSS.level(u.level) }} />
                      <span className="level-text">
                        <span className="level-kind">{LEVEL_NAME[u.level]}</span>
                        <span className="level-name">{u.name ?? "Unnamed"}</span>
                        <span className="level-meta">
                          {u.code && /^\d+$/.test(u.code) && u.level.startsWith("huc") ? `HUC ${u.code} · ` : ""}
                          {area(u.areasqkm)}
                          {u.states ? ` · ${u.states.replace(/,/g, ", ")}` : ""}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
                {hr.state === "loading" && hier.state === "ok" && (
                  <li className="loading small">Fetching the NHDPlus HR fine catchment (this service can take ~45 s)…</li>
                )}
                {drop.state === "loading" && hier.state === "ok" && <li className="loading small">Delineating what drains to your stream entry point…</li>}
              </ol>
            </section>
          )}

          {site && (
            <section>
              <h2>Where it flows</h2>
              {(ds.state === "loading" || drop.state === "loading") && <p className="loading">Tracing the raindrop downstream…</p>}
              {ds.state === "error" && <p className="error">{ds.error}</p>}
              {D && (
                <>
                  <ol className="route">
                    <li className="route-step">
                      <span className="key key-overland" style={{ background: CSS.overland }} />
                      <div>
                        <div className="route-name">Over the ground to {D.entry.stream ?? "the nearest stream"}</div>
                        <div className="route-meta">
                          {lidar.state === "loading" && <span className="loading">Tracing with 1 m lidar… (a new area takes ~30 s to download){R ? ` · 30 m estimate ${metres(R.path_m)}` : ""}</span>}
                          {L && <>{metres(L.length_m)} · {elev(L.start_elev_m)} → {elev(L.end_elev_m)} · about {duration(overlandHours)} {endReason(L)}</>}
                          {lidar.state === "error" && R && <>{metres(R.path_m)} (30 m NLDI trace; lidar unavailable: {lidar.error})</>}
                        </div>
                        {L && L.ponds.length > 0 && (
                          <div className="route-note">
                            {L.ponds.length} low spot{L.ponds.length > 1 ? "s" : ""} on the way (blue dots). In the lidar these are depressions the water fills before spilling on; in streets they're often storm inlets.
                          </div>
                        )}
                      </div>
                    </li>
                    {D.segments.map((s, i) => s.gap ? (
                      <li key={i} className="route-step">
                        <span className="key key-gap" />
                        <div>
                          <div className="route-name">{s.name}</div>
                          <div className="route-meta">about {km(s.length_km)} in a straight line · {duration(s.hours)} assumed</div>
                          <div className="route-note">The mapped stream ends here. It most likely continues in a sewer or culvert, so this stretch is a guess.</div>
                        </div>
                      </li>
                    ) : (
                      <li key={i} className={`route-step ${focusSeg === i ? "on" : ""}`}>
                        <span className="key" style={{ background: CSS.water, height: Math.min(8, 2 + s.max_order * 0.6) }} />
                        <div>
                          <div className="route-name">{s.name}</div>
                          <div className="route-meta">
                            {km(s.length_km)} · {duration(s.hours)} · drops {elev(s.drop_m)} · mean flow at end {flow(s.end_flow_cms)}
                          </div>
                          <button className="link small" onClick={() => s.end_comid && showBasin(s.end_comid, i)}>
                            Show everything that drains here{s.end_da_km2 ? ` (${area(s.end_da_km2)})` : ""}
                          </button>
                        </div>
                      </li>
                    ))}
                    <li className="route-step">
                      <span className="key key-end" />
                      <div>
                        <div className="route-name">{D.outlet.name}</div>
                        <div className="route-meta">{totals && <>{km(totals.km)} and about {duration(totals.hours)} from your roof</>}</div>
                      </div>
                    </li>
                  </ol>
                  {D.approximate && <p className="route-note">{D.approximate}</p>}
                  {basin.state === "loading" && <p className="loading small">Delineating the upstream basin…</p>}
                  {basin.state === "ok" && (
                    <p className="basin-note">
                      <span className="key-fill" /> Shaded: everything upstream of the end of {basin.data.name ?? D.segments[focusSeg ?? 0]?.name},{" "}
                      {area(basin.data.areasqkm)}. <button className="link small" onClick={() => { setBasinComid(null); setFocusSeg(null); }}>Clear</button>
                    </p>
                  )}
                  <div className="actions">
                    {!riding ? (
                      <button className="primary" onClick={startRide}>▶ Follow the raindrop</button>
                    ) : (
                      <button onClick={endRide}>■ Stop</button>
                    )}
                    <button onClick={() => {
                      const bb = D.reaches.features.flatMap((f) => f.geometry.coordinates);
                      fit([Math.min(...bb.map((c) => c[0])), Math.min(...bb.map((c) => c[1])), Math.max(...bb.map((c) => c[0])), Math.max(...bb.map((c) => c[1]))], 12);
                    }}>Whole route</button>
                    <button onClick={() => {
                      if (!overland || !site) return;
                      const c = [[site.lon, site.lat], ...overland.coords, [D.entry.lon, D.entry.lat]];
                      fit([Math.min(...c.map((p) => p[0])), Math.min(...c.map((p) => p[1])), Math.max(...c.map((p) => p[0])), Math.max(...c.map((p) => p[1]))], 17);
                    }}>Roof to creek</button>
                  </div>
                </>
              )}
            </section>
          )}

          {profile.pts.length > 1 && (
            <section>
              <h2>Elevation along the way</h2>
              <p className="section-note">Distance axis is logarithmic, so the first few hundred metres aren't squeezed out by the river. Hover to find a spot on the map.</p>
              <Profile
                points={profile.pts}
                marks={profile.marks}
                overlandKm={overland?.km ?? 0}
                onCursor={(p) => setCursor(p ? [p.lon, p.lat] : null)}
              />
            </section>
          )}

          {site && (
            <section>
              <h2>Below ground</h2>
              <p className="section-note">
                Not all rain takes the path above. What soaks in recharges shallow groundwater, and most of that seeps back into
                {D?.entry.stream ? ` ${D.entry.stream}` : " nearby creeks"} as baseflow over weeks to years; some reaches deeper aquifers.
              </p>
              {below.state === "loading" && <p className="loading">Looking up soil, surface, aquifer and wells…</p>}
              {below.state === "error" && <p className="error">{below.error}</p>}
              {below.state === "ok" && <BelowGround data={below.data} />}
            </section>
          )}
          </>
        )}

        {site && tab === "place" && (
          <PlaceTab
            site={site} geocode={place} units={units} downstream={D} lidar={L} place={placeData}
            below={ok(below)} life={ok(lifeData)} flows={ok(flowsData)} restore={ok(restoreData)}
          />
        )}
        {site && tab === "life" && <LifeTab life={lifeData} />}
        {site && tab === "flows" && <FlowsTab flows={flowsData} downstream={D} />}
        {site && tab === "restore" && (
          <RestoreTab
            ghost={ghost} restore={restoreData} overlay={overlay} onOverlay={setOverlay}
            opacity={overlayOpacity} onOpacity={setOverlayOpacity}
          />
        )}

        <footer>
          <p>
            <strong>How this works.</strong> Watershed boundaries come from the USGS Watershed Boundary Dataset (HUC2–HUC12) and NHDPlus catchments.
            The roof-to-creek path walks downhill on USGS 3DEP 1 m bare-earth lidar; the dashed line is the USGS NLDI 30 m raindrop trace.
            Downstream travel uses NHDPlus V2 flowlines and their mean-annual velocities.
          </p>
          <p>
            <strong>Storm sewers aren't in the terrain.</strong> In built-up areas most runoff drops into an inlet within a block or two and travels by pipe;
            pipes usually reach the same creek, but can cross small divides. In combined-sewer areas it goes to a treatment plant first.
          </p>
          <details className="sources">
            <summary>Data sources</summary>
            <ul>
              <li><strong>Watersheds and streams:</strong> USGS Watershed Boundary Dataset; NHDPlus V2 and High Resolution; USGS NLDI.</li>
              <li><strong>Terrain:</strong> USGS 3DEP 1 m and 5 m lidar DEMs; AWS Terrain Tiles (3D view).</li>
              <li><strong>Addresses:</strong> OpenStreetMap Nominatim, US Census Geocoder, FEMA/ORNL USA Structures.</li>
              <li><strong>Below ground:</strong> USDA NRCS SSURGO; Annual NLCD (USGS/MRLC); NOAA Atlas 14; USGS principal aquifers, karst and Water Data wells; MO DNR sinkholes.</li>
              <li><strong>Place:</strong> EPA ecoregions; One Earth bioregions; RESOLVE Ecoregions 2017; Native Land Digital (not an authoritative or legal map); USFS Royce land cessions; USDA plant hardiness zones; USFWS flyways; NOAA 1991–2020 normals; USA-NPN; Open-Meteo (ERA5); Köppen-Geiger maps by Beck et al. (2023).</li>
              <li><strong>Life:</strong> iNaturalist research-grade observations (photo credits on each photo).</li>
              <li><strong>Flows:</strong> USGS SPARROW Midwest 2012 (Robertson &amp; Saad 2019); Census TIGERweb; EPA community water systems, SDWIS, CWNS sewersheds and ATTAINS; Water Quality Portal.</li>
              <li><strong>Restore:</strong> LANDFIRE Biophysical Settings; WDNR rain garden manual; MSD Project Clear.</li>
              <li><strong>Base maps:</strong> OpenFreeMap / © OpenStreetMap contributors; USGS The National Map.</li>
            </ul>
            <p>Everything is looked up live from these public services, so a slow or offline source can leave a panel empty. Nothing you search is stored beyond a response cache.</p>
          </details>
          <p className="credit">Made by <a href="https://www.aeholbrook.com/">Adam Holbrook</a>.</p>
        </footer>
      </aside>

      <main className="stage">
        <MapView
          site={site}
          onPick={(p) => pick(p)}
          units={units}
          selected={selected}
          hovered={hovered}
          raindrop={R}
          lidar={L}
          downstream={D}
          below={ok(below)}
          ghost={ok(ghost)}
          tab={tab}
          placeData={ok(placeData)}
          flowsData={ok(flowsData)}
          restoreData={ok(restoreData)}
          overlay={overlay}
          overlayOpacity={overlayOpacity}
          focusSeg={focusSeg}
          basin={ok(basin)}
          explore={explore}
          view={view}
          basemap={basemap}
          exaggeration={exaggeration}
          wbdOverlay={wbdOverlay}
          cursor={cursor}
          riding={riding}
          onReady={(m) => {
            mapRef.current = m;
            if (site) m.jumpTo({ center: [site.lon, site.lat], zoom: 15 });
          }}
          onContextStatus={setCtxStatus}
          koppenPath={koppenPath}
          onExplore={setExplore}
          onKoppenView={setClimateView}
          theme={mode}
        />

        <div className="controls" role="group" aria-label="Map controls">
          <div className="seg" role="radiogroup" aria-label="View">
            {(["2d", "3d"] as const).map((v) => (
              <button key={v} role="radio" aria-checked={view === v} className={view === v ? "on" : ""} onClick={() => setView(v)}>
                {v.toUpperCase()}
              </button>
            ))}
          </div>
          <div className="seg" role="radiogroup" aria-label="Base map">
            {([["map", "Map"], ["satellite", "Satellite"], ["topo", "USGS Topo"]] as const).map(([v, label]) => (
              <button key={v} role="radio" aria-checked={basemap === v} className={basemap === v ? "on" : ""} onClick={() => setBasemap(v)}>
                {label}
              </button>
            ))}
          </div>
          {view === "3d" && (
            <label className="control-row">
              <span>Terrain ×{exaggeration}</span>
              <input type="range" min={1} max={12} step={0.5} value={exaggeration} onChange={(e) => setExaggeration(parseFloat(e.target.value))} />
            </label>
          )}
          <div className="seg" role="radiogroup" aria-label="Colour theme">
            {([["auto", "Auto"], ["light", "Light"], ["dark", "Dark"]] as const).map(([v, label]) => (
              <button key={v} role="radio" aria-checked={themePref === v} className={themePref === v ? "on" : ""} onClick={() => setThemePref(v)}
                title={v === "auto" ? "Follow your system setting" : `${label} theme`}>
                {label}
              </button>
            ))}
          </div>
          <label className="control-row">
            <span>Watersheds</span>
            <select value={explore ?? ""} onChange={(e) => setExplore((e.target.value || null) as LevelKey | null)}>
              <option value="">— off —</option>
              {EXPLORABLE.map((k) => <option key={k} value={k}>{LEVEL_NAME[k]}</option>)}
            </select>
          </label>
          {explore && (
            <div className="control-note">
              {ctxStatus.loading ? "Loading…"
                : ctxStatus.tooFarOut ? `Zoom in to see every ${LEVEL_NAME[explore].split(" · ")[0].toLowerCase()}`
                  : ["huc2", "huc4", "huc6", "huc8"].includes(explore)
                    ? `${ctxStatus.count.toLocaleString()} nationwide · colours only separate neighbours; hover or click for names`
                    : `${ctxStatus.count} in view · hover for names`}
            </div>
          )}
          <label className="control-row check">
            <input type="checkbox" checked={climateOn} onChange={(e) => setClimateOn(e.target.checked)} />
            <span>Climate zones (Köppen)</span>
          </label>
          {climateOn && (
            <div className="climate-controls">
              <label className="control-row">
                <span className="period-label">{KOPPEN_PERIODS[periodIndex].label}</span>
                <input type="range" min={0} max={KOPPEN_PERIODS.length - 1} step={1} value={periodIndex}
                  aria-label="Climate period" onChange={(e) => setPeriodIndex(parseInt(e.target.value, 10))} />
              </label>
              {"future" in KOPPEN_PERIODS[periodIndex] && (
                <div className="seg" role="radiogroup" aria-label="Emissions scenario">
                  {SCENARIOS.map((sc) => (
                    <button key={sc.id} role="radio" aria-checked={scenario === sc.id} title={sc.long}
                      className={scenario === sc.id ? "on" : ""} onClick={() => setScenario(sc.id)}>
                      {sc.label}
                    </button>
                  ))}
                </div>
              )}
              {"future" in KOPPEN_PERIODS[periodIndex] && (
                <div className="control-note">{SCENARIOS.find((x) => x.id === scenario)?.long} (CMIP6)</div>
              )}
            </div>
          )}
          <label className="control-row check">
            <input type="checkbox" checked={wbdOverlay} onChange={(e) => setWbdOverlay(e.target.checked)} />
            <span>USGS boundary map (all levels)</span>
          </label>
        </div>

        {climateOn && climateView.length > 0 && (
          <div className="climate-legend" aria-label="Climate zones in view">
            <div className="legend-title">Climate zones in view · {KOPPEN_PERIODS[periodIndex].label}</div>
            <ul>
              {climateView.slice(0, 9).map((c) => (
                <li key={c.code}>
                  <span className="swatch-fill" style={{ background: c.color }} />
                  <strong>{c.code}</strong> <span className="legend-name">{c.name}</span>
                  <span className="share-value">{Math.round(c.share * 100)}%</span>
                </li>
              ))}
            </ul>
            <div className="legend-source">Beck et al. (2023), 1 km, CC BY 4.0</div>
          </div>
        )}

        {rideFrame && (
          <div className="ride-hud" aria-live="polite">
            <div className="ride-seg">{rideFrame.segment}</div>
            <div className="ride-stats">
              <span><strong>{km(rideFrame.km)}</strong> traveled</span>
              <span><strong>{duration(rideFrame.hours)}</strong> after the rain</span>
              {rideFrame.elev != null && <span><strong>{elev(rideFrame.elev)}</strong> elevation</span>}
            </div>
            {rideFrame.done && <button className="link" onClick={endRide}>Close</button>}
          </div>
        )}
      </main>
    </div>
  );
}

function endReason(l: LidarTrace) {
  switch (l.end_reason) {
    case "stream": return "";
    case "depression": return "· stops in a large depression (likely a detention basin or sinkhole)";
    case "no_lidar": return "· leaves the lidar coverage";
    case "too_far":
    case "edge": return "· trace capped before reaching a mapped stream";
    case "time_budget": return "· trace stopped at its time limit before reaching a stream";
    default: return "";
  }
}
