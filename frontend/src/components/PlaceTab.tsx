import { useState } from "react";
import type {
  BelowGround, Downstream, FlowsData, Geocode, LifeData, LidarTrace, LngLat, Load, PlaceData, RestoreData, Unit,
} from "../types";
import { km } from "../format";
import { Section, Source } from "./ui";
import { ClimateTimeline } from "./ClimateTimeline";

type Props = {
  site: LngLat;
  geocode: Geocode | null;
  units: Unit[];
  downstream: Downstream | null;
  lidar: LidarTrace | null;
  place: Load<PlaceData>;
  below: BelowGround | null;
  life: LifeData | null;
  flows: FlowsData | null;
  restore: RestoreData | null;
};

const ok = <T,>(l: Load<T>) => (l.state === "ok" ? l.data : null);

/** "𐓏𐒰𐓓𐒰𐓓𐒷 … (Osage)" → "Osage" for places that can't render every script (the card image). */
function plainName(name: string) {
  const paren = name.match(/\(([^()]+)\)\s*$/);
  return paren ? paren[1] : name;
}

function moonPhase(d = new Date()) {
  const synodic = 29.530588853;
  const ref = Date.UTC(2000, 0, 6, 18, 14);
  const age = (((d.getTime() - ref) / 86400000) % synodic + synodic) % synodic;
  const names = ["new moon", "waxing crescent", "first quarter", "waxing gibbous", "full moon",
    "waning gibbous", "last quarter", "waning crescent"];
  const name = names[Math.floor((age / synodic) * 8 + 0.5) % 8];
  const toFull = ((synodic / 2 - age) + synodic) % synodic;
  return { name, daysToFull: Math.round(toFull) };
}

export function PlaceTab(p: Props) {
  const place = ok(p.place);
  const card = cardLines(p, place);
  return (
    <>
      <Section title="Your bioregional address" note="Where you are, described by water, land and life instead of streets and zip codes.">
        <AddressCard title={cardTitle(p)} lines={card} />
      </Section>

      <Section title="Ecoregion and bioregion" load={p.place} loading="Looking up ecoregions, homelands and seasons…">
        {(d: PlaceData) => (
          <>
            {d.ecoregions && (
              <ol className="eco-levels">
                {d.ecoregions.levels.map((l) => (
                  <li key={l.level}>
                    <span className="level-kind">Level {l.level} · {l.code}</span>
                    <span className="level-name">{l.name}</span>
                  </li>
                ))}
              </ol>
            )}
            <p className="section-note">Green outlines on the map: your Level III and Level IV ecoregions. They don't follow watershed divides; landforms, soils and plant communities set them.</p>
            {d.bioregion && (
              <p>
                {d.bioregion.bioregion && <>One Earth bioregion: <strong>{d.bioregion.bioregion}</strong> ({d.bioregion.bioregion_code}). </>}
                {d.bioregion.ecoregion && <>Ecoregion: {d.bioregion.ecoregion}, in the {d.bioregion.biome?.toLowerCase()} biome of the {d.bioregion.realm} realm. </>}
                {d.bioregion.conservation && <>Conservation status: <em>{d.bioregion.conservation}</em>.</>}
              </p>
            )}
            <Source>{[d.ecoregions?.source, d.bioregion?.source].filter(Boolean).join("; ")}</Source>
          </>
        )}
      </Section>

      {place && (place.homelands || place.cessions) && (
        <Section title="Whose land" note="Indigenous homelands that include this place, and the treaties by which the United States took it.">
          {place.homelands && (
            <ul className="homelands">
              {place.homelands.territories.map((t) => (
                <li key={t.name}><a href={t.url} target="_blank" rel="noreferrer" className="native-name">{t.name}</a></li>
              ))}
            </ul>
          )}
          {place.cessions && place.cessions.cessions.length > 0 && (
            <ul className="cessions">
              {place.cessions.cessions.map((c) => (
                <li key={c.number}>
                  <strong>{c.date?.slice(0, 4)}</strong> · Royce cession {c.number}: {c.tribes}
                  {c.present_day && <span className="fact-sub"> (today: {c.present_day})</span>}
                  {c.treaty_url && <> · <a href={c.treaty_url} target="_blank" rel="noreferrer">treaty text</a></>}
                </li>
              ))}
              {place.cessions.nearby && <li className="fact-sub">The cession map has a gap at this exact point; these cessions lie within 5 km.</li>}
            </ul>
          )}
          <Source>{place.homelands?.source} {place.cessions?.source}.</Source>
        </Section>
      )}

      {place && (
        <Section title="Seasons and climate">
          <dl className="facts">
            {place.hardiness && <><dt>Hardiness</dt><dd>Zone {place.hardiness.zone} (coldest night {place.hardiness.range_f} °F)</dd></>}
            {place.frost && (
              <>
                <dt>Frost</dt>
                <dd>
                  {place.frost.frost_free ? "Frost-free most years" : <>Last frost around <strong>{place.frost.last_spring}</strong>, first frost around <strong>{place.frost.first_fall}</strong>; a {place.frost.season_days}-day growing season</>}
                  <div className="fact-sub">{place.frost.station}, {place.frost.dist_km} km away</div>
                </dd>
              </>
            )}
            {place.leaf_out && (
              <>
                <dt>Leaf-out</dt>
                <dd>
                  Spring leaf-out averages <strong>{place.leaf_out.average}</strong>
                  {place.leaf_out.anomaly_days != null && place.leaf_out.anomaly_days !== 0 && (
                    <>; in {place.leaf_out.year} it came {Math.abs(place.leaf_out.anomaly_days)} days {place.leaf_out.anomaly_days < 0 ? "early" : "late"}</>
                  )}
                </dd>
              </>
            )}
            {place.precip && (
              <>
                <dt>Rain</dt>
                <dd>
                  {place.precip.normal && <>{place.precip.normal.normal_in.toFixed(1)} in in a normal year</>}
                  {place.precip.last_year && <>; {place.precip.last_year.total_in.toFixed(1)} in fell in {place.precip.last_year.year}</>}
                </dd>
              </>
            )}
            {place.flyway && <><dt>Flyway</dt><dd>{place.flyway}: the migration corridor your birds travel</dd></>}
          </dl>
          <Source>{[place.hardiness?.source, place.frost?.source, place.leaf_out?.source, place.precip?.last_year?.source].filter(Boolean).join("; ")}</Source>
        </Section>
      )}

      <Section title="Climate zone through time" note="Your Köppen climate zone from 1901 to 2099. Turn on Climate zones in the map controls to see how the zones move.">
        <ClimateTimeline site={p.site} />
      </Section>

      <Section title="Where you at?" note={<>A bioregional quiz, adapted from <em>Where You At?</em> (Charles, Dodge, Milliman &amp; Stockley, <em>CoEvolution Quarterly</em>, 1981). Try answering before you open each one.</>}>
        <Quiz {...p} placeData={place} />
      </Section>
    </>
  );
}

