# Matching & Extraction Edge Cases (living reference)

Every case here is a real product that broke the tool and the fix we shipped. Keep
this current — when a new edge case is found and fixed, add it. When changing the
matcher/extractor, re-check these so we don't regress. Python matcher cases should
also live as rows in `api/tests/matching/test_regression_cases.py` (reviewer runs
the suite); extraction cases are harder to unit-test (live HTML) so they live here.

---

## A. Brand identity (api/app/matching/attributes.py, gates.py)

1. **Single-letter brand INITIAL is optional** — "J Morita" ≡ "Morita", "B Braun" ≡
   "Braun". A competitor that drops the initial must still match. GUARDED: a size/
   grade letter ("S Cartridge", "D Speed", "M Brush") is kept, not folded — the
   letter is the distinguishing attribute there.
2. **Coined multi-hyphen brand kept whole** — "Kids-e-Crown" → brand "kidsecrown",
   NOT the fragment "kids". Single-hyphen brand-lines ("LM-SlimLift" → "lm",
   "Ora-Craft" → "ora") keep first-chunk behaviour.
3. **Manufacturer ⇄ product-line aliases** (`_BRAND_ALIASES`) — knowledge a rule
   can't infer: "Kids-e-Crown" ⇄ "Shinhung". Add entries as discovered.
4. **Brand-compatibility guard** — "Apex Locator Cable **For** … J-Morita" is a
   third-party PART that FITS J-Morita, not a J-Morita product → reject. Robust to
   normalization fusing "J-Morita" → "jmorita" (the brand is matched even fused).
5. **Brand check is alias-aware in BOTH layers** — gate (`_brand_match`) and the
   deeper `structured._brand_conflict` both honour aliases, or one rejects what the
   other accepts.
5a. **Competitor drops the manufacturer, leads with the PRODUCT LINE** — DK "3M ESPE
   Ketac Molar", competitor titled just "Ketac Molar" (no 3M). `_brand_match`
   accepts when the found name's leading token (≥4 chars, non-generic) is a word
   present in the SEARCH name. A genuinely different brand ("GDC …") still fails.
5b. **Brand only in the DESCRIPTION** — title "Ketac Molar", description "Ketac
   Molar **by 3M ESPE** …". `_brand_match` also checks the first ~240 chars of the
   description for the brand/alias (guarded against "compatible with <other>").

## B. Model / variant identity

6. **Single-letter model designator** — "UDS **E**" ≠ "UDS **P**", "Type A" ≠
   "Type B", "D Speed" ≠ "E Speed". A standalone UPPERCASE letter is a model code
   (`ml_e`/`ml_p`). Articles A/I and dimension X excluded.
7. **Short alphanumeric codes** — "V2" ≠ "V3" (and ≠ a single model letter). Fell
   between `_MODEL_RE` (needs ≥2 digits) and `_SKU_RE` (needs ≥3 letters);
   `_ALNUM_CODE_RE` (letter-led `[a-z]{1,2}\d{1,2}`) catches them. Units ("5g",
   "10ml") are digit-led so never match. Bonus: shade A2 ≠ A3.
8. **Numeric/serial codes** — EXS6 ≠ EXA6, 1.099-1 ≠ 1.099-2, articulator 3.5 ≠ 4.5.
9. **Tooth position** (contrast group) — central / lateral / canine / premolar /
   molar are different products (Kids-e-Crown reels-by-tooth).
10. **Other contrast axes** — upper/lower, left/right, intraoral/extraoral,
    pediatric/adult, restorative/luting, straight/curved, etc.
11. **Sub-variant selection** (`pipeline.select_variant`) — a configurable listing
    must resolve to the child matching the DK product (the 150MM reel, the
    016×022 Upper archwire), not the base/default. Works for dedicated scrapers
    AND (now) generic merchants via WooCommerce variation extraction (see G).

## C. Size / quantity tokenization (api/app/matching/tokens.py)

12. **Number + unit is JOINED** — "150 MM" → "150mm" so the spaced and glued forms
    match (DK "150MM" ≡ variant "150 MM x 200 M"), AND the unit stays bound:
    **"200mm" (width) ≠ "200m" (length)** — a bare "200" was matching the wrong
    reel. Model codes (letter-first: EXS6, V2) stay whole.
13. **Freebie is NOT pack** (`lib/pack-detector.ts`) — "8 Tips **Free**", "free 8
    tips", "with 8 free tips" = one product + bonus items, not pack-of-8. Real
    packs ("Pack of 100", "x10") still count.
14. **Dimension "x" is NOT pack** — "150 MM **x 200 M**" is 200 metres, not
    pack-of-200. The x-pack pattern excludes a following unit incl. **m / mtr /
    meter** (the reel-length unit), not just mm/ml/g.

## D. Match scoring (api/app/matching/structured.py)

15. **Near-identical name is NOT hard-rejected on a price gap** — a high fuzz/token
    name match whose unit price is far off is a pack/FORM/bundle difference, shown +
    ⚠-flagged, not rejected. WEAK-name lookalikes (low fuzz AND token) still rejected.
