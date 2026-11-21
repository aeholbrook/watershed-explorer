"""Build data/sparrow_midwest.sqlite from the USGS SPARROW Midwest 2012 model output.

The live SPARROW map service takes ~1 minute per lookup, so the app reads this local
table instead. Streams the two ScienceBase files (~940 MB) without saving them; keeps
per-COMID loads, delivery fraction and the source mix (in permille).

Source: Robertson & Saad (2019), ScienceBase item 5cbf5150e4b09b8c0b700df3.

    .venv/bin/python scripts/build_sparrow_index.py
"""

import csv
import io
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://www.sciencebase.gov/catalog/file/get/5cbf5150e4b09b8c0b700df3?f=__disk__"
FILES = {
    "tn": BASE + "66%2F4f%2Ff2%2F664ff289064560bbce748082f7b34593dad49ca2",
    "tp": BASE + "bf%2F73%2F1f%2Fbf731fdf4e984a5cf50c0f1a140cda366cb8c1d3",
}
OUT = Path(os.environ.get("SPARROW_DB") or Path(__file__).resolve().parent.parent / "data" / "sparrow_midwest.sqlite")


def sources(header: list[str], prefix: str) -> list[str]:
    """Source columns for a load prefix, e.g. il_tn -> [il_tn_wwtp, il_tn_fert, ...]."""
    return [h for h in header if h.startswith(prefix + "_")]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.execute("""CREATE TABLE sparrow (comid INTEGER, nutrient TEXT, inc_area REAL, cum_area REAL,
                  inc_load REAL, acc_load REAL, del_frac REAL, inc_mix TEXT, acc_mix TEXT,
                  PRIMARY KEY (comid, nutrient)) WITHOUT ROWID""")
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    for nut, url in FILES.items():
        t0 = time.time()
        resp = urllib.request.urlopen(url, timeout=600)
        reader = csv.reader(io.TextIOWrapper(resp, encoding="utf-8"))
        header = next(reader)
        idx = {h: i for i, h in enumerate(header)}
        il, al = f"il_{nut}", f"al_{nut}"
        il_src, al_src = sources(header, il), sources(header, al)
        db.execute("INSERT INTO meta VALUES (?, ?)", (f"{nut}_sources", ",".join(c[len(il) + 1:] for c in il_src)))
        rows, n = [], 0
        for r in reader:
            inc, acc = float(r[idx[il]]), float(r[idx[al]])
            if inc <= 0 and acc <= 0:
                continue

            def mix(cols, total):
                return ",".join(str(round(1000 * float(r[idx[c]]) / total)) if total > 0 else "0" for c in cols)

            rows.append((int(r[idx["comid"]]), nut, float(r[idx["IncAreaKm2"]]), float(r[idx["CumAreaKm2"]]),
                         inc, acc, float(r[idx["DEL_FRAC"]]), mix(il_src, inc), mix(al_src, acc)))
            n += 1
            if len(rows) >= 50_000:
                db.executemany("INSERT OR REPLACE INTO sparrow VALUES (?,?,?,?,?,?,?,?,?)", rows)
                rows.clear()
                print(f"{nut}: {n:,} rows, {time.time() - t0:.0f}s", flush=True)
        db.executemany("INSERT OR REPLACE INTO sparrow VALUES (?,?,?,?,?,?,?,?,?)", rows)
        db.commit()
        print(f"{nut}: done, {n:,} rows in {time.time() - t0:.0f}s", flush=True)
    db.execute("INSERT INTO meta VALUES ('model', 'USGS SPARROW Midwest 2012 base year')")
    db.commit()
    db.execute("VACUUM")
    db.close()
    tmp.rename(OUT)
    print(f"-> {OUT} ({OUT.stat().st_size // 2**20} MB)")


if __name__ == "__main__":
    sys.exit(main())
