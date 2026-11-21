# Watershed Explorer

Where does the rain on your roof go? Pick any US address and the app traces a raindrop
from the roof to the creek to the sea, and shows every watershed it belongs to
(HUC2 → HUC12 → NHDPlus catchments), what's below ground, the place's ecology and
history, what the runoff carries, and what you could do about it.

Published at <https://www.aeholbrook.com/watersheds/>.

## Layout

- `backend/` — FastAPI. Every panel has its own endpoint (`/api/...`) that queries the
  public services (USGS, EPA, NOAA, USDA, iNaturalist, …) with retries and a SQLite
  response cache. Lidar traces and ghost streams run in a process pool.
- `frontend/` — React + Vite + MapLibre. Static national layers (WBD HUC2–8, Köppen
  tiles) live in `frontend/public/data/`.
- `backend/scripts/` — builders for the static data. `build_sparrow_index.py` makes
  `sparrow_midwest.sqlite` (250 MB, not in git; streams ~940 MB from ScienceBase).

## Develop

```sh
cd backend && uv venv && uv pip install -r requirements.txt
.venv/bin/python scripts/build_sparrow_index.py          # once; Flows works without it
.venv/bin/uvicorn app.main:app --port 8765
cd frontend && npm ci && npx vite                        # http://localhost:5173/watersheds/
```

The app is served under `/watersheds/` (Vite `base`, backend `WATERSHEDS_BASE_PATH`);
the backend also answers at `/`.

## Run the production image

```sh
docker compose up --build                                # http://localhost:8765/watersheds/
```

One container serves the built front end and the API. Volumes: `/app/backend/cache`
(writable) and `/app/backend/sparrow` (the SPARROW table). Environment:

| Variable | Default | |
|---|---|---|
| `WATERSHEDS_BASE_PATH` | `/watersheds` | path prefix it's published under |
| `ARCGIS_API_KEY` | empty | enables the historic topo map overlay (Esri terms) |
| `WATERSHEDS_WAIT_SECONDS` | `20` | how long a request waits before answering 202 |
| `SPARROW_DB` | `/app/backend/sparrow/sparrow_midwest.sqlite` | |

Keep it to one uvicorn worker: in-flight lookups and rate limits live in the process.

## Behind proxies

Cloudflare ends a request after 100 s and some lookups take longer, so each lookup runs
as a task keyed by its URL. A request waits up to 20 s and then answers `202`; the
browser asks again with the same URL and picks up the same task (`backend/app/jobs.py`).
New lookups count against a per-visitor budget (left-most `X-Forwarded-For`), and the
CPU-heavy ones share a small global queue. Nominatim requests are paced to 1/s.
