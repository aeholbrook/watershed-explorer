# Watershed Explorer: the built front end served by the FastAPI backend, on one port.
#   docker build -t watershed-explorer .
# Volumes: /app/backend/cache (HTTP + lidar caches) and /app/backend/sparrow
# (sparrow_midwest.sqlite, made once by scripts/build_sparrow_index.py).

FROM node:22-slim AS web
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NUMBA_CACHE_DIR=/app/backend/cache/numba \
    SPARROW_DB=/app/backend/sparrow/sparrow_midwest.sqlite
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/data/normals_index.json ./data/
COPY --from=web /src/dist /app/frontend/dist
RUN useradd --system --uid 10001 --home-dir /app app \
 && mkdir -p cache sparrow && chown app:app cache sparrow
USER app
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4)"
# One worker: in-flight lookups and rate-limit windows live in this process.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765", "--no-access-log"]
