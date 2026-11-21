import type { Feature, FeatureCollection, Geometry, LineString, MultiPolygon, Polygon } from "geojson";

export type LngLat = { lon: number; lat: number };

export type Geocode = LngLat & {
  label: string;
  source: "openstreetmap" | "census";
  precision: string;
  alternatives?: Geocode[];
};

export type LevelKey =
  | "huc2" | "huc4" | "huc6" | "huc8" | "huc10" | "huc12"
  | "catchment" | "hr_catchment" | "drains_to_entry";

export type Unit = {
  level: LevelKey;
  label: string;
  code: string;
  name: string | null;
  areasqkm: number | null;
  states?: string | null;
  source: string;
  geometry: Polygon | MultiPolygon;
  bbox: [number, number, number, number];
  label_at: [number, number];
};

export type Entry = LngLat & {
  comid: number;
  stream: string | null;
  reachcode: string;
  measure: number | null;
};

export type Raindrop = {
  path: LineString;
  path_m: number;
  entry: Entry;
  entry_flowline: Geometry;
  drains_to_entry: Unit | null;
};

export type Segment = {
  name: string;
  start_km: number;
  end_km: number;
  length_km: number;
  start_hours: number;
  end_hours: number;
  hours: number;
  start_elev_m: number;
  end_elev_m: number;
  drop_m: number;
  end_comid: number;
  end_da_km2: number | null;
  end_flow_cms: number;
  max_order: number;
  reach_count: number;
};

export type ReachProps = {
  comid: number;
  name: string;
  seg: number;
  order: number;
  start_km: number;
  length_km: number;
  hours: number;
  flow_cms: number;
  da_km2: number | null;
};

export type Downstream = {
  entry: Entry;
  reaches: FeatureCollection<LineString, ReachProps>;
  segments: Segment[];
  /** [km from stream entry, elevation m, lon, lat] */
  profile: [number, number, number, number][];
  outlet: LngLat & { name: string; kind: "sea" | "terminal"; elev_m: number };
  totals: { length_km: number; hours: number; reach_count: number };
};

export type Pond = LngLat & { depth_m: number; area_m2: number; sink: boolean };

export type LidarTrace = {
  path: LineString;
  length_m: number;
  start_elev_m: number;
  end_elev_m: number;
  end: LngLat;
  end_reason: "stream" | "depression" | "no_lidar" | "too_far" | "edge" | "time_budget";
  levels_used: number[];
  ponds: Pond[];
  /** [metres along path, elevation m, lon, lat] */
  profile: [number, number, number, number][];
  resolution_m: number;
  seconds: number;
};

export type Basin = {
  comid: number;
  name: string | null;
  areasqkm: number;
  geometry: Polygon | MultiPolygon;
  bbox: [number, number, number, number];
};

export type ContextProps = {
  code: string;
  name: string | null;
  areasqkm: number | null;
  level: LevelKey;
  label_at: [number, number];
};

export type ContextCollection = FeatureCollection<Polygon | MultiPolygon, ContextProps> & {
  too_far_out?: boolean;
  min_zoom?: number;
};

export type Load<T> =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "ok"; data: T }
  | { state: "error"; error: string };

export type Basemap = "map" | "satellite" | "topo";

export type UnitFeature = Feature<Polygon | MultiPolygon, { level: LevelKey }>;

export type RunoffCase = {
  label: string;
  storm_in: number;
  storm_mm: number;
  runoff_frac: number;
};

export type Well = LngLat & {
  id: string;
  name: string;
  dist_km: number;
  depth_to_water_m: number | null;
  measured: string | null;
  well_depth_m: number | null;
  url: string | null;
};

export type Sinkhole = LngLat & { dist_m: number; area_m2: number | null };

export type BelowGround = {
  soil: {
    mapunit: string;
    component: string | null;
    component_pct: number | null;
    hydgrp: string | null;
    drainage: string | null;
    ksat_um_s: number | null;
    water_table_cm: number | null;
    urban: boolean;
  } | null;
  land: { impervious_pct: number; radius_m: number; land_cover: string | null; source: string } | null;
  storms: { source: string; "24h_in": Record<string, number> } | null;
  runoff: RunoffCase[];
  aquifer: { name: string; rock_type: string | null; source: string } | null;
  karst: { in_karst: boolean; description: string | null; sinkholes: Sinkhole[]; source: string } | null;
  wells: Well[];
  notes: string[];
};

