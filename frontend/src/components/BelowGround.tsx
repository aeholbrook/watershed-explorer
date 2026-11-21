import type { BelowGround as Data } from "../types";
import { CSS } from "../palette";

const { overland: OVERLAND, soak: SOAK } = CSS;

const SOIL_GROUP: Record<string, string> = {
  A: "A — sandy, water soaks in fast",
  B: "B — loamy, moderate infiltration",
  C: "C — silty or clayey, slow infiltration",
  D: "D — clay or shallow to rock/water table, very slow",
};

function pct(f: number) {
  return `${Math.round(f * 100)}%`;
}

/** The complement of a rounded share, so the pair always adds to 100%. */
function rest(f: number) {
  return `${100 - Math.round(f * 100)}%`;
}

export function BelowGround({ data }: { data: Data }) {
  const { soil, land, runoff, aquifer, karst, wells } = data;
  const group = soil?.hydgrp?.trim().toUpperCase().slice(0, 1) ?? null;

  return (
    <div className="below">
      {runoff.length > 0 && (
        <>
          <div className="legend" aria-hidden="true">
            <span><i style={{ background: OVERLAND }} />Runs off (the orange path)</span>
            <span><i style={{ background: SOAK }} />Soaks in or evaporates</span>
          </div>
          <ul className="split-bars">
            {runoff.map((r) => (
              <li key={r.label}>
                <div className="split-label">
                  <span>{r.label}</span>
                  <span className="split-values">{pct(r.runoff_frac)} runs off · {rest(r.runoff_frac)} soaks in</span>
                </div>
                <svg className="split-bar" viewBox="0 0 300 14" preserveAspectRatio="none" role="img"
                  aria-label={`${r.label}: ${pct(r.runoff_frac)} runs off, ${rest(r.runoff_frac)} soaks in`}>
                  {r.runoff_frac > 0.005 && (
                    <rect x={0} y={0} width={Math.max(0, 300 * r.runoff_frac - 1)} height={14} rx={4} fill={OVERLAND}>
                      <title>{`Runs off: ${pct(r.runoff_frac)} of ${r.storm_in} in`}</title>
                    </rect>
                  )}
                  {r.runoff_frac < 0.995 && (
                    <rect x={Math.min(300, 300 * r.runoff_frac + 1)} y={0} width={Math.max(0, 300 * (1 - r.runoff_frac) - 1)} height={14} rx={4} fill={SOAK}>
                      <title>{`Soaks in or evaporates: ${rest(r.runoff_frac)}`}</title>
                    </rect>
                  )}
                </svg>
              </li>
            ))}
          </ul>
          <p className="section-note">
            NRCS curve-number estimate for the ground around the pin
            {land ? ` (${Math.round(land.impervious_pct)}% paved or roofed within ${land.radius_m} m` : " ("}
            {group ? `, soil group ${group})` : ")"}. Your roof itself sheds essentially all of it.
          </p>
        </>
      )}

      <dl className="facts">
        {soil && (
          <>
            <dt>Soil</dt>
            <dd>
              {soil.mapunit}
              {group && <div className="fact-sub">Hydrologic group {SOIL_GROUP[group] ?? soil.hydgrp}</div>}
              {soil.drainage && <div className="fact-sub">{soil.drainage}{soil.water_table_cm != null ? ` · water table as shallow as ${(soil.water_table_cm / 100).toFixed(1)} m` : ""}</div>}
              {soil.urban && <div className="fact-sub">Mapped as urban land: graded, compacted fill is common, so real infiltration is often lower than the soil group suggests.</div>}
            </dd>
          </>
        )}
        {land && (
          <>
            <dt>Surface</dt>
            <dd>
              {Math.round(land.impervious_pct)}% impervious within {land.radius_m} m{land.land_cover ? ` · ${land.land_cover}` : ""}
              <div className="fact-sub">{land.source}</div>
            </dd>
          </>
        )}
        {aquifer && (
          <>
            <dt>Aquifer</dt>
            <dd>
              {aquifer.name}
              {aquifer.rock_type && <div className="fact-sub">{aquifer.rock_type}</div>}
            </dd>
          </>
        )}
        {karst && (
          <>
            <dt>Karst</dt>
            <dd>
              {karst.in_karst ? (karst.description ?? "In a karst area") : "Not in a mapped karst area"}
              {karst.sinkholes.length > 0 && (
                <div className="fact-sub">
                  {karst.sinkholes.length} mapped sinkhole{karst.sinkholes.length > 1 ? "s" : ""} within 2 km (dark rings on the map), nearest {Math.round(karst.sinkholes[0].dist_m)} m.
                  Water entering one goes straight to groundwater and can resurface in a different watershed.
                </div>
              )}
            </dd>
          </>
        )}
        {wells.length > 0 && (
          <>
            <dt>Nearest live well</dt>
            <dd>
              <ul className="wells">
                {wells.slice(0, 3).map((w) => (
                  <li key={w.id}>
                    {w.depth_to_water_m != null ? <strong>{w.depth_to_water_m.toFixed(1)} m down</strong> : "no recent level"}
                    {" at "}
                    {w.url ? <a href={w.url} target="_blank" rel="noreferrer">{w.name}</a> : w.name}
                    <span className="fact-sub"> · {w.dist_km.toFixed(1)} km away{w.measured ? ` · ${w.measured}` : ""}{w.well_depth_m ? ` · well ${Math.round(w.well_depth_m)} m deep` : ""}</span>
                  </li>
                ))}
              </ul>
            </dd>
          </>
        )}
      </dl>
      {data.notes.map((n) => <p key={n} className="section-note">{n}</p>)}
    </div>
  );
}
