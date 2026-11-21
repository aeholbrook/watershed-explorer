import { useEffect, useState } from "react";
import type { Mode } from "./palette";

/** "auto" follows the system setting; the choice is remembered in this browser. */
export type ThemePref = "auto" | "light" | "dark";

const KEY = "watersheds-theme";
const media = () => window.matchMedia("(prefers-color-scheme: dark)");

export function readPref(): ThemePref {
  const v = localStorage.getItem(KEY);
  return v === "light" || v === "dark" ? v : "auto";
}

export const resolve = (pref: ThemePref): Mode => (pref === "auto" ? (media().matches ? "dark" : "light") : pref);

/** The theme preference and the mode it resolves to; keeps <html data-theme> in step. */
export function useTheme(): [ThemePref, (p: ThemePref) => void, Mode] {
  const [pref, setPref] = useState<ThemePref>(readPref);
  const [mode, setMode] = useState<Mode>(() => resolve(readPref()));
  useEffect(() => {
    const update = () => setMode(resolve(pref));
    update();
    const mq = media();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, [pref]);
  useEffect(() => {
    document.documentElement.dataset.theme = mode;
  }, [mode]);
  const choose = (p: ThemePref) => {
    if (p === "auto") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, p);
    setPref(p);
  };
  return [pref, choose, mode];
}
