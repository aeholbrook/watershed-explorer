import type { LevelKey } from "./types";

/**
 * Watershed levels are ordered (region → lot), so they share one violet ordinal ramp
 * (THEMES[mode].levels). Violet keeps boundaries visually separate from water, which is blue.
 */
export const LEVEL_ORDER: LevelKey[] = [
  "huc2", "huc4", "huc6", "huc8", "huc10", "huc12", "catchment", "hr_catchment", "drains_to_entry",
];

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

export type Mode = "light" | "dark";

/**
 * Every colour the map draws, per mode. Dark is its own set of steps (validated against the
 * dark surface #1a1a19), not an automatic inversion. The page and the SVG charts use the CSS
 * variables in styles.css instead (CSS below), which carry the same values.
 */
export type Theme = {
  /** Categorical slots 1-3: water, the overland path, rain that soaks in. */
  water: string;
  /** Emphasis for water (focused reach, outlet, links): darker on light, lighter on dark. */
  waterEmphasis: string;
  overland: string;
  soak: string;
  /** Categorical slot 6 (green): ecoregion outlines, clear of water blue and watershed violet. */
  eco: string;
  /** Status "critical": impaired waters, always paired with an icon and the word "Impaired". */
  critical: string;
  /**
   * Hues for colouring neighbouring watersheds apart (graph-coloured, so adjacent units never
   * share one). Light: five slots validated all-pairs (worst CVD ΔE 13.0, normal 16.3; yellow
   * and magenta under 3:1, so every region also carries a label and a hover readout).
   * Dark: no five of the dark steps clear the all-pairs floors (violet-blue, red-magenta,
   * aqua-green and orange-yellow all collide), and HUC8 can't be four-coloured under the
   * builder's neighbour rule. So four hues (validated all-pairs: normal ΔE 19.3, CVD 6.9 in
   * the warn band, carried by the labels and readout) and the rare fifth class (1 HUC4,
   * 5 HUC6, 40 HUC8) as a 45° hatch in regionHatch.
   */
  regions: [string, string, string, string, string];
  regionHatch: string | null;
  /** Lines between national regions (a gap in the surface colour). */
  regionLine: string;
  /**
   * Watershed levels share one violet ordinal ramp, largest unit to finest, the finest being
   * the most prominent. Light runs light → dark (validated --ordinal, light surface: adjacent
   * ΔL ≥ 0.06, light end 2.12:1); dark runs dim → bright on the same hue (validated --ordinal,
   * dark surface: ΔL 0.0635, dim end 2.15:1).
   */
  levels: Record<LevelKey, string>;
  ink: { primary: string; secondary: string; muted: string; grid: string; axis: string; surface: string };
  /** Text halos, casings and marker rings: the surface colour, so marks separate from the map. */
  halo: string;
  hillshade: { shadow: string; highlight: string };
  sky: { sky: string; horizon: string; fog: string };
};

export const THEMES: Record<Mode, Theme> = {
  light: {
    water: "#2a78d6", waterEmphasis: "#184f95", overland: "#eb6834", soak: "#1baf7a",
    eco: "#008300", critical: "#d03b3b",
    regions: ["#eda100", "#e87ba4", "#008300", "#4a3aa7", "#2a78d6"], regionHatch: null, regionLine: "#ffffff",
    levels: {
      huc2: "#b4a5f6", huc4: "#a18cee", huc6: "#8e74e3", huc8: "#7c5dd4", huc10: "#6b48c0",
      huc12: "#5934a9", catchment: "#47248d", hr_catchment: "#361670", drains_to_entry: "#250c51",
    },
    ink: { primary: "#0b0b0b", secondary: "#52514e", muted: "#898781", grid: "#e1e0d9", axis: "#c3c2b7", surface: "#fcfcfb" },
    halo: "#ffffff",
    hillshade: { shadow: "#5b5a55", highlight: "#ffffff" },
    sky: { sky: "#a9cdf2", horizon: "#e8eef4", fog: "#f2f4f6" },
  },
  dark: {
    water: "#3987e5", waterEmphasis: "#86b6ef", overland: "#d95926", soak: "#199e70",
    eco: "#008300", critical: "#d03b3b",
    regions: ["#c98500", "#d55181", "#008300", "#3987e5", "rgba(0,0,0,0)"], regionHatch: "#9085e9", regionLine: "#0d0d0d",
    levels: {
      huc2: "#55448c", huc4: "#6851ad", huc6: "#7b5fcc", huc8: "#8e70e9", huc10: "#a184fe",
      huc12: "#b29fff", catchment: "#c5b9ff", hr_catchment: "#d9d2ff", drains_to_entry: "#eeebfe",
    },
    ink: { primary: "#ffffff", secondary: "#c3c2b7", muted: "#898781", grid: "#2c2c2a", axis: "#383835", surface: "#1a1a19" },
    halo: "#1a1a19",
    hillshade: { shadow: "#000000", highlight: "#4a4a46" },
    sky: { sky: "#16273d", horizon: "#2a3646", fog: "#1f201e" },
  },
};

/** The same roles as CSS variables, for the page and the SVG charts (they follow the theme on their own). */
export const CSS = {
  water: "var(--water)", waterEmphasis: "var(--water-dark)", overland: "var(--overland)", soak: "var(--soak)",
  ink: { primary: "var(--text-primary)", secondary: "var(--text-secondary)", muted: "var(--text-muted)",
         grid: "var(--grid)", axis: "var(--axis)", surface: "var(--surface-1)" },
  level: (k: LevelKey) => `var(--level-${k})`,
};