16. **Description IS used** — `extract_attributes_rich` pulls specs/size/material
    from the description, and the cosine is computed on `name + description[:240]`
    (augmented). So matching considers name + description + brand + specs + variant +
    pack + price band — not just the raw name string.
17. **Price band** — an 8×+ per-unit gap (uncorroborated, weak name) = different
    product. Confirmed/exact names exempt.
18. **"Extra" formulation difference FLAGS, doesn't HIDE** — "(Extra)" vs not used
    to hard-reject, but it's often just naming: a competitor sells GC "Gold Label 9
    Extra" as "HS / High Strength Posterior" (same product) or omits the word. Now
    a formulation difference → BORDERLINE + ⚠ "possible formulation difference
    (Extra)", never CONFIRMED, never hidden. Recovered buzzdent/onlinedental/
    medidentalpro/dentalbucket for the GC Gold Label 9 product.

## E. Foreign / currency

18. **Non-INR pages dropped** — all competitors are Indian (gl=in). `parsePdpHtml`
    captures `priceCurrency`, and when absent infers from page symbols (₹/Rs/INR →
    keep; €/£/EUR/GBP/AED/CAD → foreign → drop). Kills IPG Dental's EUR "Localizador
    Root ZX Mini". Foreign also leaks via URL params (`country=AE&currency=USD`).

## F. JSON-LD / HTML extraction (lib/pdp.ts)

19. **`@type` full-URL form** — accept "Product", "http(s)://schema.org/Product",
    "IndividualProduct", not just "Product" (hospitalstore.com).
19a. **ProductGroup wrapper** — some sites (jaypeedent) don't put `Product` at the
    top of `@graph`; they emit a `ProductGroup` whose `hasVariant[]` holds the
    priced `Product` nodes. `findProductNode` recurses into `hasVariant` (not just
    `@graph`/arrays), else the page reads as "couldn't verify" despite a clean
    price (₹2695).
20. **Malformed JSON-LD salvage** — strip JS `//` and `/* */` comments, trailing
    commas, AND **raw control characters** (literal tab/newline inside a string —
    dentganga). thedentistshop ships a `//` comment in its Product block.
21. **Price fallbacks** — JSON-LD offers → `product:price:amount` / `og:price:amount`
    / `itemprop=price` (content or text). Name fallbacks: og:title → <title> → <h1>.
22. **NO body-text price scraping** — deliberately removed; on real pages it grabs
    the struck-through MRP or concatenates digits (dentalstores → "2500024"). A
    wrong price is worse than none.
22a. **`image` may be an ImageObject, not a URL string** — schema.org `image` can be
    a URL, an array of URLs, an `ImageObject` ({"@type":"ImageObject","url":…}), OR
    an array of ImageObjects (WooCommerce/Yoast ship the last form). A naive
    `String(img)` yields the literal `"[object Object]"` → a broken `<img src>` →
    blank thumbnail. `jsonLdImageUrl()` unwraps `url`/`contentUrl`/`@id` recursively.
    Hit dentosky, onlinedental, medidentalpro — a whole class, not one site.

## G. Generic merchant fetch (lib/scrapers/generic.ts, sidecar)

23. **Generic reader** for merchants with no dedicated scraper — JSON-LD/OG/microdata
    via `parsePdpHtml`, same pack/unit normalization.
24. **Sub-variants per PLATFORM — all three must be extracted** into `variants[]` so
    `select_variant` resolves the DK child (size / (Extra) Big Pack), not the
    base/lowest price. A page's single JSON-LD/OG price is the DEFAULT (often the
    cheapest Mini), so without variants we show the wrong pack:
    - **WooCommerce** → `data-product_variations` attribute in the HTML
      (ayushidensity reel: 55/75/100/150/200/300 MM).
    - **Shopify** → variants are JS-hydrated (NOT in the server HTML — the page ships
      only a lowPrice JSON-LD), so fetch `/products/<handle>.json` and map
      `variants[]` (buzzdent GC Gold Label 9: picked ₹1424 Mini, correct ₹2858
      (Extra) Big Pack). `.json` prices are major units; "Default Title" = single
      variant (skip).
    - **Magento** → `jsonConfig` object in the HTML: `attributes[].options[].label`
      + `optionPrices[productId].finalPrice.amount` (medidentalpro GC Gold Label 9:
      picked ₹1379 Mini, correct ₹2812 "Big Pack - (Extra)").
    These cover the three storefront platforms ~every Indian dental merchant runs.
25. **ScraperAPI fallback** — a cheap proxy fetch (no JS render) when the direct
    fetch fails (datacenter-IP block, e.g. hospitalstore 403; buzzdent/medidentalpro
    block our IP too). Render is NOT used (~25 credits each and SPA pages usually
    have no structured price anyway). The Shopify `.json` fetch uses the same direct
    → proxy fallback.
