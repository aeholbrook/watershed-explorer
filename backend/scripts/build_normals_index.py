"""Build data/normals_index.json: NOAA 1991-2020 annual normals per station (frost dates, precip).

NCEI's search API isn't reachable from every network, so the app looks up the nearest
station in this local index instead. Source: NOAA Open Data on AWS (public, no key).

    .venv/bin/python scripts/build_normals_index.py [path/to/archive.tar.gz]
"""

import csv
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

URL = ("https://noaa-normals-pds.s3.amazonaws.com/normals-annualseasonal/1991-2020/archive/"
       "us-climate-normals_1991-2020_v1.0.1_annualseasonal_multivariate_by-station_c20230404.tar.gz")
OUT = Path(__file__).resolve().parent.parent / "data" / "normals_index.json"
FIELDS = {
    "lst": "ANN-TMIN-PRBLST-T32FP50",   # last spring 32°F frost, 50% probability (MM/DD)
    "fst": "ANN-TMIN-PRBFST-T32FP50",   # first fall 32°F frost
    "gsl": "ANN-TMIN-PRBGSL-T32FP50",   # growing season length (days)
    "prcp": "ANN-PRCP-NORMAL",          # annual precipitation (inches)
}


def main() -> None:
    if len(sys.argv) > 1:
        data = Path(sys.argv[1]).read_bytes()
    else:
        print("downloading", URL)
        data = urllib.request.urlopen(URL, timeout=300).read()
    stations = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar:
            if not member.name.endswith(".csv"):
                continue
            rows = list(csv.DictReader(io.TextIOWrapper(tar.extractfile(member), encoding="utf-8")))
            if not rows:
                continue
            r = rows[0]
            rec = {"id": r["STATION"], "name": r["NAME"].strip(),
                   "lat": float(r["LATITUDE"]), "lon": float(r["LONGITUDE"])}
            for key, col in FIELDS.items():
                v = (r.get(col) or "").strip()
                if v and v != "-9999":
                    rec[key] = v
            if len(rec) > 4:
                stations.append(rec)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(stations, separators=(",", ":")))
    print(f"{len(stations)} stations -> {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
