import type { ReactNode } from "react";
import type { Load, Species } from "../types";

/** A titled panel section that shows loading / error states for its data. */
export function Section<T>({ title, note, load, loading, children }: {
  title: string;
  note?: ReactNode;
  load?: Load<T>;
  loading?: string;
  children: (data: T) => ReactNode;
} | {
  title: string;
  note?: ReactNode;
  load?: undefined;
  loading?: string;
  children: ReactNode;
}) {
  let body: ReactNode;
  if (!load) body = children as ReactNode;
  else if (load.state === "loading") body = <p className="loading">{loading ?? "Loading…"}</p>;
  else if (load.state === "error") body = <p className="error">{load.error}</p>;
  else if (load.state === "ok") body = (children as (d: T) => ReactNode)(load.data);
  else body = null;
  return (
    <section>
      <h2>{title}</h2>
      {note && <p className="section-note">{note}</p>}
      {body}
    </section>
  );
}

export function Source({ children }: { children: ReactNode }) {
  return <p className="source-note">{children}</p>;
}

export function SpeciesGrid({ species, empty }: { species: Species[]; empty?: string }) {
  if (!species.length) return <p className="section-note">{empty ?? "No research-grade records yet."}</p>;
  return (
    <ul className="species">
      {species.map((s) => (
        <li key={s.id}>
          <a href={s.url ?? undefined} target="_blank" rel="noreferrer" title={s.attribution ?? undefined}>
            {s.photo ? <img src={s.photo} alt="" loading="lazy" width={56} height={56} /> : <span className="no-photo" />}
            <span className="sp-text">
              <span className="sp-common">{s.common ?? s.name}</span>
              <span className="sp-sci">{s.name}</span>
              <span className="sp-count">{s.count.toLocaleString()} records</span>
            </span>
          </a>
        </li>
      ))}
    </ul>
  );
}

/** Status marks never rely on colour alone: an icon and a word go with it. */
export function Status({ kind, children }: { kind: "good" | "critical" | "unknown"; children: ReactNode }) {
  const icon = kind === "good" ? "✓" : kind === "critical" ? "!" : "–";
  return <span className={`status status-${kind}`}><span className="status-icon" aria-hidden="true">{icon}</span>{children}</span>;
}

/** Horizontal share bars: one hue, value labelled at the tip, sorted by size. */
export function ShareBars({ rows, label }: { rows: { source: string; share: number }[]; label: string }) {
  const max = Math.max(...rows.map((r) => r.share), 0.01);
  return (
    <ul className="share-bars" aria-label={label}>
      {rows.map((r) => (
        <li key={r.source}>
          <span className="share-name">{r.source}</span>
          <span className="share-track">
            <span className="share-fill" style={{ width: `${(r.share / max) * 100}%` }} />
          </span>
          <span className="share-value">{Math.round(r.share * 100)}%</span>
        </li>
      ))}
    </ul>
  );
}
