import { useEffect, useRef, useState } from "react";
import { Marker, Popup } from "maplibre-gl";
import type { Map as MapLibreMap, MapGeoJSONFeature, RasterTileSource } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { api } from "../api";
import { area } from "../format";
import { LEVEL_COLOR, LEVEL_NAME, LEVEL_ORDER, LEVEL_WIDTH, WATER, WATER_DARK } from "../palette";
import type {
  Basemap, Basin, BelowGround, Downstream, FlowsData, GhostStreams, LevelKey, PlaceData, RestoreData, LidarTrace, LngLat, Raindrop, Unit,
} from "../types";
import { SKY, TAB_LAYERS, addLayers, animateFlow, createMap, setData, setOverlay } from "./setup";
import { classAt, classesInView, tileUrl, type KoppenClass } from "./koppen";
import type { Overlay } from "../components/RestoreTab";

export type ContextStatus = { loading: boolean; count: number; tooFarOut: boolean; minZoom?: number };

type Props = {
  site: LngLat | null;
  onPick: (p: LngLat) => void;
  units: Unit[];
  selected: LevelKey | null;
  hovered: LevelKey | null;
  raindrop: Raindrop | null;
  lidar: LidarTrace | null;
  downstream: Downstream | null;
  below: BelowGround | null;
  ghost: GhostStreams | null;
  tab: string;
  placeData: PlaceData | null;
  flowsData: FlowsData | null;
  restoreData: RestoreData | null;
  overlay: Overlay;
  overlayOpacity: number;
  focusSeg: number | null;
  basin: Basin | null;
  explore: LevelKey | null;
  view: "2d" | "3d";
  basemap: Basemap;
  exaggeration: number;
  wbdOverlay: boolean;
  cursor: [number, number] | null;
  riding: boolean;
  onReady: (map: MapLibreMap) => void;
  onContextStatus: (s: ContextStatus) => void;
  koppenPath: string | null;
  onExplore: (level: LevelKey | null) => void;
  onKoppenView: (classes: (KoppenClass & { share: number })[]) => void;
};

const NATIONAL: LevelKey[] = ["huc2", "huc4", "huc6", "huc8"];
const FINER: Partial<Record<LevelKey, LevelKey>> = { huc2: "huc4", huc4: "huc6", huc6: "huc8", huc8: "huc10" };
const nationalData = new Map<string, Promise<GeoJSON.FeatureCollection>>();
function loadNational(level: string) {
  let pr = nationalData.get(level);
  if (!pr) {
    pr = fetch(`${import.meta.env.BASE_URL}data/wbd/${level}.geojson`).then((r) => r.json());
    nationalData.set(level, pr);
  }
  return pr;
}

function bboxOf(g: GeoJSON.Geometry): [number, number, number, number] {
  const b: [number, number, number, number] = [180, 90, -180, -90];
  const walk = (c: unknown): void => {
    if (typeof (c as number[])[0] === "number") {
      const [x, y] = c as number[];
      b[0] = Math.min(b[0], x); b[1] = Math.min(b[1], y); b[2] = Math.max(b[2], x); b[3] = Math.max(b[3], y);
    } else (c as unknown[]).forEach(walk);
  };
  walk((g as GeoJSON.Polygon).coordinates);
  return b;
}