// ---------------------------------------------------------------- address card

function cardTitle(p: Props) {
  if (p.geocode) return p.geocode.label.split(",").slice(0, 2).join(",");
  // Opened from a link: the building footprint under the pin may carry its address.
  const addr = p.restore?.roof?.match === "contains" ? p.restore.roof.address : null;
  if (addr) return addr.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
  return `${p.site.lat.toFixed(4)}, ${p.site.lon.toFixed(4)}`;
}

function cardLines(p: Props, place: PlaceData | null): [string, string][] {
  const lines: [string, string][] = [];
  const D = p.downstream;
  if (D) lines.push(["Water", [...D.segments.map((s) => s.name), D.outlet.name].join(" → ")]);
  const h12 = p.units.find((u) => u.level === "huc12");
  if (h12) lines.push(["Watershed", `${h12.name} (HUC ${h12.code})`]);
  const eco = place?.ecoregions?.levels;
  if (eco) lines.push(["Ecoregion", `${eco[3].name}, ${eco[2].name}`]);
  if (place?.bioregion?.bioregion) lines.push(["Bioregion", place.bioregion.bioregion]);
  if (p.restore?.then_now?.before) lines.push(["Land before", p.restore.then_now.before]);
  if (place?.homelands) lines.push(["Homelands", place.homelands.territories.map((t) => plainName(t.name)).join(", ")]);
  if (place?.flyway) lines.push(["Flyway", place.flyway]);
  if (place?.hardiness || place?.frost) {
    const f = place.frost;
    lines.push(["Season", [place.hardiness && `Zone ${place.hardiness.zone}`,
      f && (f.frost_free ? "frost-free" : `frost-free ${f.last_spring} – ${f.first_fall}`)].filter(Boolean).join(" · ")]);
  }
  if (p.below?.soil?.component) lines.push(["Soil", `${p.below.soil.component} series${p.below.soil.urban ? " under urban fill" : ""}`]);
  if (p.below?.aquifer) lines.push(["Below", p.below.aquifer.name]);
  return lines;
}

