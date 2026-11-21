import { useMemo, useRef, useState } from "react";
import { elev, km as fmtKm } from "../format";
import { INK, WATER } from "../palette";

export type ProfilePoint = { km: number; elev: number; lon: number; lat: number; segment: string };
export type ProfileMark = { km: number; label: string };

type Props = {
  points: ProfilePoint[];
  marks: ProfileMark[];
  overlandKm: number;
  onCursor: (p: ProfilePoint | null) => void;
};

const W = 360;
const H = 190;
const M = { top: 14, right: 12, bottom: 30, left: 40 };
const IW = W - M.left - M.right;
const IH = H - M.top - M.bottom;

// A distance axis that is linear near zero and logarithmic beyond, so the first few
// hundred metres (yard, street, swale) get as much room as the 1,800 km of river.
const K0 = 0.05;
const sx = (km: number) => Math.log10(1 + km / K0);

function niceTicks(maxKm: number) {
  return [0, 0.1, 0.5, 1, 5, 10, 50, 100, 500, 1000, 5000].filter((t) => t <= maxKm * 1.0001);
}

function tickLabel(t: number) {
  if (t === 0) return "0";
  if (t < 1) return `${t * 1000} m`;
  return t >= 1000 ? `${t / 1000}k km` : `${t} km`;
}

export function Profile({ points, marks, overlandKm, onCursor }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const geom = useMemo(() => {
    const maxKm = points[points.length - 1]?.km ?? 1;
    const els = points.map((p) => p.elev);
    const lo = Math.min(...els), hi = Math.max(...els);
    const step = hi - lo > 150 ? 50 : hi - lo > 60 ? 20 : 10;
    const y0 = Math.floor(lo / step) * step, y1 = Math.ceil(hi / step) * step || step;
    const X = (km: number) => M.left + (sx(km) / sx(maxKm)) * IW;
    const Y = (e: number) => M.top + IH - ((e - y0) / (y1 - y0 || 1)) * IH;
    const line = points.map((p, i) => `${i ? "L" : "M"}${X(p.km).toFixed(1)},${Y(p.elev).toFixed(1)}`).join("");
    const areaPath = `${line}L${X(maxKm).toFixed(1)},${M.top + IH}L${M.left},${M.top + IH}Z`;
    const yTicks: number[] = [];
    for (let v = y0; v <= y1 + 1e-6; v += step) yTicks.push(v);
    return { maxKm, X, Y, line, areaPath, yTicks, xs: points.map((p) => X(p.km)) };
  }, [points]);

  if (points.length < 2) return null;

  const pick = (clientX: number) => {
    const rect = svgRef.current!.getBoundingClientRect();
    const x = ((clientX - rect.left) / rect.width) * W;
    let best = 0, bd = Infinity;
    geom.xs.forEach((px, i) => {
      const d = Math.abs(px - x);
      if (d < bd) { bd = d; best = i; }
    });
    setHover(best);
    onCursor(points[best]);
  };

  const leave = () => { setHover(null); onCursor(null); };
  const hp = hover != null ? points[hover] : null;
  const overX = geom.X(Math.min(overlandKm, geom.maxKm));

  // Direct labels in up to three staggered rows above the plot; a label that fits no
  // row is left to the tooltip and the route list rather than overlapping another.
  const CHAR_W = 6.2;
  const rowEnds = [-Infinity, -Infinity, -Infinity];
  const shownMarks: (ProfileMark & { x: number; row: number; flip: boolean })[] = [];
  for (const m of marks) {
    const x = geom.X(m.km);
    const w = m.label.length * CHAR_W + 8;
    const flip = x + w > W;
    const from = flip ? x - w : x;
    const row = rowEnds.findIndex((end) => from > end + 4);
    if (row < 0) continue;
    rowEnds[row] = flip ? x : x + w;
    shownMarks.push({ ...m, x, row, flip });
  }
  const rows = shownMarks.reduce((n, m) => Math.max(n, m.row + 1), 0);

  return (
    <div className="profile">
      <svg
        ref={svgRef}
        viewBox={`0 ${-14 * rows} ${W} ${H + 14 * rows}`}
        role="img"
        aria-label="Elevation along the water's path from your roof to the outlet"
        tabIndex={0}
        onPointerMove={(e) => pick(e.clientX)}
        onPointerLeave={leave}
        onBlur={leave}
        onKeyDown={(e) => {
          if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
          e.preventDefault();
          const i = Math.max(0, Math.min(points.length - 1, (hover ?? 0) + (e.key === "ArrowRight" ? 1 : -1) * Math.max(1, Math.round(points.length / 60))));
          setHover(i);
          onCursor(points[i]);
        }}
      >
        {overlandKm > 0 && (
          <g>
            <rect x={M.left} y={M.top} width={Math.max(0, overX - M.left)} height={IH} fill={INK.grid} opacity={0.45} />
            <text x={M.left + 4} y={M.top + 11} className="axis-label">overland</text>
          </g>
        )}
        {geom.yTicks.map((v) => (
          <g key={v}>
            <line x1={M.left} x2={W - M.right} y1={geom.Y(v)} y2={geom.Y(v)} stroke={INK.grid} strokeWidth={1} />
            <text x={M.left - 6} y={geom.Y(v) + 3.5} textAnchor="end" className="axis-label tabular">{v}</text>
          </g>
        ))}
        <text x={8} y={M.top + IH / 2} className="axis-label" transform={`rotate(-90 8 ${M.top + IH / 2})`} textAnchor="middle">m</text>
        <line x1={M.left} x2={W - M.right} y1={M.top + IH} y2={M.top + IH} stroke={INK.axis} strokeWidth={1} />
        {niceTicks(geom.maxKm).map((t) => (
          <text key={t} x={geom.X(t)} y={H - 10} textAnchor="middle" className="axis-label tabular">{tickLabel(t)}</text>
        ))}

        {shownMarks.map((m) => (
          <line key={m.km + m.label} x1={m.x} x2={m.x} y1={M.top - 14 * (rows - m.row) + 10} y2={M.top + IH} stroke={INK.axis} strokeWidth={1} />
        ))}
        {shownMarks.map((m) => (
          <text key={`t${m.km}${m.label}`} x={m.flip ? m.x - 4 : m.x + 4} y={M.top - 14 * (rows - m.row) + 9}
            textAnchor={m.flip ? "end" : "start"} className="mark-label">{m.label}</text>
        ))}

        <path d={geom.areaPath} fill={WATER} opacity={0.1} />
        <path d={geom.line} fill="none" stroke={WATER} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />

        {hp && (
          <g pointerEvents="none">
            <line x1={geom.X(hp.km)} x2={geom.X(hp.km)} y1={M.top} y2={M.top + IH} stroke={INK.secondary} strokeWidth={1} />
            <circle cx={geom.X(hp.km)} cy={geom.Y(hp.elev)} r={4.5} fill={WATER} stroke={INK.surface} strokeWidth={2} />
          </g>
        )}
      </svg>
      {hp && (
        <div
          className="chart-tip"
          style={{ left: `${(geom.X(hp.km) / W) * 100}%`, top: `${(14 * rows / (H + 14 * rows)) * 100}%`, transform: geom.X(hp.km) > W * 0.6 ? "translateX(calc(-100% - 10px))" : "translateX(10px)" }}
        >
          <div className="tip-value">{elev(hp.elev)}</div>
          <div className="tip-label">{fmtKm(hp.km)} from your roof</div>
          <div className="tip-label">{hp.segment}</div>
        </div>
      )}
    </div>
  );
}
