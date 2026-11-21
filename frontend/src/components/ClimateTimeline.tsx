import { useEffect, useState } from "react";
import { KOPPEN_PERIODS, SCENARIOS, classAt, periodPath, type KoppenClass } from "../map/koppen";
import type { LngLat } from "../types";

type Row = { label: string; classes: { scenario?: string; k: KoppenClass | null }[] };

/** The Köppen zone at the site for every period, read from the static tiles. */
export function ClimateTimeline({ site }: { site: LngLat }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  useEffect(() => {
    let live = true;
    Promise.all(KOPPEN_PERIODS.map(async (p, i) => {
      const future = "future" in p && p.future;
      const scen = future ? SCENARIOS : [null];
      const classes = await Promise.all(scen.map(async (sc) => ({
        scenario: sc?.label, k: await classAt(periodPath(i, sc?.id ?? "ssp245"), site.lon, site.lat),
      })));
      return { label: p.label, classes };
    })).then((r) => { if (live) setRows(r); });
    return () => { live = false; };
  }, [site.lon, site.lat]);

  if (!rows) return <p className="loading">Reading climate maps…</p>;
  if (rows.every((r) => r.classes.every((c) => !c.k))) return <p className="section-note">No climate map here.</p>;
  const now = rows[3]?.classes[0]?.k;
  // The first future period where any scenario leaves today's zone.
  const shift = rows.slice(4).find((r) => r.classes.some((c) => c.k && c.k.code !== now?.code));
  const codes = shift ? [...new Set(shift.classes.map((c) => c.k?.code).filter(Boolean))] : [];
  const target = shift?.classes.find((c) => c.k && c.k.code !== now?.code)?.k;
  return (
    <>
      {now && (
        <p>
          Today (1991–2020) this spot is <strong>{now.code}</strong>, {now.name.toLowerCase()}.{" "}
          {!shift && <>It stays {now.code} through 2099 in all three emissions scenarios.</>}
          {shift && codes.length === 1 && target && (
            <>By {shift.label} it becomes <strong>{target.code}</strong>, {target.name.toLowerCase()}, under every emissions scenario.</>
          )}
          {shift && codes.length > 1 && (
            <>By {shift.label} it depends on emissions: {shift.classes.map((c, i) => (
              <span key={i}>{i > 0 && "; "}<strong>{c.k?.code ?? "–"}</strong> ({c.scenario?.toLowerCase()})</span>
            ))}.</>
          )}
        </p>
      )}
      <table className="climate-timeline">
        <tbody>
          {rows.map((r) => (
            <tr key={r.label}>
              <td>{r.label}</td>
              <td>
                {r.classes.map((c, i) => (
                  <span key={i} className="kchip" title={c.k?.name}>
                    {c.k && <span className="swatch-fill" style={{ background: c.k.color }} />}
                    <strong>{c.k?.code ?? "–"}</strong>
                    {c.scenario && <span className="fact-sub">{c.scenario.toLowerCase()}</span>}
                  </span>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="source-note">Köppen-Geiger, 1 km: Beck et al. (2023), <em>Scientific Data</em> 10, 724 (CC BY 4.0). Future periods from constrained CMIP6 projections; low/middle/high = SSP1-2.6/SSP2-4.5/SSP5-8.5.</p>
    </>
  );
}