function AddressCard({ title, lines }: { title: string; lines: [string, string][] }) {
  const [copied, setCopied] = useState(false);
  const text = `${title}\n${lines.map(([k, v]) => `${k}: ${v}`).join("\n")}`;
  return (
    <div className="address-card">
      <div className="card-title">{title}</div>
      <dl>
        {lines.map(([k, v]) => (
          <div key={k} className="card-row"><dt>{k}</dt><dd>{v}</dd></div>
        ))}
      </dl>
      <div className="actions">
        <button onClick={() => navigator.clipboard?.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })}>
          {copied ? "Copied" : "Copy as text"}
        </button>
        <button onClick={() => downloadCard(title, lines)}>Download image</button>
      </div>
    </div>
  );
}

function downloadCard(title: string, lines: [string, string][]) {
  const W = 1200, pad = 64, labelW = 230, lineH = 40;
  const c = document.createElement("canvas");
  const ctx = c.getContext("2d")!;
  const font = (w: number, s: number) => `${w} ${s}px system-ui, -apple-system, "Segoe UI", sans-serif`;
  const wrap = (s: string, width: number) => {
    ctx.font = font(400, 28);
    const words = s.split(" ");
    const out: string[] = [];
    let cur = "";
    for (const w of words) {
      const t = cur ? `${cur} ${w}` : w;
      if (ctx.measureText(t).width > width && cur) { out.push(cur); cur = w; } else cur = t;
    }
    if (cur) out.push(cur);
    return out;
  };
  const wrapped = lines.map(([k, v]) => [k, wrap(v, W - pad * 2 - labelW)] as const);
  const H = pad * 2 + 90 + wrapped.reduce((n, [, v]) => n + v.length * lineH + 18, 0) + 40;
  c.width = W; c.height = H;
  ctx.fillStyle = "#fcfcfb"; ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = "#2a78d6"; ctx.fillRect(0, 0, W, 12);
  ctx.fillStyle = "#0b0b0b"; ctx.font = font(650, 44); ctx.fillText(title, pad, pad + 40);
  let y = pad + 110;
  for (const [k, v] of wrapped) {
    ctx.fillStyle = "#898781"; ctx.font = font(600, 22); ctx.fillText(k.toUpperCase(), pad, y);
    ctx.fillStyle = "#0b0b0b"; ctx.font = font(400, 28);
    v.forEach((line, i) => ctx.fillText(line, pad + labelW, y + i * lineH));
    y += v.length * lineH + 18;
  }
  ctx.fillStyle = "#898781"; ctx.font = font(400, 18);
  ctx.fillText("Sources: USGS WBD & NHDPlus, US EPA ecoregions, One Earth, LANDFIRE, Native Land Digital, USFWS, USDA, NOAA", pad, H - 32);
  const a = document.createElement("a");
  a.href = c.toDataURL("image/png");
  a.download = `${title.replace(/[^\w]+/g, "-").toLowerCase()}-bioregional-address.png`;
  a.click();
}

// ---------------------------------------------------------------- quiz

