/**
 * Persistent Node sidecar that hosts every TS scraper behind a tiny HTTP API.
 *
 * Started once by Python (FastAPI lifespan) on port 3100. Replaces the
 * per-call `npx tsx` spawn — avoids 2.4s of cold-start latency per scrape.
 *
 * Endpoints:
 *   GET  /health
 *   GET  /:competitorId?q=<product name>
 *
 * Response: JSON array of ProductData (raw candidates, no matching).
 */
import { createServer } from "http";
import { searchPinkblue } from "../../lib/scrapers/pinkblue";
import { searchMedikabazar } from "../../lib/scrapers/medikabazar";
import { searchOralkart } from "../../lib/scrapers/oralkart";
import { searchDentmark } from "../../lib/scrapers/dentmark";
import { searchMetroOrthodontics } from "../../lib/scrapers/metroorthodontics";
import { searchShop4Smile } from "../../lib/scrapers/shop4smile";
import { searchSurgicalmart } from "../../lib/scrapers/surgicalmart";
import { searchSmileStream } from "../../lib/scrapers/smilestream";
import { searchDentaid } from "../../lib/scrapers/dentaid";
import { searchDentalkart } from "../../lib/scrapers/dentalkart";
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
