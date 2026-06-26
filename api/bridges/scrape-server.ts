/**
 * Persistent Node sidecar that hosts every TS scraper behind a tiny HTTP API.
 *
 * Started once by Python (FastAPI lifespan) on port 3100. Replaces the
 * per-call `npx tsx` spawn — avoids 2.4s of cold-start latency per scrape.
 *
 * Endpoints:
 *   GET  /health
 *   GET  /product?scraper=&url=
 *   GET  /:competitorId?q=<product name>
 *
 * Response: JSON array of ProductData (raw candidates, no matching).
 */
import { createServer } from "http";
import { readFileSync, existsSync } from "node:fs";

// Load .env from repo root into process.env (without overriding already-set vars).
// Lets us pick up SCRAPER_API_KEY etc. without a dotenv dep.
const envFile = ".env";
if (existsSync(envFile)) {
  for (const line of readFileSync(envFile, "utf-8").split("\n")) {
    const m = line.trim().match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (m && !(m[1] in process.env)) process.env[m[1]] = m[2];
  }
}

import { searchPinkblue, fetchPinkblueProduct } from "../../lib/scrapers/pinkblue";
import { searchMedikabazar } from "../../lib/scrapers/medikabazar";
import { searchOralkart, fetchOralkartProduct } from "../../lib/scrapers/oralkart";
import { searchDentmark, fetchDentmarkProduct } from "../../lib/scrapers/dentmark";
import { searchMetroOrthodontics } from "../../lib/scrapers/metroorthodontics";
import { searchShop4Smile } from "../../lib/scrapers/shop4smile";
import { searchSurgicalmart } from "../../lib/scrapers/surgicalmart";
import { searchSmileStream } from "../../lib/scrapers/smilestream";
import { searchDentaid } from "../../lib/scrapers/dentaid";
import { searchDentalkart, fetchDentalkartProduct } from "../../lib/scrapers/dentalkart";
import { fetchGenericProduct } from "../../lib/scrapers/generic";
import type { ProductData } from "../../lib/types";

const scrapers: Record<string, (q: string) => Promise<ProductData[]>> = {
  pinkblue: searchPinkblue,
  medikabazar: searchMedikabazar,
  oralkart: searchOralkart,
  dentmark: searchDentmark,
  metroorthodontics: searchMetroOrthodontics,
  shop4smile: searchShop4Smile,
  surgicalmart: searchSurgicalmart,
  smilestream: searchSmileStream,
  dentaid: searchDentaid,
  dentalkart: searchDentalkart,
};

const productFetchers: Record<string, (url: string) => Promise<ProductData | null>> = {
  pinkblue: fetchPinkblueProduct,
  oralkart: fetchOralkartProduct,
  dentmark: fetchDentmarkProduct,
  dentalkart: fetchDentalkartProduct,
  // Fallback for arbitrary top-10 merchants with no dedicated scraper. Any
  // unknown `?scraper=` also routes here (see below), so new Google-Shopping
  // sources are fetchable without adding a per-site scraper.
  generic: fetchGenericProduct,
};

const PORT = Number(process.env.SCRAPE_SERVER_PORT || 3100);

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url || "/", `http://localhost:${PORT}`);
    const path = url.pathname.replace(/^\//, "").trim();

    if (path === "health") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ status: "ok", scrapers: Object.keys(scrapers) }));
      return;
    }

    if (path === "product") {
      const scraper = url.searchParams.get("scraper")?.trim() || "";
      const target = url.searchParams.get("url")?.trim() || "";
      // Unknown scraper id → generic PDP fetcher (top-10 merchants with no
      // dedicated scraper). Known ids keep their site-specific fetcher.
      const fetcher = productFetchers[scraper] || productFetchers.generic;
      if (!target) {
        res.writeHead(400, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: "missing query param ?url=" }));
        return;
      }
      const t0 = Date.now();
      try {
        const product = await fetcher(target);
        console.log(`[product/${scraper}] ${target} → ${product ? "ok" : "null"} in ${Date.now() - t0}ms`);
        if (!product) {
          res.writeHead(404, { "content-type": "application/json" });
          res.end(JSON.stringify({ error: "could not parse PDP" }));
          return;
        }
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify(product));
      } catch (err) {
        res.writeHead(500, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: (err as Error).message }));
      }
      return;
    }

    const scraper = scrapers[path];
    if (!scraper) {
      res.writeHead(404, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: `unknown competitor: ${path}` }));
      return;
    }

    const q = url.searchParams.get("q")?.trim();
    if (!q) {
      res.writeHead(400, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "missing query param ?q=" }));
      return;
    }

    const t0 = Date.now();
    try {
      const results = await scraper(q);
      const dur = Date.now() - t0;
      console.log(`[${path}] q=${JSON.stringify(q)} → ${results.length} hits in ${dur}ms`);
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify(results));
    } catch (err) {
      const dur = Date.now() - t0;
      const msg = (err as Error).message || String(err);
      console.error(`[${path}] q=${JSON.stringify(q)} FAILED in ${dur}ms: ${msg}`);
      res.writeHead(500, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: msg, candidates: [] }));
    }
  } catch (err) {
    console.error("server error:", err);
    res.writeHead(500, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: (err as Error).message }));
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`scrape-server listening on http://127.0.0.1:${PORT}`);
  console.log(`scrapers loaded: ${Object.keys(scrapers).join(", ")}`);
});

process.on("SIGTERM", () => server.close(() => process.exit(0)));
process.on("SIGINT", () => server.close(() => process.exit(0)));