25p. **Platform-NEUTRAL first; platform-specific only as needed.** The base product
    (name/price/image/description) comes from JSON-LD → OpenGraph → microdata →
    title/h1 — standards ~every storefront emits, so an UNKNOWN platform we've never
    seen still extracts (verified: shristigroup custom cart, dentalprod). The
    platform-specific code exists ONLY for (a) sub-variants and (b) Amazon (no
    structured data). For sub-variants, after the three platform parsers we fall
    back to **schema.org `hasVariant`** (`parseSchemaVariants`) — also
    platform-neutral, so any standards-compliant store's variants resolve (verified:
    dentalprod picked 2 variants with no dedicated parser). The Shopify `.json`
    probe fires on ANY `/products/<handle>` URL (custom-domain Shopify lacks the
    cdn.shopify marker). Genuinely structure-less pages (SPA with JS-only price:
    oralhealthcart; parked/geo-blocked: dentistdepots) stay "couldn't verify" — the
    deliberate no-wrong-price line; only the AI extractor can read those.
25a. **Discovery can land on the WRONG listing** — for buzzdent, Google surfaced a
    `gc-gold-label-hybrid` combined page (BUZZDENT title, no JSON-LD → unreadable),
    not the dedicated `gc-gold-label-9-hs-posterior-big-pack` product. Own-search /
    multiple candidate URLs mitigate; the variant fix makes the right page correct
    once reached. (Note: buzzdent names "Extra" → "HS / High Strength".)

## H. Discovery / DK anchor (api/app/serp.py, routes/serp.py)

26. **DK is the head** — DK resolves via its OWN site search, not Google. The
    comparison is DK's product vs the same product at competitors.
27. **Brand drift on BRAND-LESS queries** — "sterilization reels 150mm" resolves DK
    to whatever its top exact-size match is (Waldent one day, Oro another, as DK's
    live catalog/search changes), while Google Shopping is dominated by another
    brand (Oro) → all rejected as different-brand. Search WITH the brand for a
    specific product. (Open: optionally anchor competitor discovery to DK's product.)
28. **Google Shopping returns wrong sizes/brands** — for "…150mm" it routinely hands
    back 100mm/55mm/300mm/other-brand listings; rejecting those is correct.
29. **Shopping gate fails OPEN** — a failed/quota'd lookup returns None and we match
    normally, never stamping a whole run "Not on Google Shopping".
30. **Top-N order** — competitor columns are ordered matched-first, then others.

## I-amazon. Amazon (lib/scrapers/generic.ts → parseAmazon)

- **Amazon ships NO JSON-LD and NO OpenGraph** — `parsePdpHtml` finds nothing. A
  dedicated DOM parser reads Amazon's proprietary markup:
  - name → `#productTitle`
  - price → FIRST `.a-offscreen` inside `#corePrice_feature_div` (the DEAL price;
    the struck MRP is in a separate basis-price block) → ₹3200, with selector
    fallbacks (`#corePriceDisplay_*`, `#price_inside_buybox`, `priceblock_*`,
    `span.a-price .a-offscreen`).
  - image → `#landingImage[data-a-dynamic-image]` (a {url:[w,h]} map) → first url.
  - description → `#feature-bullets li` (specs for matching vs DK).
  - currency → amazon.in is ₹/INR; a non-₹ symbol ($/€/£) → foreign → drop.
- **It is NOT (always) anti-bot** — a residential IP gets HTTP 200; the failure was
  purely a parsing gap. The droplet (datacenter IP) WILL captcha, so
  `fetchAmazonProduct` tries direct THEN the ScraperAPI proxy.
- A page with no price block (out of stock / dead ASIN) returns null → "couldn't
  verify" (never a wrong price). Verified on 3 live ASINs + the GC one.

## I. Known-hard (no reliable fix without the AI extractor — currently parked)

- **Pure SPA, no structured price** (dentalstores.in — React, price is plain-text
  MRP) → "couldn't verify" (no wrong price shown).
- **No price in JSON-LD** (dentganga — name parses after control-char salvage, but
  offers carry no price) → "couldn't verify".
  These need the LLM extractor + judge (needs an Anthropic API key).

## J. Operational

- **SerpAPI** 250 searches/month; a Google compare spends ~3–8. **ScraperAPI**
  credits — proxy ~1/page, render ~25/page (render disabled).
- **Confirmed memory** — ✓ keep / ✗ hide writes to SQLite `confirmed_matches`;
  reused (re-priced) next run. Keyed on the normalized DK/query name.
- **Auto-flag ⚠** — surfaces borderline matches (possible verdict, different size,
  low similarity, price ≥2× off) for a human; never silently shown as confident.
- **Prod parity** — `.env` is gitignored, so the droplet needs its own
  `SERPAPI_KEY`, `SERP_ENABLED=1`, `SCRAPER_API_KEY`, and **`PROXY_PINKBLUE=1`**
  (datacenter IP blocks pinkblue without the proxy).
