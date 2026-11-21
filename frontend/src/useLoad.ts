import { useEffect, useRef, useState } from "react";
import type { Load } from "./types";

/** Fetch whenever `key` changes; aborts the previous request. A null key means idle. */
export function useLoad<T>(key: string | null, fn: (signal: AbortSignal) => Promise<T>): Load<T> {
  const [s, setS] = useState<Load<T>>({ state: "idle" });
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    if (!key) {
      setS({ state: "idle" });
      return;
    }
    const ctrl = new AbortController();
    setS({ state: "loading" });
    fnRef.current(ctrl.signal)
      .then((data) => setS({ state: "ok", data }))
      .catch((e) => {
        if (e.name !== "AbortError") setS({ state: "error", error: String(e.message ?? e) });
      });
    return () => ctrl.abort();
  }, [key]);
  return s;
}

export const ok = <T,>(l: Load<T>): T | null => (l.state === "ok" ? l.data : null);
