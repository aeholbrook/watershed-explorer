/**
 * Köppen-Geiger climate zones from static tiles (Beck et al. 2023, CC BY 4.0).
 *
 * The tiles are paletted PNGs whose colours are unique per class, so a zone can be read
 * at any point by decoding the tile under it: no API, works on any static host.
 */

export type KoppenClass = { value: number; code: string; name: string; color: string };

export const KOPPEN_PERIODS = [
  { id: "1901_1930", label: "1901–1930" },
  { id: "1931_1960", label: "1931–1960" },
  { id: "1961_1990", label: "1961–1990" },
  { id: "1991_2020", label: "1991–2020" },
  { id: "2041_2070", label: "2041–2070", future: true },
  { id: "2071_2099", label: "2071–2099", future: true },
] as const;

export const SCENARIOS = [
  { id: "ssp126", label: "Low", long: "SSP1-2.6, low emissions" },
  { id: "ssp245", label: "Middle", long: "SSP2-4.5, intermediate emissions" },
  { id: "ssp585", label: "High", long: "SSP5-8.5, very high emissions" },
] as const;

export type ScenarioId = (typeof SCENARIOS)[number]["id"];

export const KOPPEN_MAX_ZOOM = 7;
const BASE = `${import.meta.env.BASE_URL}data/koppen`;

export function periodPath(periodIndex: number, scenario: ScenarioId): string {
  const p = KOPPEN_PERIODS[periodIndex];
  return "future" in p && p.future ? `${p.id}/${scenario}` : p.id;
}

export function tileUrl(path: string): string {
  return `${location.origin}${BASE}/${path}/{z}/{x}/{y}.png`;
}

let legendPromise: Promise<KoppenClass[]> | null = null;
export function koppenLegend(): Promise<KoppenClass[]> {
  legendPromise ??= fetch(`${BASE}/legend.json`).then((r) => r.json());
  return legendPromise;
}

const byColor = new Map<string, KoppenClass>();
async function classForRGB(r: number, g: number, b: number) {
  if (!byColor.size) for (const k of await koppenLegend()) byColor.set(k.color, k);
  return byColor.get("#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")) ?? null;
}

const tiles = new Map<string, Promise<ImageData | null>>();
function tile(path: string, z: number, x: number, y: number): Promise<ImageData | null> {
  const key = `${path}/${z}/${x}/${y}`;
  let p = tiles.get(key);
  if (!p) {
    p = fetch(`${BASE}/${key}.png`)
      .then(async (r) => {
        if (!r.ok || !(r.headers.get("content-type") ?? "").startsWith("image/")) return null;
        // Keep the exact palette colours: no colour management, no premultiplication.
        const bmp = await createImageBitmap(await r.blob(), { colorSpaceConversion: "none", premultiplyAlpha: "none" });
        const c = new OffscreenCanvas(256, 256);
        const ctx = c.getContext("2d", { willReadFrequently: true })!;
        ctx.drawImage(bmp, 0, 0);
        return ctx.getImageData(0, 0, 256, 256);
      })
      .catch(() => null);
    tiles.set(key, p);
    if (tiles.size > 400) tiles.delete(tiles.keys().next().value!);
  }
  return p;
}

function project(lon: number, lat: number, z: number) {
  const n = 2 ** z;
  const x = ((lon + 180) / 360) * n;
  const s = Math.sin((lat * Math.PI) / 180);
  const y = (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * n;
  return { tx: Math.floor(x), ty: Math.floor(y), px: Math.floor((x % 1) * 256), py: Math.floor((y % 1) * 256) };
}

/** The climate zone at a point, from the most detailed tile. */
export async function classAt(path: string, lon: number, lat: number): Promise<KoppenClass | null> {
  const { tx, ty, px, py } = project(lon, lat, KOPPEN_MAX_ZOOM);
  const img = await tile(path, KOPPEN_MAX_ZOOM, tx, ty);
  if (!img) return null;
  const i = (py * 256 + px) * 4;
  if (img.data[i + 3] === 0) return null;
  return classForRGB(img.data[i], img.data[i + 1], img.data[i + 2]);
}

/** Share of each zone across the tiles covering a view. */
export async function classesInView(path: string, bounds: [number, number, number, number], zoom: number) {
  const z = Math.max(0, Math.min(KOPPEN_MAX_ZOOM, Math.floor(zoom)));
  const a = project(bounds[0], bounds[3], z);
  const b = project(bounds[2], bounds[1], z);
  const counts = new Map<string, number>();
  const jobs: Promise<void>[] = [];
  for (let x = a.tx; x <= b.tx && jobs.length < 64; x++) {
    for (let y = a.ty; y <= b.ty && jobs.length < 64; y++) {
      jobs.push(tile(path, z, x, y).then((img) => {
        if (!img) return;
        const d = img.data;
        for (let i = 0; i < d.length; i += 16) {          // every 4th pixel is plenty
          if (d[i + 3] === 0) continue;
          const k = `${d[i]},${d[i + 1]},${d[i + 2]}`;
          counts.set(k, (counts.get(k) ?? 0) + 1);
        }
      }));
    }
  }
  await Promise.all(jobs);
  const total = [...counts.values()].reduce((s, v) => s + v, 0) || 1;
  const out: (KoppenClass & { share: number })[] = [];
  for (const [k, n] of counts) {
    const [r, g, bl] = k.split(",").map(Number);
    const c = await classForRGB(r, g, bl);
    if (c) out.push({ ...c, share: n / total });
  }
  return out.filter((c) => c.share >= 0.005).sort((p, q) => q.share - p.share);
}
