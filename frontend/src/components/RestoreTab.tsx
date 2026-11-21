import type { GhostStreams, Load, RestoreData } from "../types";
import { Section, Source } from "./ui";

export type Overlay = null | { kind: "bps" } | { kind: "nlcd" } | { kind: "topo"; year: number };

const n0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export function RestoreTab({ ghost, restore, overlay, onOverlay, opacity, onOpacity }: {
  ghost: Load<GhostStreams>;
  restore: Load<RestoreData>;
  overlay: Overlay;
  onOverlay: (o: Overlay) => void;
  opacity: number;
  onOpacity: (v: number) => void;
}) {
  return (
    <>
      <Section title="Ghost streams" load={ghost} loading="Reading the lidar for valleys with no mapped stream (a few seconds)…"
        note="Dashed blue on the map: where the bare-earth lidar says water concentrates into a channel, but no stream is mapped. Thicker means more land drains through it.">
        {(g: GhostStreams) => (
          <>
            <p>
              <strong>{g.ghost_km.toFixed(1)} km</strong> of unmapped drainage lines within the {g.window_km} km square around you
              {g.nearest && <>; the nearest runs <strong>{n0.format(g.nearest.dist_m)} m</strong> from the pin and drains {g.nearest.area_ha} ha</>}.
            </p>
            <p className="section-note">
              In older suburbs these are mostly the creeks that were graded, culverted or piped as land was built on. In dense street grids
              they often follow streets, which now do the job a creek once did. Daylighting projects start from maps like this.
            </p>
            <Source>USGS 3DEP lidar averaged to 2 m; channels where more than {g.threshold_ha} ha drains; compared against NHDPlus streams.</Source>
          </>
        )}
      </Section>

      <Section title="Your roof and the rain" load={restore} loading="Measuring your roof…">
        {(r: RestoreData) => <RoofPlan r={r} />}
      </Section>

      {restore.state === "ok" && (
        <Section title="Then and now" note="What grew here before Euro-American settlement, what's here now, and old USGS maps of the same ground.">
          <ThenNow r={restore.data} overlay={overlay} onOverlay={onOverlay} opacity={opacity} onOpacity={onOpacity} />
        </Section>
      )}
    </>
  );
}

function RoofPlan({ r }: { r: RestoreData }) {
  if (!r.roof || !r.rain_plan) return <p className="section-note">No building footprint found at the pin. Drag it onto your roof.</p>;
  const p = r.rain_plan;
  return (
    <>
      <p>
        Your roof is about <strong>{n0.format(r.roof.area_ft2)} ft²</strong> ({n0.format(r.roof.area_m2)} m²)
        {r.roof.match === "nearest" && <> (the nearest building, {r.roof.distance_m} m from the pin; check it's yours)</>}.
        {p.annual_gallons && r.precip_normal_in && (
          <> With {r.precip_normal_in.toFixed(1)} in of rain in a normal year it sheds about <strong>{n0.format(p.annual_gallons)} gallons</strong> a year</>
        )}
        , {n0.format(p.gallons_per_inch)} gallons for every inch of rain.
      </p>
      <ul className="plan">
        <li>
          <strong>Rain garden:</strong> about <strong>{n0.format(p.garden_ft2_per_downspout)} ft²</strong> per downspout,
          dug {p.depth} deep, if it's within 30 ft of the house ({n0.format(p.garden_ft2_far)} ft² if farther away).
          Soil here is in the "{p.texture}" class ({p.basis}).
          {p.too_slow && <> That's slower than the 0.15 in/hr minimum the guide recommends; test with a percolation hole first.</>}
        </li>
        <li>
          <strong>Rain barrels:</strong> {p.barrels_per_downspout_inch} × 55-gallon barrels hold one inch of rain from one downspout.
        </li>
      </ul>
      {r.msd && (
        <p>
          {r.msd.eligible_areas.length > 0
            ? <>You're inside MSD's <strong>{r.msd.eligible_areas.join(" and ")}</strong> rainscaping grant area. </>
            : <>MSD runs rainscaping grants in parts of St. Louis. </>}
          <a href={r.msd.url} target="_blank" rel="noreferrer">MSD Project Clear rainscaping</a>. {r.msd.note}
        </p>
      )}
      <Source>Roof: {r.roof.source}. Sizing: {p.source}, one quarter of the roof per downspout.</Source>
    </>
  );
}

function ThenNow({ r, overlay, onOverlay, opacity, onOpacity }: {
  r: RestoreData; overlay: Overlay; onOverlay: (o: Overlay) => void; opacity: number; onOpacity: (v: number) => void;
}) {
  const years = [...new Set(r.topo_editions.map((e) => e.year))];
  const on = (o: Overlay) => JSON.stringify(o) === JSON.stringify(overlay);
  return (
    <>
      {r.then_now && (
        <dl className="facts">
          <dt>Before</dt><dd>{r.then_now.before ?? "unknown"}</dd>
          <dt>Now</dt><dd>{r.then_now.now ?? "unknown"}</dd>
        </dl>
      )}
      <div className="overlay-buttons" role="group" aria-label="Map overlay">
        <button className={overlay === null ? "on" : ""} onClick={() => onOverlay(null)}>Off</button>
        <button className={on({ kind: "bps" }) ? "on" : ""} onClick={() => onOverlay({ kind: "bps" })}>Before settlement</button>
        <button className={on({ kind: "nlcd" }) ? "on" : ""} onClick={() => onOverlay({ kind: "nlcd" })}>Land cover now</button>
        {years.map((y) => (
          <button key={y} className={on({ kind: "topo", year: y }) ? "on" : ""} onClick={() => onOverlay({ kind: "topo", year: y })}>
            {y} map
          </button>
        ))}
      </div>
      {overlay && overlay.kind !== "topo" && r.then_now && (
        <ul className="overlay-legend" aria-label="Overlay legend">
          {(overlay.kind === "bps" ? r.then_now.before_around : r.then_now.now_around).map((c) => (
            <li key={c.name}><span className="swatch-fill" style={{ background: c.color }} />{c.name}<span className="share-value">{Math.round(c.share * 100)}%</span></li>
          ))}
          <li className="fact-sub">Share of the 5 km square around the pin.</li>
        </ul>
      )}
      {overlay && (
        <label className="control-row">
          <span>Overlay opacity</span>
          <input type="range" min={0.2} max={1} step={0.05} value={opacity} onChange={(e) => onOpacity(parseFloat(e.target.value))} />
        </label>
      )}
      <Source>
        {r.then_now?.source}.
        {r.topo_editions.length > 0 && <> Historic maps: USGS Historical Topographic Map Collection via Esri Living Atlas
          ({r.topo_editions.map((e) => `${e.name} ${e.year}`).filter((v, i, a) => a.indexOf(v) === i).join(", ")}).</>}
      </Source>
    </>
  );
}
