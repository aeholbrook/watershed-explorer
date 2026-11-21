import { Map as MapLibreMap, NavigationControl, ScaleControl, setWorkerUrl } from "maplibre-gl";
import type { ExpressionSpecification, GeoJSONSource } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { LEVEL_ORDER, THEMES, type Mode, type Theme } from "../palette";

// MapLibre 6 finds its worker next to its own module, but Vite pre-bundles the
// library into .vite/deps where that file doesn't exist. Point it at the real file.
setWorkerUrl(workerUrl);

export const BASE_STYLE = "https://tiles.openfreemap.org/styles/positron";
const TERRARIUM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";
const TNM = "https://basemap.nationalmap.gov/arcgis/rest/services";
const WBD_EXPORT =
  "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer/export" +
  "?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=512,512&format=png32&transparent=true&dpi=96&f=image";

const EMPTY = { type: "FeatureCollection" as const, features: [] };
// Initial paints are the light theme's; addLayers() ends with applyTheme() for the actual mode.
const L = THEMES.light;
const { water: WATER, waterEmphasis: WATER_DARK, overland: OVERLAND, eco: ECO, critical: CRITICAL, ink: INK } = L;
const REGION_COLORS = L.regions;
const FONT_BOLD = ["Noto Sans Bold"];
const FONT_ITALIC = ["Noto Sans Italic"];

export function createMap(container: HTMLElement, center: [number, number], zoom: number): MapLibreMap {
  const map = new MapLibreMap({
    container,
    style: BASE_STYLE,
    center,
    zoom,
    maxPitch: 80,
    attributionControl: { compact: true },
    canvasContextAttributes: { antialias: true, preserveDrawingBuffer: true },
  });
  map.addControl(new NavigationControl({ visualizePitch: true }), "bottom-right");
  map.addControl(new ScaleControl({ unit: "metric" }), "bottom-left");
  return map;
}

