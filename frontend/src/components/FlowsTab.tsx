import type { Downstream, FlowsData, Load } from "../types";
import { Section, ShareBars, Source, Status } from "./ui";

const n0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

function tonnes(kg: number) {
  if (kg >= 1e9) return `${(kg / 1e9).toFixed(1)} million tonnes`;
  if (kg >= 1e6) return `${n0.format(kg / 1e3)} tonnes`;
  return `${n0.format(kg)} kg`;
}

function joinNames(names: string[]) {
  const u = [...new Set(names)];
  return u.length <= 1 ? u.join("") : `${u.slice(0, -1).join(", ")} and ${u[u.length - 1]}`;
}

/** Group "Missouri River North", "Missouri River Central 1 & 2" … into rivers. */
function sourceWaters(names: string[]) {
  const rivers = names.map((n) => (n.match(/^(.*?(River|Creek|Lake|Reservoir))/i)?.[1] ?? n).trim());
  return joinNames(rivers);
}

export function FlowsTab({ flows, downstream }: { flows: Load<FlowsData>; downstream: Downstream | null }) {
  return (
    <Section title="What your runoff carries" load={flows} loading="Gathering nutrient, census, water-system and water-quality data…">
      {(d: FlowsData) => (
        <>
          {d.nutrients ? <Nutrients d={d} /> : <p className="section-note">No SPARROW nutrient model covers this area yet (the Midwest model is wired up).</p>}

          {d.neighbors && (
            <>
              <h3>Who shares your subwatershed</h3>
              <p>
                <strong>{n0.format(d.neighbors.population)} people</strong> live in {d.neighbors.huc12_name} ({n0.format(d.neighbors.area_km2)} km²),
                split among <strong>{d.neighbors.municipalities.length} municipalities</strong>. The creek doesn't follow city limits; every
                one of them sends runoff to the same water.
              </p>
              <p className="muni-list">
                {d.neighbors.municipalities.map((m) => m.name.replace(/ (city|village|town)$/i, "")).join(" · ")}
              </p>
              <Source>{d.neighbors.source}; a block counts if its centre falls inside the watershed.</Source>
            </>
          )}

          {(d.tap_water || d.wastewater) && <h3>Water in, water out</h3>}
          {d.tap_water && (
            <p>
              <strong>Your tap:</strong> {d.tap_water.name}
              {d.tap_water.source_type && <>, {d.tap_water.source_type}</>}
              {d.tap_water.primary_sources.length > 0 && <> drawn from the <strong>{sourceWaters(d.tap_water.primary_sources.map((s) => s.name))}</strong></>}
              {d.tap_water.population ? <> for about {n0.format(d.tap_water.population)} people</> : null}.
              {downstream && <> Your rain leaves by {downstream.segments[0]?.name}, so the water you drink and the water you shed come from different rivers.</>}
            </p>
          )}
          {d.wastewater && (
            <p>
              <strong>Your drains:</strong> {d.wastewater.facility}
              {d.wastewater.population ? <>, serving {n0.format(d.wastewater.population)} people</> : null}.
              {d.wastewater.combined_sewer?.combined && (
                <> You're in a <strong>combined-sewer</strong> area: rain and sewage share pipes, and in big storms the overflow goes
                  to the river untreated.</>
              )}
              {d.wastewater.combined_sewer && !d.wastewater.combined_sewer.combined && <> Separate storm sewers here: rain goes to the creek, sewage to the plant.</>}
              {d.wastewater.plant && <> The plant is the dark square on the map.</>}
            </p>
          )}

          {d.impaired && d.impaired.units.length > 0 && (
            <>
              <h3>Water quality</h3>
              <p className="section-note">State assessments of streams in your subwatershed and where your water joins the river. Red on the map = impaired.</p>
              <ul className="impaired">
                {d.impaired.units.slice(0, 8).map((u) => (
                  <li key={u.id}>
                    <span className="imp-name">{u.name ?? u.id}{u.on_route && <span className="on-route">on your route</span>}</span>
                    {u.impaired ? <Status kind="critical">Impaired</Status>
                      : u.status === "Fully Supporting" ? <Status kind="good">Meets standards</Status>
                        : <Status kind="unknown">{u.status ?? "Not assessed"}</Status>}
                    {u.causes.length > 0 && (
                      <div className="imp-causes">
                        {u.causes.slice(0, 4).join(", ")}{u.causes.length > 4 ? ` and ${u.causes.length - 4} more` : ""}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
              {d.monitoring && (
                <p className="section-note">
                  {n0.format(d.monitoring.total)} water-quality monitoring sites in this subwatershed
                  ({d.monitoring.usgs} USGS, {d.monitoring.other} state and other).{" "}
                  <a href={d.monitoring.url} target="_blank" rel="noreferrer">Water Quality Portal</a>
                </p>
              )}
              <Source>{d.impaired.source}</Source>
            </>
          )}
        </>
      )}
    </Section>
  );
}

function Nutrients({ d }: { d: FlowsData }) {
  const n = d.nutrients!;
  const tn = n.nitrogen;
  const tp = n.phosphorus;
  const river = n.river_nitrogen;
  return (
    <>
      <p>
        Your catchment sheds about <strong>{n0.format(tn.local_yield_kg_km2)} kg of nitrogen</strong>
        {tp && <> and <strong>{n0.format(tp.local_yield_kg_km2)} kg of phosphorus</strong></>} per square kilometre a year.
        An estimated <strong>{Math.round(tn.delivered_frac * 100)}%</strong> of that nitrogen reaches the {tn.target}, where
        nutrients from across the basin feed the summer low-oxygen "dead zone".
      </p>
      <h3>Where your catchment's nitrogen comes from</h3>
      <ShareBars rows={tn.local_sources} label="Nitrogen sources in your catchment" />
      {river && river.river_sources.length > 0 && (
        <>
          <h3>The {river.stream ?? "river"} where your water joins it</h3>
          <p className="section-note">
            Carries about {tonnes(river.river_load_kg)} of nitrogen a year from {n0.format(river.upstream_km2)} km² upstream. Its sources:
          </p>
          <ShareBars rows={river.river_sources} label="Nitrogen sources in the river" />
        </>
      )}
      <Source>{n.model} (mean-annual conditions). Model estimates, not measurements.</Source>
    </>
  );
}