export function MapView(p: Props) {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const marker = useRef<Marker | null>(null);
  const [ready, setReady] = useState(false);
  const latest = useRef(p);
  latest.current = p;

  // ------------------------------------------------------------------ create once
  useEffect(() => {
    const map = createMap(el.current!, [-96, 38.5], 3.6);
    mapRef.current = map;
    let stopFlow = () => {};
    map.on("load", () => {
      addLayers(map);
      stopFlow = animateFlow(map);
      setReady(true);
      latest.current.onReady(map);
    });
    // One readout for whatever is under the cursor: watershed unit and/or climate zone.
    const popup = new Popup({ closeButton: false, closeOnClick: false, className: "hover-popup", offset: 12, maxWidth: "300px" });
    let hover: { source: string; id: string | number } | null = null;
    let ticket = 0;
    const setHover = (f: MapGeoJSONFeature | null) => {
      if (hover && (!f || f.id !== hover.id || f.source !== hover.source)) {
        map.setFeatureState(hover, { hover: false });
        hover = null;
      }
      if (f && f.id !== undefined && !hover) {
        hover = { source: f.source, id: f.id };
        map.setFeatureState(hover, { hover: true });
      }
    };
    const unitAt = (point: { x: number; y: number }) => {
      const layers = ["national-fill", "context-fill"].filter((id) => map.getLayoutProperty(id, "visibility") !== "none");
      return layers.length ? map.queryRenderedFeatures([point.x, point.y], { layers })[0] ?? null : null;
    };
    const unitHtml = (f: MapGeoJSONFeature) => {
      const pr = f.properties as { name?: string; code: string; areasqkm?: number; level?: LevelKey; states?: string };
      const level = pr.level ?? (`huc${pr.code.length}` as LevelKey);
      return `<div class="pop-kind">${LEVEL_NAME[level]}</div>` +
        `<div class="pop-name">${escapeHtml(pr.name ?? "Unnamed")}</div>` +
        `<div class="pop-meta">HUC ${pr.code} · ${area(pr.areasqkm)}${pr.states ? " · " + escapeHtml(pr.states.replace(/,/g, ", ")) : ""}</div>`;
    };
    map.on("mousemove", (e) => {
      if (latest.current.riding) return;
      const f = unitAt(e.point);
      setHover(f);
      const path = latest.current.koppenPath;
      const mine = ++ticket;
      const show = (k: KoppenClass | null) => {
        if (mine !== ticket) return;
        const parts = [];
        if (f) parts.push(unitHtml(f));
        if (k) parts.push(`<div class="pop-climate"><span class="pop-swatch" style="background:${k.color}"></span>` +
          `<strong>${k.code}</strong> ${escapeHtml(k.name)}</div>`);
        if (!parts.length) {
          popup.remove();
          map.getCanvas().style.cursor = "";
          return;
        }
        popup.setLngLat(e.lngLat).setHTML(parts.join("")).addTo(map);
        map.getCanvas().style.cursor = f ? "pointer" : "crosshair";
      };
      if (path) classAt(path, e.lngLat.lng, e.lngLat.lat).then(show);
      else show(null);
    });
    map.on("mouseout", () => { ticket++; setHover(null); popup.remove(); });

    // Clicking a coloured region opens its card; clicking anywhere else drops a raindrop.
    const card = new Popup({ closeButton: true, className: "region-card", offset: 8, maxWidth: "280px" });
    map.on("click", async (e) => {
      if (latest.current.riding) return;
      const f = unitAt(e.point);
      if (!f || f.source !== "national") {
        latest.current.onPick({ lon: e.lngLat.lng, lat: e.lngLat.lat });
        return;
      }
      const code = String((f.properties as { code: string }).code);
      const level = `huc${code.length}` as LevelKey;
      const full = (await loadNational(level)).features.find((x) => String(x.properties?.code) === code);
      const el = document.createElement("div");
      el.innerHTML = unitHtml(f);
      const actions = document.createElement("div");
      actions.className = "card-actions";
      const finer = FINER[level];
      if (finer && full) {
        const b = document.createElement("button");
        b.textContent = `Show its ${LEVEL_NAME[finer].split(" · ")[0].toLowerCase()}s`;
        b.onclick = () => {
          card.remove();
          latest.current.onExplore(finer);
          map.fitBounds(bboxOf(full.geometry), { padding: 40, duration: 1200 });
        };
        actions.appendChild(b);
      }
      const drop = document.createElement("button");
      drop.textContent = "Drop a raindrop here";
      drop.onclick = () => { card.remove(); latest.current.onPick({ lon: e.lngLat.lng, lat: e.lngLat.lat }); };
      actions.appendChild(drop);
      el.appendChild(actions);
      card.setLngLat(e.lngLat).setDOMContent(el).addTo(map);
    });

    return () => {
      stopFlow();
      map.remove();
    };
  }, []);

  const map = ready ? mapRef.current : null;

  // ------------------------------------------------------------------ site marker
  useEffect(() => {
    if (!map) return;
    if (!p.site) {
      marker.current?.remove();
      marker.current = null;
      return;
    }
    if (!marker.current) {
      const pin = document.createElement("div");
      pin.className = "site-pin";
      pin.title = "Drag onto your roof";
      pin.innerHTML =
        '<svg viewBox="0 0 32 42" width="32" height="42" aria-hidden="true">' +
        '<path d="M16 1C8 1 2 7 2 15c0 10 14 26 14 26s14-16 14-26C30 7 24 1 16 1z" fill="#0b0b0b" stroke="#fff" stroke-width="2"/>' +
        '<path d="M16 8c-3 4.5-5.5 7.2-5.5 10a5.5 5.5 0 0 0 11 0c0-2.8-2.5-5.5-5.5-10z" fill="#5ea2ee"/></svg>';
      marker.current = new Marker({ element: pin, draggable: true, anchor: "bottom" })
        .setLngLat([p.site.lon, p.site.lat])
        .addTo(map);
      marker.current.on("dragend", () => {
        const ll = marker.current!.getLngLat();
        latest.current.onPick({ lon: ll.lng, lat: ll.lat });
      });
    } else {
      marker.current.setLngLat([p.site.lon, p.site.lat]);
    }
  }, [map, p.site]);

  // ------------------------------------------------------------------ nested watersheds
  useEffect(() => {
    if (!map) return;
    const sorted = [...p.units].sort((a, b) => LEVEL_ORDER.indexOf(a.level) - LEVEL_ORDER.indexOf(b.level));
    const emphasis = p.hovered ?? p.selected;
    setData(map, "units", {
      type: "FeatureCollection",
      features: sorted.map((u) => ({
        type: "Feature",
        geometry: u.geometry,
        properties: {
          level: u.level,
          color: LEVEL_COLOR[u.level],
          width: LEVEL_WIDTH[u.level],
          selected: u.level === p.selected,
          emphasis: u.level === emphasis,
        },
      })),
    });
    setData(map, "unit-labels", {
      type: "FeatureCollection",
      features: sorted.map((u) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: u.label_at },
        properties: {
          name: u.name ?? "Unnamed",
          kind: LEVEL_NAME[u.level],
          selected: u.level === emphasis,
        },
      })),
    });
  }, [map, p.units, p.selected, p.hovered]);

  // ------------------------------------------------------------------ raindrop + route
  useEffect(() => {
    if (!map) return;
    setData(map, "nldi-path", p.raindrop ? { type: "Feature", properties: {}, geometry: p.raindrop.path } : null);
  }, [map, p.raindrop]);

  useEffect(() => {
    if (!map) return;
    setData(map, "lidar-path", p.lidar ? { type: "Feature", properties: {}, geometry: p.lidar.path } : null);
    setData(map, "ponds", p.lidar ? {
      type: "FeatureCollection",
      features: p.lidar.ponds.map((pd) => ({
        type: "Feature", geometry: { type: "Point", coordinates: [pd.lon, pd.lat] },
        properties: { depth_m: pd.depth_m, area_m2: pd.area_m2 },
      })),
    } : null);
  }, [map, p.lidar]);

  useEffect(() => {
    if (!map) return;
    const ds = p.downstream;
    if (!ds) {
      setData(map, "reaches", null);
      setData(map, "points", null);
      return;
    }
    setData(map, "reaches", {
      type: "FeatureCollection",
      features: ds.reaches.features.map((f) => ({
        ...f, properties: { ...f.properties, focus: f.properties.seg === p.focusSeg },
      })),
    });
    setData(map, "points", {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature", geometry: { type: "Point", coordinates: [ds.entry.lon, ds.entry.lat] },
          properties: { label: `Enters ${ds.entry.stream ?? "a stream"}`, color: WATER },
        },
        {
          type: "Feature", geometry: { type: "Point", coordinates: [ds.outlet.lon, ds.outlet.lat] },
          properties: { label: ds.outlet.name, color: WATER_DARK },
        },
      ],
    });
  }, [map, p.downstream, p.focusSeg]);

  useEffect(() => {
    if (!map) return;
    const b = p.below;
    setData(map, "sinkholes", b?.karst ? {
      type: "FeatureCollection",
      features: b.karst.sinkholes.map((k) => ({
        type: "Feature", geometry: { type: "Point", coordinates: [k.lon, k.lat] }, properties: {},
      })),
    } : null);
    setData(map, "wells", b ? {
      type: "FeatureCollection",
      features: b.wells.map((w) => ({
        type: "Feature", geometry: { type: "Point", coordinates: [w.lon, w.lat] },
        properties: { label: w.depth_to_water_m != null ? `Well: water ${w.depth_to_water_m.toFixed(1)} m down` : "Well" },
      })),
    } : null);
  }, [map, p.below]);

  // ------------------------------------------------------------------ bioregional tab layers
  useEffect(() => {
    if (!map) return;
    setData(map, "ghost", p.ghost?.lines ?? null);
    setData(map, "eco", p.placeData?.ecoregions?.shapes ?? null);
    setData(map, "impaired", p.flowsData?.impaired?.shapes ?? null);
    const plant = p.flowsData?.wastewater?.plant;
    setData(map, "facility", plant ? {
      type: "Feature", geometry: { type: "Point", coordinates: [plant.lon, plant.lat] },
      properties: { label: p.flowsData!.wastewater!.facility },
    } : null);
    setData(map, "roof", p.restoreData?.roof ? { type: "Feature", properties: {}, geometry: p.restoreData.roof.geometry } : null);
  }, [map, p.ghost, p.placeData, p.flowsData, p.restoreData]);

  useEffect(() => {
    if (!map) return;
    for (const [tab, ids] of Object.entries(TAB_LAYERS)) {
      for (const id of ids) map.setLayoutProperty(id, "visibility", tab === p.tab ? "visible" : "none");
    }
  }, [map, p.tab]);

  useEffect(() => {
    if (!map) return;
    const o = p.restoreData?.overlays;
    if (!o || !p.overlay) {
      setOverlay(map, null, 0);
      return;
    }
    if (p.overlay.kind === "topo") {
      if (!o.historic_topo) return setOverlay(map, null, 0);
      // Show, for each spot, the edition closest to the chosen year.
      const rule = { mosaicMethod: "esriMosaicAttribute", sortField: "Year", sortValue: String(p.overlay.year),
        ascending: true, where: "Map_Scale <= 62500" };
      setOverlay(map, {
        tiles: `${o.historic_topo}${o.historic_topo.includes("?") ? "&" : "?"}bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=256,256&format=png&f=image` +
          `&mosaicRule=${encodeURIComponent(JSON.stringify(rule))}`,
        attribution: "Historical topographic maps: USGS, via Esri",
      }, p.overlayOpacity);
    } else {
      // WMS overlays go through our API as cached tiles (see /api/tiles).
      setOverlay(map, {
        tiles: `${location.origin}${import.meta.env.BASE_URL}api/tiles/${p.overlay.kind}/{z}/{x}/{y}.png`,
        attribution: p.overlay.kind === "bps" ? "LANDFIRE (USGS/USFS)" : "NLCD 2025 (USGS/MRLC)",
      }, p.overlayOpacity);
    }
  }, [map, p.overlay, p.restoreData]);

  useEffect(() => {
    if (map?.getLayer("overlay")) map.setPaintProperty("overlay", "raster-opacity", p.overlayOpacity);
  }, [map, p.overlayOpacity]);

  useEffect(() => {
    if (!map) return;
    setData(map, "basin", p.basin ? { type: "Feature", properties: {}, geometry: p.basin.geometry } : null);
  }, [map, p.basin]);

  useEffect(() => {
    if (!map) return;
    setData(map, "cursor", p.cursor ? { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: p.cursor } } : null);
  }, [map, p.cursor]);

  // ------------------------------------------------------------------ context layer (all units of a level in view)
  useEffect(() => {
    if (!map) return;
    const national = p.explore && NATIONAL.includes(p.explore);
    for (const id of ["national-fill", "national-line", "national-labels"]) {
      map.setLayoutProperty(id, "visibility", national ? "visible" : "none");
    }
    if (national) {
      setData(map, "context", null);
      setData(map, "context-labels", null);
      latest.current.onContextStatus({ loading: true, count: 0, tooFarOut: false });
      let live = true;
      loadNational(p.explore!).then((fc) => {
        if (!live) return;
        setData(map, "national", fc);
        setData(map, "national-labels", {
          type: "FeatureCollection",
          features: fc.features.map((f) => ({
            type: "Feature", properties: { name: f.properties?.name },
            geometry: { type: "Point", coordinates: [f.properties?.lx, f.properties?.ly] },
          })),
        });
        latest.current.onContextStatus({ loading: false, count: fc.features.length, tooFarOut: false });
      });
      return () => { live = false; };
    }
    setData(map, "national", null);
    setData(map, "national-labels", null);
    if (!p.explore) {
      setData(map, "context", null);
      setData(map, "context-labels", null);
      latest.current.onContextStatus({ loading: false, count: 0, tooFarOut: false });
      return;
    }
    const level = p.explore;
    let ctrl: AbortController | null = null;
    const load = () => {
      ctrl?.abort();
      ctrl = new AbortController();
      const b = map.getBounds();
      latest.current.onContextStatus({ loading: true, count: 0, tooFarOut: false });
      api.context(level, [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], map.getZoom(), ctrl.signal)
        .then((fc) => {
          const color = LEVEL_COLOR[level];
          setData(map, "context", {
            ...fc, features: fc.features.map((f) => ({ ...f, properties: { ...f.properties, color } })),
          });
          setData(map, "context-labels", {
            type: "FeatureCollection",
            features: fc.features.map((f) => ({
              type: "Feature", geometry: { type: "Point", coordinates: f.properties.label_at },
              properties: { name: f.properties.name ?? "" },
            })),
          });
          latest.current.onContextStatus({
            loading: false, count: fc.features.length, tooFarOut: !!fc.too_far_out, minZoom: fc.min_zoom,
          });
        })
        .catch((e) => {
          if (e.name !== "AbortError") latest.current.onContextStatus({ loading: false, count: 0, tooFarOut: false });
        });
    };
    load();
    map.on("moveend", load);
    return () => {
      ctrl?.abort();
      map.off("moveend", load);
    };
  }, [map, p.explore]);

  // ------------------------------------------------------------------ climate zones
  useEffect(() => {
    if (!map) return;
    const path = p.koppenPath;
    map.setLayoutProperty("koppen", "visibility", path ? "visible" : "none");
    // With climate colours on, watersheds become outlines so both stay readable.
    map.setPaintProperty("national-fill", "fill-opacity",
      path ? ["case", ["boolean", ["feature-state", "hover"], false], 0.18, 0] : ["case", ["boolean", ["feature-state", "hover"], false], 0.58, 0.32]);
    map.setPaintProperty("national-line", "line-color", path ? "#0b0b0b" : "#ffffff");
    map.setPaintProperty("national-line", "line-opacity", path ? 0.55 : 0.95);
    if (!path) {
      latest.current.onKoppenView([]);
      return;
    }
    (map.getSource("koppen") as RasterTileSource).setTiles([tileUrl(path)]);
    let mine = 0;
    const update = () => {
      const t = ++mine;
      const b = map.getBounds();
      classesInView(path, [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], map.getZoom()).then((cls) => {
        if (t === mine) latest.current.onKoppenView(cls);
      });
    };
    update();
    map.on("moveend", update);
    return () => { map.off("moveend", update); };
  }, [map, p.koppenPath]);

  // ------------------------------------------------------------------ basemap, overlays, 2D/3D
  useEffect(() => {
    if (!map) return;
    map.setLayoutProperty("imagery", "visibility", p.basemap === "satellite" ? "visible" : "none");
    map.setLayoutProperty("topo", "visibility", p.basemap === "topo" ? "visible" : "none");
    map.setLayoutProperty("hillshade", "visibility", p.basemap === "map" ? "visible" : "none");
  }, [map, p.basemap]);

  useEffect(() => {
    if (!map) return;
    map.setLayoutProperty("wbd-overlay", "visibility", p.wbdOverlay ? "visible" : "none");
  }, [map, p.wbdOverlay]);

  useEffect(() => {
    if (!map) return;
    if (p.view === "3d") {
      map.setTerrain({ source: "terrain-dem", exaggeration: latest.current.exaggeration });
      map.setSky(SKY as never);
      if (!latest.current.riding) map.easeTo({ pitch: 62, bearing: map.getBearing() || -18, duration: 1200 });
    } else {
      map.setTerrain(null);
      if (!latest.current.riding) map.easeTo({ pitch: 0, bearing: 0, duration: 900 });
    }
  }, [map, p.view]);

  useEffect(() => {
    if (!map || p.view !== "3d") return;
    map.setTerrain({ source: "terrain-dem", exaggeration: p.exaggeration });
  }, [map, p.exaggeration, p.view]);

  return <div ref={el} className="map" />;
}

function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}