/** Add every source and layer once, empty; React effects only swap data and visibility. */
export function addLayers(map: MapLibreMap, mode: Mode) {
  snapshotBasemap(map);
  const firstSymbol = map.getStyle().layers.find((l) => l.type === "symbol")?.id;

  map.addSource("terrain-dem", {
    type: "raster-dem", tiles: [TERRARIUM], encoding: "terrarium", tileSize: 256, maxzoom: 15,
    attribution: "Terrain: AWS Terrain Tiles (USGS 3DEP, SRTM)",
  });
  map.addSource("hillshade-dem", {
    type: "raster-dem", tiles: [TERRARIUM], encoding: "terrarium", tileSize: 256, maxzoom: 15,
  });
  map.addSource("usgs-imagery", {
    type: "raster", tiles: [`${TNM}/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}`], tileSize: 256, maxzoom: 16,
    attribution: "Imagery: USGS The National Map",
  });
  map.addSource("usgs-topo", {
    type: "raster", tiles: [`${TNM}/USGSTopo/MapServer/tile/{z}/{y}/{x}`], tileSize: 256, maxzoom: 16,
    attribution: "Topo: USGS The National Map",
  });
  map.addSource("wbd-overlay", {
    type: "raster", tiles: [WBD_EXPORT], tileSize: 512,
    attribution: "Watershed Boundary Dataset: USGS",
  });
  for (const id of ["context", "context-labels", "basin", "units", "unit-labels", "reaches",
    "nldi-path", "lidar-path", "ponds", "points", "cursor", "drop", "sinkholes", "wells", "ghost",
    "eco", "impaired", "facility", "roof"]) {
    map.addSource(id, { type: "geojson", data: EMPTY, promoteId: id === "context" ? "code" : undefined });
  }
  map.addSource("route", { type: "geojson", data: EMPTY, lineMetrics: true });
  map.addSource("national", { type: "geojson", data: EMPTY, promoteId: "code", tolerance: 0.6 });
  map.addSource("national-labels", { type: "geojson", data: EMPTY });
  map.addSource("koppen", { type: "raster", tiles: ["data:,"], tileSize: 256, maxzoom: 7, bounds: [-170, 15, -52, 72],
    attribution: "Köppen-Geiger: Beck et al. (2023), CC BY 4.0" });

  const below = (layer: Parameters<MapLibreMap["addLayer"]>[0]) => map.addLayer(layer, firstSymbol);

  below({ id: "imagery", type: "raster", source: "usgs-imagery", layout: { visibility: "none" } });
  below({ id: "topo", type: "raster", source: "usgs-topo", layout: { visibility: "none" } });
  below({
    id: "hillshade", type: "hillshade", source: "hillshade-dem",
    paint: { "hillshade-exaggeration": 0.35, "hillshade-shadow-color": "#5b5a55", "hillshade-highlight-color": "#ffffff" },
  });
  below({ id: "wbd-overlay", type: "raster", source: "wbd-overlay", layout: { visibility: "none" }, paint: { "raster-opacity": 0.85 } });

  // Climate zones (static tiles; nearest-neighbour so zone edges stay crisp).
  below({
    id: "koppen", type: "raster", source: "koppen", layout: { visibility: "none" },
    paint: { "raster-opacity": 0.62, "raster-resampling": "nearest" },
  });

  // The whole country coloured by watershed (static files, HUC2–HUC8).
  below({
    id: "national-fill", type: "fill", source: "national", layout: { visibility: "none" },
    paint: {
      "fill-color": ["match", ["get", "c"], 0, REGION_COLORS[0], 1, REGION_COLORS[1], 2, REGION_COLORS[2],
        3, REGION_COLORS[3], REGION_COLORS[4]],
      "fill-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 0.58, 0.32],
    },
  });
  // Dark mode draws the rare fifth region colour as a 45° hatch (see palette.ts regions).
  map.addImage("region-hatch", hatch(L.levels.huc8), { pixelRatio: 2 });
  below({
    id: "national-hatch", type: "fill", source: "national", filter: ["==", ["get", "c"], 4],
    layout: { visibility: "none" }, paint: { "fill-pattern": "region-hatch", "fill-opacity": 0 },
  });
  below({
    id: "national-line", type: "line", source: "national", layout: { visibility: "none", "line-join": "round" },
    paint: { "line-color": "#ffffff", "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.8, 8, 2], "line-opacity": 0.95 },
  });

  // Every unit of the explored level in view, with names; hover lights one up.
  below({
    id: "context-fill", type: "fill", source: "context",
    paint: {
      "fill-color": levelColor(L),
      "fill-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 0.16, 0.0],
    },
  });
  below({
    id: "context-line", type: "line", source: "context",
    paint: { "line-color": levelColor(L), "line-width": 1.2, "line-opacity": 0.9 },
  });

  // Everything upstream of a chosen point on the route.
  below({ id: "basin-fill", type: "fill", source: "basin", paint: { "fill-color": WATER, "fill-opacity": 0.13 } });
  below({ id: "basin-line", type: "line", source: "basin", paint: { "line-color": WATER_DARK, "line-width": 2, "line-opacity": 0.9 } });

  // The nested watersheds around the site.
  below({
    id: "unit-fill", type: "fill", source: "units",
    filter: ["==", ["get", "selected"], true],
    paint: { "fill-color": levelColor(L), "fill-opacity": 0.1 },
  });
  below({
    id: "unit-casing", type: "line", source: "units",
    layout: { "line-join": "round" },
    paint: {
      "line-color": "#ffffff",
      "line-width": ["+", ["get", "width"], 2.5],
      "line-opacity": ["case", ["get", "emphasis"], 0.9, 0.5],
    },
  });
  below({
    id: "unit-line", type: "line", source: "units",
    layout: { "line-join": "round" },
    paint: {
      "line-color": levelColor(L),
      "line-width": ["case", ["get", "emphasis"], ["+", ["get", "width"], 1.5], ["get", "width"]],
      "line-opacity": ["case", ["get", "emphasis"], 1, 0.75],
    },
  });

  // Ecoregions (Place tab): Level III dashed, Level IV solid.
  below({
    id: "eco-line", type: "line", source: "eco",
    layout: { "line-join": "round", visibility: "none" },
    paint: {
      "line-color": ECO,
      "line-width": ["match", ["get", "level"], "III", 2.5, 1.8],
      "line-dasharray": ["match", ["get", "level"], "III", ["literal", [3, 2]], ["literal", [1, 0]]],
      "line-opacity": 0.9,
    },
  });

  // Impaired waters (Flows tab).
  below({
    id: "impaired-line", type: "line", source: "impaired",
    filter: ["==", ["get", "impaired"], true],
    layout: { "line-join": "round", "line-cap": "round", visibility: "none" },
    paint: { "line-color": CRITICAL, "line-width": 4, "line-opacity": 0.8 },
  });

  // Roof footprint (Restore tab).
  below({ id: "roof-fill", type: "fill", source: "roof", layout: { visibility: "none" }, paint: { "fill-color": OVERLAND, "fill-opacity": 0.25 } });
  below({ id: "roof-line", type: "line", source: "roof", layout: { visibility: "none" }, paint: { "line-color": OVERLAND, "line-width": 2.5 } });

  // Ghost streams: channels the terrain implies but no map shows. Bigger drainage area, bolder line.
  below({
    id: "ghost-streams", type: "line", source: "ghost",
    layout: { "line-join": "round", "line-cap": "round", visibility: "none" },
    paint: {
      "line-color": WATER,
      "line-width": ["interpolate", ["linear"], ["get", "area_ha"], 6, 1.5, 40, 3, 300, 5.5],
      "line-opacity": ["interpolate", ["linear"], ["get", "area_ha"], 6, 0.45, 40, 0.95],
      "line-dasharray": [2, 1.4],
    },
  });

  // Downstream route: width follows Strahler stream order.
  const orderWidth: ExpressionSpecification = ["interpolate", ["linear"], ["get", "order"], 1, 2.5, 4, 3.5, 7, 5, 10, 7];
  below({
    id: "reaches-casing", type: "line", source: "reaches",
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": "#ffffff", "line-width": ["+", orderWidth, 3], "line-opacity": 0.85 },
  });
  below({
    id: "reaches-line", type: "line", source: "reaches",
    layout: { "line-join": "round", "line-cap": "round" },
    paint: {
      "line-color": ["case", ["boolean", ["get", "focus"], false], WATER_DARK, WATER],
      "line-width": orderWidth,
      // Unmapped underground stretches the app bridged in a straight line.
      "line-dasharray": ["case", ["boolean", ["get", "gap"], false], ["literal", [1.5, 1.5]], ["literal", [1, 0]]],
    },
  });
  below({
    id: "reaches-flow", type: "line", source: "reaches",
    layout: { "line-join": "round" },
    paint: { "line-color": "#ffffff", "line-width": ["*", orderWidth, 0.35], "line-opacity": 0.85, "line-dasharray": [0, 4, 3] },
  });

  // Trail drawn behind the raindrop while riding.
  below({
    id: "route-trail", type: "line", source: "route",
    layout: { "line-join": "round", "line-cap": "round", visibility: "none" },
    paint: { "line-width": 6, "line-gradient": ["step", ["line-progress"], OVERLAND, 0, "rgba(0,0,0,0)"] },
  });

  below({
    id: "nldi-path", type: "line", source: "nldi-path",
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": OVERLAND, "line-width": 2, "line-opacity": 0.8, "line-dasharray": [1.5, 1.5] },
  });
  below({
    id: "lidar-casing", type: "line", source: "lidar-path",
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": "#ffffff", "line-width": 6.5 },
  });
  below({
    id: "lidar-line", type: "line", source: "lidar-path",
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": OVERLAND, "line-width": 3.5 },
  });
  below({
    id: "lidar-flow", type: "line", source: "lidar-path",
    layout: { "line-join": "round" },
    paint: { "line-color": "#ffffff", "line-width": 1.4, "line-opacity": 0.9, "line-dasharray": [0, 4, 3] },
  });
  below({
    id: "ponds", type: "circle", source: "ponds",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["get", "depth_m"], 0.3, 4, 2, 8],
      "circle-color": WATER, "circle-opacity": 0.85,
      "circle-stroke-color": "#ffffff", "circle-stroke-width": 2,
    },
  });

  below({
    id: "sinkholes", type: "circle", source: "sinkholes",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 3, 16, 7],
      "circle-color": INK.secondary, "circle-opacity": 0.15,
      "circle-stroke-color": INK.secondary, "circle-stroke-width": 1.5,
    },
  });

  // Labels go on top of the base map's own labels.
  map.addLayer({
    id: "national-labels", type: "symbol", source: "national-labels", layout: {
      visibility: "none", "text-field": ["get", "name"], "text-font": FONT_BOLD,
      "text-size": ["interpolate", ["linear"], ["zoom"], 3, 10, 8, 14], "text-max-width": 8, "text-padding": 4,
    },
    paint: { "text-color": INK.primary, "text-halo-color": "#ffffff", "text-halo-width": 1.8 },
  });
  map.addLayer({
    id: "context-labels", type: "symbol", source: "context-labels",
    layout: {
      "text-field": ["get", "name"], "text-font": FONT_ITALIC, "text-size": 12,
      "text-max-width": 9, "text-padding": 6,
    },
    paint: { "text-color": INK.secondary, "text-halo-color": "#ffffff", "text-halo-width": 1.6 },
  });
  map.addLayer({
    id: "unit-labels", type: "symbol", source: "unit-labels",
    filter: ["==", ["get", "selected"], true],
    layout: {
      "text-field": ["format", ["get", "name"], {}, "\n", {}, ["get", "kind"], { "font-scale": 0.8 }],
      "text-font": FONT_BOLD, "text-size": 14, "text-max-width": 10, "text-allow-overlap": false,
    },
    paint: { "text-color": INK.primary, "text-halo-color": "#ffffff", "text-halo-width": 2 },
  });
  map.addLayer({
    id: "reach-labels", type: "symbol", source: "reaches",
    layout: {
      "symbol-placement": "line", "text-field": ["get", "name"], "text-font": FONT_ITALIC,
      "text-size": 13, "symbol-spacing": 400, "text-max-angle": 30,
    },
    paint: { "text-color": WATER_DARK, "text-halo-color": "#ffffff", "text-halo-width": 2 },
  });
  map.addLayer({
    id: "points", type: "circle", source: "points",
    paint: {
      "circle-radius": 7, "circle-color": pointColor(L),
      "circle-stroke-color": "#ffffff", "circle-stroke-width": 2.5,
    },
  });
  map.addLayer({
    id: "point-labels", type: "symbol", source: "points",
    layout: {
      "text-field": ["get", "label"], "text-font": FONT_BOLD, "text-size": 12,
      "text-offset": [0, 1.3], "text-anchor": "top", "text-max-width": 12,
    },
    paint: { "text-color": INK.primary, "text-halo-color": "#ffffff", "text-halo-width": 2 },
  });
  map.addLayer({
    id: "wells", type: "circle", source: "wells",
    paint: { "circle-radius": 5, "circle-color": WATER_DARK, "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 },
  });
  map.addLayer({
    id: "well-labels", type: "symbol", source: "wells", minzoom: 9,
    layout: { "text-field": ["get", "label"], "text-font": FONT_ITALIC, "text-size": 11, "text-offset": [0, 1.1], "text-anchor": "top" },
    paint: { "text-color": WATER_DARK, "text-halo-color": "#ffffff", "text-halo-width": 1.6 },
  });
  map.addLayer({
    id: "facility", type: "symbol", source: "facility",
    layout: {
      visibility: "none", "text-field": ["concat", "■  ", ["get", "label"]], "text-font": FONT_BOLD,
      "text-size": 13, "text-anchor": "left", "text-offset": [-0.45, 0], "text-allow-overlap": true,
    },
    paint: { "text-color": INK.primary, "text-halo-color": "#ffffff", "text-halo-width": 2 },
  });
  map.addLayer({
    id: "cursor", type: "circle", source: "cursor",
    paint: { "circle-radius": 6, "circle-color": INK.primary, "circle-stroke-color": "#ffffff", "circle-stroke-width": 2.5 },
  });
  map.addLayer({
    id: "drop-glow", type: "circle", source: "drop",
    paint: { "circle-radius": 18, "circle-color": WATER, "circle-opacity": 0.25, "circle-blur": 0.6 },
  });
  map.addLayer({
    id: "drop", type: "circle", source: "drop",
    paint: { "circle-radius": 7, "circle-color": WATER, "circle-stroke-color": "#ffffff", "circle-stroke-width": 3 },
  });

  applyTheme(map, mode);
}