function Quiz(p: Props & { placeData: PlaceData | null }) {
  const { downstream: D, placeData: pl, below, life, flows, restore, lidar } = p;
  const moon = moonPhase();
  const qs: { q: string; a: React.ReactNode | null; hint?: string }[] = [
    {
      q: "Trace the rain that falls on your roof to the sea.",
      a: D && <>Over the ground{lidar ? ` ${km(lidar.length_m / 1000)}` : ""} into {D.segments[0]?.name ?? "a stream"}{D.segments.length > 1 ? `, then ${D.segments.slice(1).map((s) => s.name).join(", then ")}` : ""}, to the {D.outlet.name}: about {km(D.totals.length_km + (lidar ? lidar.length_m / 1000 : 0))} in all.</>,
    },
    {
      q: "Where does the water from your tap come from?",
      a: flows?.tap_water && <>{flows.tap_water.name}: {flows.tap_water.source_type ?? "unknown source"} from {flows.tap_water.primary_sources.slice(0, 4).map((s) => s.name).join(", ") || "sources not listed"}.</>,
    },
    {
      q: "Where does it go after you flush?",
      a: flows?.wastewater && <>{flows.wastewater.facility}{flows.wastewater.combined_sewer?.combined ? ", through combined sewers that overflow to the river in big storms" : ""}.</>,
    },
    {
      q: "What soil are you standing on?",
      a: below?.soil && <>{below.soil.mapunit}; the main soil is {below.soil.component}, hydrologic group {below.soil.hydgrp ?? "unknown"}.</>,
    },
    {
      q: "How much rain falls here in a year? How much fell last year?",
      a: pl?.precip && <>{pl.precip.normal ? `${pl.precip.normal.normal_in.toFixed(1)} in normally` : ""}{pl.precip.last_year ? `; ${pl.precip.last_year.total_in.toFixed(1)} in in ${pl.precip.last_year.year}` : ""}.</>,
    },
    {
      q: "How long is the growing season? When are the frosts?",
      a: pl?.frost && (pl.frost.frost_free ? <>Frost-free most years.</> : <>{pl.frost.season_days} days, from about {pl.frost.last_spring} to {pl.frost.first_fall}.</>),
    },
    {
      q: "What grew here before the land was settled and built on?",
      a: restore?.then_now?.before && <>{restore.then_now.before}. Today: {restore.then_now.now?.toLowerCase()}.</>,
    },
    {
      q: "Whose homeland is this? How was it taken?",
      a: pl?.homelands && <>{pl.homelands.territories.map((t) => plainName(t.name)).join(", ")}.{pl.cessions?.cessions[0] && <> Ceded in {pl.cessions.cessions.map((c) => `${c.date?.slice(0, 4)} (${c.tribes})`).join("; ")}.</>}</>,
    },
    {
      q: "Name five native plants of your area.",
      a: life && life.native_plants.length > 0 && <>{life.native_plants.slice(0, 5).map((s) => s.common ?? s.name).join(", ")}.</>,
    },
    {
      q: "Which spring wildflowers bloom first?",
      a: life && life.early_bloomers.length > 0 && <>{life.early_bloomers.slice(0, 4).map((s) => s.common ?? s.name).join(", ")}.</>,
    },
    {
      q: "Name birds that live here all year, and some that only visit.",
      a: life?.birds && <>Year-round: {life.birds.year_round.slice(0, 4).map((s) => s.common).join(", ")}. Summer: {life.birds.summer.slice(0, 3).map((s) => s.common).join(", ")}. Winter: {life.birds.winter.slice(0, 3).map((s) => s.common).join(", ")}.</>,
    },
    {
      q: "What lies beneath you?",
      a: below && <>{below.aquifer?.name ?? "No mapped principal aquifer"}{below.karst?.in_karst ? `; karst (${below.karst.description})` : ""}.</>,
    },
    {
      q: "What ecological region are you in?",
      a: pl?.ecoregions && <>{pl.ecoregions.levels[3].name}, within {pl.ecoregions.levels[2].name}{pl.bioregion?.bioregion ? `, in the ${pl.bioregion.bioregion} bioregion` : ""}.</>,
    },
    {
      q: "When do the trees leaf out?",
      a: pl?.leaf_out && <>Around {pl.leaf_out.average} on average{pl.leaf_out.anomaly_days ? `; ${Math.abs(pl.leaf_out.anomaly_days)} days ${pl.leaf_out.anomaly_days < 0 ? "early" : "late"} in ${pl.leaf_out.year}` : ""}.</>,
    },
    {
      q: "What phase is the moon in, and how many days until it's full?",
      a: <>{moon.name[0].toUpperCase() + moon.name.slice(1)}; {moon.daysToFull === 0 ? "full tonight" : `about ${moon.daysToFull} days to full`}.</>,
    },
    {
      q: "On what day of the year are shadows shortest here?",
      a: <>{p.site.lat > 23.44 ? "The June solstice, around June 21, at solar noon." : p.site.lat < -23.44 ? "The December solstice, around December 21, at solar noon." : "Twice a year, on the days the noon sun passes straight overhead."}</>,
    },
    { q: "Where does your garbage go?", a: null, hint: "Look up your hauler's landfill or transfer station. Which watershed is it in?" },
    { q: "From where you're sitting, point north.", a: null, hint: "Then check: the map's north arrow is in the corner." },
    { q: "What species have disappeared from your area?", a: null, hint: "Ask your state's natural heritage program or a local naturalist group." },
  ];
  return (
    <div className="quiz">
      {qs.map((item, i) => (
        <details key={i}>
          <summary>{item.q}</summary>
          <div className="quiz-answer">
            {item.a ?? (item.hint ? <em>{item.hint}</em> : <span className="loading">Still gathering the data…</span>)}
          </div>
        </details>
      ))}
    </div>
  );
}
