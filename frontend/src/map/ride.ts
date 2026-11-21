import type { Map as MapLibreMap } from "maplibre-gl";
import { OVERLAND, WATER } from "../palette";
import { setData } from "./setup";

export type RideFrame = {
  km: number;
  segment: string;
  hours: number;
  elev: number | null;
  done: boolean;
};

export type RidePlan = {
  /** lon/lat vertices from the roof to the outlet */
  coords: [number, number][];
  /** km from the roof at each vertex */
  km: number[];
  /** distance where the overland part ends */
  overlandKm: number;
  /** label + time lookup by distance */
  stops: { km: number; name: string; hours: number }[];
  /** [km from roof, elevation] */
  profile: [number, number][];
  overlandHours: number;
};

const R = 6371.0088;
function hav(a: [number, number], b: [number, number]) {
  const toR = Math.PI / 180;
  const dLat = (b[1] - a[1]) * toR;
  const dLon = (b[0] - a[0]) * toR;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * toR) * Math.cos(b[1] * toR) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

export function cumulativeKm(coords: [number, number][]): number[] {
  const out = [0];
  for (let i = 1; i < coords.length; i++) out.push(out[i - 1] + hav(coords[i - 1], coords[i]));
  return out;
}

function mercX(lon: number) { return (lon + 180) / 360; }
function mercY(lat: number) {
  const s = Math.sin((lat * Math.PI) / 180);
  return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
}

function locate(plan: RidePlan, d: number): { i: number; t: number } {
  const k = plan.km;
  let lo = 0, hi = k.length - 1;
  if (d <= 0) return { i: 0, t: 0 };
  if (d >= k[hi]) return { i: hi - 1, t: 1 };
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (k[mid] <= d) lo = mid; else hi = mid;
  }
  const span = k[lo + 1] - k[lo] || 1;
  return { i: lo, t: (d - k[lo]) / span };
}

export function pointAt(plan: RidePlan, d: number): [number, number] {
  const { i, t } = locate(plan, d);
  const a = plan.coords[i], b = plan.coords[Math.min(i + 1, plan.coords.length - 1)];
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
}

function bearingBetween(a: [number, number], b: [number, number]) {
  const toR = Math.PI / 180;
  const y = Math.sin((b[0] - a[0]) * toR) * Math.cos(b[1] * toR);
  const x = Math.cos(a[1] * toR) * Math.sin(b[1] * toR) - Math.sin(a[1] * toR) * Math.cos(b[1] * toR) * Math.cos((b[0] - a[0]) * toR);
  return (Math.atan2(y, x) * 180) / Math.PI;
}

function interp(points: [number, number][], x: number): number | null {
  if (!points.length) return null;
  if (x <= points[0][0]) return points[0][1];
  for (let i = 1; i < points.length; i++) {
    if (points[i][0] >= x) {
      const [x0, y0] = points[i - 1], [x1, y1] = points[i];
      return y0 + ((y1 - y0) * (x - x0)) / (x1 - x0 || 1);
    }
  }
  return points[points.length - 1][1];
}

export function frameAt(plan: RidePlan, d: number): Omit<RideFrame, "done"> {
  let stop = plan.stops[0];
  for (const s of plan.stops) if (s.km <= d + 1e-9) stop = s;
  // Time: overland at an assumed pace, then the NHDPlus reach velocities via the stops table.
  let hours: number;
  if (d <= plan.overlandKm) {
    hours = plan.overlandKm ? (d / plan.overlandKm) * plan.overlandHours : 0;
  } else {
    const after = plan.stops.filter((s) => s.km >= plan.overlandKm);
    let h = plan.overlandHours;
    for (let i = 0; i < after.length; i++) {
      const s = after[i], next = after[i + 1];
      if (!next || next.km > d) {
        const span = next ? next.km - s.km : 1;
        const frac = next ? (d - s.km) / span : 0;
        h = s.hours + (next ? (next.hours - s.hours) * frac : 0);
        break;
      }
    }
    hours = h;
  }
  return { km: d, segment: stop?.name ?? "", hours, elev: interp(plan.profile, d) };
}

/**
 * Fly the camera along the route. The first part (roof → creek) is a few km at most and
 * the rest can be thousands, so time maps to distance linearly overland and then
 * exponentially, while the camera zooms out as the drop travels.
 */
export function ride(
  map: MapLibreMap,
  plan: RidePlan,
  threeD: boolean,
  onFrame: (f: RideFrame) => void,
): () => void {
  const total = plan.km[plan.km.length - 1];
  const L0 = Math.max(plan.overlandKm, 0.01);
  const T_OVERLAND = Math.min(9000, 3000 + L0 * 2500);
  const T_RIVER = 26000;
  const rest = Math.max(total - L0, 0.001);
  const k = Math.log(rest / 0.05 + 1);

  // Trail progress uses mercator distance, which is what line-progress measures.
  const mx = plan.coords.map(([lon, lat]) => [mercX(lon), mercY(lat)]);
  const mk = [0];
  for (let i = 1; i < mx.length; i++) mk.push(mk[i - 1] + Math.hypot(mx[i][0] - mx[i - 1][0], mx[i][1] - mx[i - 1][1]));
  const mTotal = mk[mk.length - 1] || 1;

  setData(map, "route", { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: plan.coords } });
  map.setLayoutProperty("route-trail", "visibility", "visible");

  let raf = 0;
  let start = 0;
  let bearing = map.getBearing();
  const startPitch = threeD ? 62 : 0;

  const distanceAt = (ms: number) => {
    if (ms <= T_OVERLAND) return (ms / T_OVERLAND) * L0;
    const u = Math.min(1, (ms - T_OVERLAND) / T_RIVER);
    return L0 + (rest * (Math.exp(k * u) - 1)) / (Math.exp(k) - 1);
  };

  const tick = (now: number) => {
    if (!start) start = now;
    const ms = now - start;
    const d = Math.min(total, distanceAt(ms));
    const p = pointAt(plan, d);
    const ahead = pointAt(plan, Math.min(total, d + Math.max(0.04, d * 0.12)));
    const targetBearing = bearingBetween(p, ahead);
    let diff = ((targetBearing - bearing + 540) % 360) - 180;
    if (!threeD) diff = 0;
    bearing += diff * 0.06;

    const zoom = Math.max(4.3, Math.min(17.3, 17.3 - Math.log2(1 + (d * 1000) / 140)));
    map.jumpTo({ center: p, zoom, bearing: threeD ? bearing : 0, pitch: startPitch });

    const { i, t } = locate(plan, d);
    const mprog = (mk[i] + (mk[Math.min(i + 1, mk.length - 1)] - mk[i]) * t) / mTotal;
    const overlandProg = mk[locate(plan, L0).i] / mTotal;
    map.setPaintProperty("route-trail", "line-gradient",
      mprog <= overlandProg
        ? ["step", ["line-progress"], OVERLAND, Math.max(mprog, 1e-6), "rgba(0,0,0,0)"]
        : ["step", ["line-progress"], OVERLAND, overlandProg, WATER, Math.max(mprog, overlandProg + 1e-6), "rgba(0,0,0,0)"]);
    setData(map, "drop", { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: p } });

    const done = d >= total - 1e-6;
    onFrame({ ...frameAt(plan, d), done });
    if (!done) raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);

  return () => {
    cancelAnimationFrame(raf);
    map.setLayoutProperty("route-trail", "visibility", "none");
    setData(map, "drop", null);
  };
}