export function setData(map: MapLibreMap, id: string, data: GeoJSON.GeoJSON | null) {
  const src = map.getSource(id) as GeoJSONSource | undefined;
  src?.setData((data ?? EMPTY) as GeoJSON.GeoJSON);
}

/** Unit and context features carry their level; the colour comes from the theme's ramp. */
function levelColor(T: Theme): ExpressionSpecification {
  return ["match", ["get", "level"], ...LEVEL_ORDER.flatMap((k) => [k, T.levels[k]]), T.levels.huc12] as unknown as ExpressionSpecification;
}

/** Route points: where the water enters a stream, and where it reaches the sea. */
function pointColor(T: Theme): ExpressionSpecification {
  return ["match", ["get", "kind"], "outlet", T.waterEmphasis, T.water];
}

/** A 45° hatch tile (drawn at 2x), the texture channel for the fifth region class. */
function hatch(color: string): ImageData {
  const n = 16;
  const c = document.createElement("canvas");
  c.width = c.height = n;
  const g = c.getContext("2d")!;
  g.strokeStyle = color;
  g.lineWidth = 3;
  g.beginPath();
  for (const o of [-n, 0, n]) { g.moveTo(o, n); g.lineTo(o + n, 0); }
  g.stroke();
  return g.getImageData(0, 0, n, n);
}

