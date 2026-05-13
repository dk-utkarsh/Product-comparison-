/**
 * Bridge: call a single competitor scraper from Python.
 *
 * Usage:
 *   npx tsx api/bridges/scrape-competitor.ts <competitorId> "<product name>"
 *
 * Output: JSON array of ProductData (raw scraped candidates, no matching).
 * Errors are printed to stderr; stdout always carries valid JSON.
 *
 * Competitor IDs are the same ones used in lib/competitors.ts:
 *   pinkblue, medikabazar, oralkart, dentmark, metroorthodontics,
 *   shop4smile, surgicalmart, smilestream, dentaid
 */
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

async function main() {
  const [, , competitorId, ...rest] = process.argv;
  const productName = rest.join(" ").trim();

  if (!competitorId || !productName) {
    console.error(
      "usage: tsx scrape-competitor.ts <competitorId> <productName>"
    );
    console.log("[]");
    process.exit(2);
  }

  const scraper = scrapers[competitorId];
  if (!scraper) {
    console.error(`unknown competitor: ${competitorId}`);
    console.log("[]");
    process.exit(2);
  }

  try {
    const results = await scraper(productName);
    process.stdout.write(JSON.stringify(results));
  } catch (err) {
    console.error(`scrape failed: ${(err as Error).message}`);
    process.stdout.write("[]");
    process.exit(1);
  }
}

main();
