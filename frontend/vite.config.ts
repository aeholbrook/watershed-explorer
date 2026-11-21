import { existsSync } from "node:fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Published at www.aeholbrook.com/watersheds (see backend BASE_PATH).
const BASE = process.env.WATERSHEDS_BASE ?? "/watersheds/";

// Missing static data files must 404, not fall back to index.html: map tiles that don't
// exist (open ocean) would otherwise "succeed" as HTML and fail to decode.
const dataNotFound = {
  name: "data-not-found",
  configureServer(server: { middlewares: { use: (fn: (req: { url?: string }, res: { statusCode: number; end: () => void }, next: () => void) => void) => void } }) {
    server.middlewares.use((req, res, next) => {
      const path = (req.url ?? "").split("?")[0];
      const rel = path.startsWith(BASE) ? path.slice(BASE.length - 1) : path;
      if (rel.startsWith("/data/") && !existsSync(`public${decodeURIComponent(rel)}`)) {
        res.statusCode = 404;
        res.end();
        return;
      }
      next();
    });
  },
};

export default defineConfig({
  base: BASE,
  plugins: [react(), dataNotFound],
  server: {
    port: 5173,
    proxy: { [`${BASE}api`]: "http://127.0.0.1:8765" },
  },
  build: { chunkSizeWarningLimit: 1500 },
});