/** Recolour every layer this app adds for a theme. */
export function applyTheme(map: MapLibreMap, mode: Mode) {
  const T = THEMES[mode];
  const set = (id: string, prop: string, v: unknown) => {
    if (map.getLayer(id)) map.setPaintProperty(id, prop as never, v as never);
  };
  set("hillshade", "hillshade-shadow-color", T.hillshade.shadow);
  set("hillshade", "hillshade-highlight-color", T.hillshade.highlight);
  set("national-fill", "fill-color", ["match", ["get", "c"], 0, T.regions[0], 1, T.regions[1], 2, T.regions[2], 3, T.regions[3], T.regions[4]]);
  if (T.regionHatch) map.updateImage("region-hatch", hatch(T.regionHatch));
  set("national-hatch", "fill-opacity", T.regionHatch ? 0.9 : 0);
  for (const id of ["context-fill", "context-line", "unit-fill", "unit-line"]) {
    set(id, id.endsWith("fill") ? "fill-color" : "line-color", levelColor(T));
  }
  set("basin-fill", "fill-color", T.water);
  set("basin-line", "line-color", T.waterEmphasis);
  set("eco-line", "line-color", T.eco);
  set("impaired-line", "line-color", T.critical);
  set("roof-fill", "fill-color", T.overland);
  set("roof-line", "line-color", T.overland);
  set("ghost-streams", "line-color", T.water);
  set("reaches-line", "line-color", ["case", ["boolean", ["get", "focus"], false], T.waterEmphasis, T.water]);
  set("route-trail", "line-gradient", ["step", ["line-progress"], T.overland, 0, "rgba(0,0,0,0)"]);
  set("nldi-path", "line-color", T.overland);
  set("lidar-line", "line-color", T.overland);
  set("ponds", "circle-color", T.water);
  set("sinkholes", "circle-color", T.ink.secondary);
  set("sinkholes", "circle-stroke-color", T.ink.secondary);
  set("points", "circle-color", pointColor(T));
  set("wells", "circle-color", T.waterEmphasis);
  set("well-labels", "text-color", T.waterEmphasis);
  set("reach-labels", "text-color", T.waterEmphasis);
  set("context-labels", "text-color", T.ink.secondary);
  for (const id of ["national-labels", "unit-labels", "point-labels", "facility"]) set(id, "text-color", T.ink.primary);
  set("cursor", "circle-color", T.ink.primary);
  set("drop", "circle-color", T.water);
  set("drop-glow", "circle-color", T.water);
  // Casings, rings and halos are the surface colour, so marks lift off the map in either mode.
  for (const id of ["unit-casing", "reaches-casing", "lidar-casing"]) set(id, "line-color", T.halo);
  for (const id of ["ponds", "points", "wells", "cursor", "drop"]) set(id, "circle-stroke-color", T.halo);
  for (const id of ["national-labels", "context-labels", "unit-labels", "reach-labels", "point-labels", "well-labels", "facility"]) {
    set(id, "text-halo-color", T.halo);
  }
  recolourBasemap(map, mode);
}

