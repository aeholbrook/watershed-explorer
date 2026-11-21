import type { LevelKey } from "./types";

/**
 * Watershed levels are ordered (region → lot), so they share one violet ordinal ramp,
 * light for the largest unit and dark for the finest. Validated with the dataviz
 * validator (--ordinal, light surface): monotone L, adjacent ΔL ≥ 0.06, light end 2.1:1.
 * Violet keeps boundaries visually separate from water, which is blue.
 */
export const LEVEL_ORDER: LevelKey[] = [
  "huc2", "huc4", "huc6", "huc8", "huc10", "huc12", "catchment", "hr_catchment", "drains_to_entry",
];

export const LEVEL_COLOR: Record<LevelKey, string> = {
  huc2: "#b4a5f6",
  huc4: "#a18cee",
  huc6: "#8e74e3",
  huc8: "#7c5dd4",
  huc10: "#6b48c0",
  huc12: "#5934a9",
  catchment: "#47248d",
  hr_catchment: "#361670",
  drains_to_entry: "#250c51",
};

export const LEVEL_WIDTH: Record<LevelKey, number> = {
  huc2: 4, huc4: 3.5, huc6: 3, huc8: 2.6, huc10: 2.2, huc12: 1.9,
  catchment: 1.6, hr_catchment: 1.4, drains_to_entry: 1.4,
};

export const LEVEL_NAME: Record<LevelKey, string> = {
  huc2: "Region · HUC2",
  huc4: "Subregion · HUC4",
  huc6: "Basin · HUC6",
  huc8: "Subbasin · HUC8",
  huc10: "Watershed · HUC10",
  huc12: "Subwatershed · HUC12",
  catchment: "Stream catchment · NHDPlus",
  hr_catchment: "Fine catchment · NHDPlus HR",
  drains_to_entry: "Drains to your entry point",
};

/** Categorical slots 1 and 2 of the reference palette: water vs. the overland path. */
export const WATER = "#2a78d6";
export const WATER_DARK = "#184f95";
export const OVERLAND = "#eb6834";
/** Categorical slot 3: rain that soaks in (paired with OVERLAND in the runoff bars). */
export const SOAK = "#1baf7a";

/** Categorical slot 6 (green): ecoregion outlines, kept clear of water blue and watershed violet. */
export const ECO = "#008300";
/** Status "critical": impaired waters, always paired with an icon and the word "Impaired". */
export const CRITICAL = "#d03b3b";

/**
 * Five hues for colouring neighbouring watersheds apart (graph-coloured, so adjacent units
 * never share one). Validated all-pairs (light mode): worst CVD ΔE 13.0, normal 16.3. Yellow
 * and magenta sit under 3:1 contrast, so every region also carries a label and a hover readout.
 */
export const REGION_COLORS = ["#eda100", "#e87ba4", "#008300", "#4a3aa7", "#2a78d6"];

export const INK = {
  primary: "#0b0b0b",
  secondary: "#52514e",
  muted: "#898781",
  grid: "#e1e0d9",
  axis: "#c3c2b7",
  surface: "#fcfcfb",
};
