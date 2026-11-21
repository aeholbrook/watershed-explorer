const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

export function km(v: number): string {
  if (v < 1) return `${nf0.format(v * 1000)} m`;
  if (v < 10) return `${nf1.format(v)} km`;
  return `${nf0.format(v)} km`;
}

export function metres(v: number): string {
  return v >= 1000 ? km(v / 1000) : `${nf0.format(v)} m`;
}

export function area(km2: number | null | undefined): string {
  if (km2 == null) return "";
  if (km2 < 0.01) return `${nf0.format(km2 * 1e6)} m²`;
  if (km2 < 1) return `${nf2.format(km2)} km² (${nf0.format(km2 * 247.105)} acres)`;
  if (km2 < 100) return `${nf1.format(km2)} km²`;
  return `${nf0.format(km2)} km²`;
}

export function duration(hours: number): string {
  if (hours < 1 / 60) return "under a minute";
  if (hours < 1) return `${nf0.format(hours * 60)} min`;
  if (hours < 48) return `${nf1.format(hours)} h`;
  return `${nf1.format(hours / 24)} days`;
}

export function elev(m: number): string {
  // Sea-level outlets come back as e.g. -0.07 m; don't print "-0 m".
  return `${nf0.format(Math.abs(m) < 0.5 ? 0 : m)} m`;
}

export function flow(cms: number): string {
  if (cms < 1) return `${nf2.format(cms)} m³/s`;
  return `${nf0.format(cms)} m³/s`;
}

export const fmt = { nf0, nf1, nf2 };