export function sky(mode: Mode) {
  const s = THEMES[mode].sky;
  return { ...SKY, "sky-color": s.sky, "horizon-color": s.horizon, "fog-color": s.fog };
}

// ------------------------------------------------------------------ basemap in dark mode
// OpenFreeMap's Positron is nearly greyscale, so dark mode inverts the lightness of its own
// paint colours in place (hue and saturation kept). Our sources and drawn data stay put,
// which swapping in a second style would not do; light mode restores the originals.

const baseColours = new WeakMap<MapLibreMap, { id: string; prop: string; value: unknown }[]>();

function snapshotBasemap(map: MapLibreMap) {
  const out: { id: string; prop: string; value: unknown }[] = [];
  for (const layer of map.getStyle().layers) {
    for (const [prop, value] of Object.entries((layer as { paint?: Record<string, unknown> }).paint ?? {})) {
      if (prop.endsWith("-color")) out.push({ id: layer.id, prop, value });
    }
  }
  baseColours.set(map, out);
}

function recolourBasemap(map: MapLibreMap, mode: Mode) {
  for (const { id, prop, value } of baseColours.get(map) ?? []) {
    if (map.getLayer(id)) map.setPaintProperty(id, prop as never, (mode === "dark" ? invertColours(value) : value) as never);
  }
}