export type GhostStreams = {
  lines: import("geojson").FeatureCollection<LineString, { area_ha: number; length_m: number }>;
  ghost_km: number;
  count: number;
  nearest: { dist_m: number; area_ha: number } | null;
  window_km: number;
  threshold_ha: number;
  seconds: number;
};

// ---------------------------------------------------------------- bioregional tabs

export type Species = {
  id: number; name: string; common: string | null; count: number;
  photo: string | null; attribution: string | null; url: string | null;
};

export type PlaceData = {
  ecoregions: { levels: { level: string; code: string; name: string }[];
    shapes: import("geojson").FeatureCollection; source: string } | null;
  bioregion: { bioregion?: string; bioregion_code?: string; ecoregion?: string; biome?: string;
    realm?: string; conservation?: string; source: string } | null;
  homelands: { territories: { name: string; url: string }[]; source: string } | null;
  cessions: { cessions: { number: string; date: string | null; tribes: string | null; present_day: string | null;
    schedule_url: string | null; treaty_url: string | null }[]; nearby: boolean; source: string } | null;
  hardiness: { zone: string; range_f: string; source: string } | null;
  flyway: string | null;
  frost: { last_spring: string | null; first_fall: string | null; season_days: number; frost_free: boolean;
    station: string; dist_km: number; source: string } | null;
  leaf_out: { average: string; anomaly_days: number | null; year: number; source: string } | null;
  precip: { normal: { normal_in: number; station: string; dist_km: number } | null;
    last_year: { year: number; total_in: number; source: string } | null } | null;
};

export type LifeData = {
  county: { id: number; name: string };
  native_plants: Species[];
  introduced_plants: Species[];
  early_bloomers: Species[];
  birds: { year_round: Species[]; summer: Species[]; winter: Species[] } | null;
  fish: { segment: string; lon: number; lat: number; species: Species[] }[];
  source: string;
};

export type SparrowSite = {
  comid: string; stream: string | null; area_km2: number; upstream_km2: number;
  local_yield_kg_km2: number; local_load_kg: number; local_sources: { source: string; share: number }[];
  river_load_kg: number; river_sources: { source: string; share: number }[];
  delivered_frac: number; target: string; concentration_mg_l: number | null;
};

export type FlowsData = {
  nutrients: { nitrogen: SparrowSite; phosphorus: SparrowSite | null; river_nitrogen: SparrowSite | null;
    river_phosphorus: SparrowSite | null; model: string } | null;
  neighbors: { population: number; municipalities: { name: string; share_inside: number }[];
    huc12: string; huc12_name: string; area_km2: number; source: string } | null;
  tap_water: { pwsid: string; name: string; population: number | null; source_type: string | null;
    primary_sources: { name: string; kind: string }[]; emergency_sources: { name: string; kind: string }[];
    source: string } | null;
  wastewater: { facility: string; npdes: string | null; population: number | null; echo_url: string | null;
    plant: LngLat | null; combined_sewer: { combined: boolean; area: string | null } | null; source: string } | null;
  impaired: { units: { id: string; name: string | null; status: string | null; impaired: boolean;
    category: string | null; cycle: number | null; causes: string[]; on_route: boolean }[];
    shapes: import("geojson").FeatureCollection; source: string } | null;
  monitoring: { total: number; usgs: number; other: number; url: string } | null;
};

export type RestoreData = {
  roof: { area_m2: number; area_ft2: number; address: string | null; use: string | null; height_m: number | null;
    match: "contains" | "nearest"; distance_m: number; geometry: import("geojson").Geometry; source: string } | null;
  precip_normal_in: number | null;
  rain_plan: { texture: string; basis: string; depth: string; factor: number; garden_ft2_per_downspout: number;
    garden_ft2_far: number; gallons_per_inch: number; barrels_per_downspout_inch: number; too_slow: boolean;
    annual_gallons?: number; source: string } | null;
  then_now: { before: string | null; now: string | null; source: string;
    before_around: { name: string; color: string; share: number }[];
    now_around: { name: string; color: string; share: number }[] } | null;
  topo_editions: { year: number; scale: number; name: string; ids: number[] }[];
  msd: { eligible_areas: string[]; url: string; note: string } | null;
  overlays: { historic_topo: string | null; landfire: string; bps_layer: string; evt_layer: string };
};