function invertColours(v: unknown): unknown {
  if (typeof v === "string") return invertColour(v) ?? v;
  if (Array.isArray(v)) return v.map(invertColours);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, invertColours(x)]));
  return v;
}

function invertColour(s: string): string | null {
  const rgba = parseColour(s.trim());
  if (!rgba) return null;
  const [h, sat, l] = rgbToHsl(rgba[0], rgba[1], rgba[2]);
  const [r, g, b] = hslToRgb(h, sat, 0.08 + (1 - l) * 0.82);
  return `rgba(${Math.round(r)},${Math.round(g)},${Math.round(b)},${rgba[3]})`;
}

function parseColour(s: string): [number, number, number, number] | null {
  let m = s.match(/^#([0-9a-f]{3,8})$/i);
  if (m) {
    let h = m[1];
    if (h.length <= 4) h = [...h].map((c) => c + c).join("");
    const n = (i: number) => parseInt(h.slice(i, i + 2), 16);
    return [n(0), n(2), n(4), h.length === 8 ? n(6) / 255 : 1];
  }
  m = s.match(/^(rgba?|hsla?)\(([^)]*)\)$/i);
  if (!m) return null;
  const parts = m[2].split(/[\s,/]+/).filter(Boolean).map((x) => parseFloat(x));
  if (parts.length < 3 || parts.some(Number.isNaN)) return null;
  const a = parts[3] ?? 1;
  if (m[1].toLowerCase().startsWith("rgb")) return [parts[0], parts[1], parts[2], a];
  const [r, g, b] = hslToRgb(parts[0] / 360, parts[1] / 100, parts[2] / 100);
  return [r, g, b, a];
}

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [h / 6, s, l];
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  if (s === 0) return [l * 255, l * 255, l * 255];
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
  const f = (t: number) => {
    t = (t + 1) % 1;
    return 255 * (t < 1 / 6 ? p + (q - p) * 6 * t : t < 1 / 2 ? q : t < 2 / 3 ? p + (q - p) * (2 / 3 - t) * 6 : p);
  };
  return [f(h + 1 / 3), f(h), f(h - 1 / 3)];
}

/** Marching dashes along the river and overland path, so the flow direction reads at a glance. */
const DASH_STEPS: number[][] = [
  [0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5], [2, 4, 1], [2.5, 4, 0.5], [3, 4, 0],
  [0, 0.5, 3, 3.5], [0, 1, 3, 3], [0, 1.5, 3, 2.5], [0, 2, 3, 2], [0, 2.5, 3, 1.5], [0, 3, 3, 1], [0, 3.5, 3, 0.5],
];

export function animateFlow(map: MapLibreMap): () => void {
  let step = 0;
  let last = 0;
  let raf = 0;
  const tick = (t: number) => {
    if (t - last > 70) {
      last = t;
      step = (step + 1) % DASH_STEPS.length;
      for (const id of ["reaches-flow", "lidar-flow"]) {
        if (map.getLayer(id)) map.setPaintProperty(id, "line-dasharray", DASH_STEPS[step]);
      }
    }
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
  return () => cancelAnimationFrame(raf);
}

export const SKY = {
  "sky-color": "#a9cdf2",
  "horizon-color": "#e8eef4",
  "fog-color": "#f2f4f6",
  "sky-horizon-blend": 0.6,
  "horizon-fog-blend": 0.6,
  "fog-ground-blend": 0.35,
  "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 10, 1, 12, 0],
} as const;

/** Layers that belong to one sidebar tab: shown only while that tab is open. */
export const TAB_LAYERS: Record<string, string[]> = {
  place: ["eco-line"],
  flows: ["impaired-line", "facility"],
  restore: ["ghost-streams", "roof-fill", "roof-line"],
};

export type OverlaySpec = { tiles: string; attribution: string } | null;

/** Swap the single historical/vegetation raster overlay, drawn under all the hydrology. */
export function setOverlay(map: MapLibreMap, spec: OverlaySpec, opacity: number) {
  if (map.getLayer("overlay")) map.removeLayer("overlay");
  if (map.getSource("overlay")) map.removeSource("overlay");
  if (!spec) return;
  map.addSource("overlay", { type: "raster", tiles: [spec.tiles], tileSize: 256, attribution: spec.attribution });
  map.addLayer({ id: "overlay", type: "raster", source: "overlay", paint: { "raster-opacity": opacity } }, "context-fill");
}
