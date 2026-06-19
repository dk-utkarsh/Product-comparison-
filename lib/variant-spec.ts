/**
 * Sub-variant / composition parser.
 *
 * Many dental products are one "base product" sold in several sub-variants that
 * differ only by SIZE or COMPOSITION — e.g. GC Gold Label 9 ships as
 *   "15g Powder + 13.1g (10.5mL) Liquid"  (Big Pack)
 *   "5g Powder + 5g (4ml) Liquid"         (Mini Pack)
 * plus a separate "Extra" formulation line. Matching on the (identical) base
 * name alone lands on the wrong size and produces a misleading price delta.
 *
 * This module turns free-text (name / packaging / variant title / spec table)
 * into a structured {@link VariantSpec} so the matcher can:
 *   1. require the SAME formulation line (Extra vs non-Extra), and
 *   2. prefer the SAME size, falling back to the closest size with a per-unit
 *      price comparison (Dentalkart is the source of truth for the target spec).
 *
 * Pure module — no I/O. Used by both the TS scrapers and (via the bridge) the
 * Python matcher.
 */

export interface VariantSpec {
  powderG?: number; // grams of powder
  liquidG?: number; // grams of liquid
  liquidMl?: number; // millilitres of liquid
  capsules?: number; // capsule count
  pieces?: number; // generic count: "pack of N", "N pcs", "set of N"
  isExtra: boolean; // "Extra" formulation line — a DIFFERENT product, never cross-match
  sizeTier: "big" | "mini" | null; // explicit Big/Mini pack label when present
  // Configuration tier (kit grade) — e.g. surgical boxes sold as
  // Only / Set-of-N / Basic / Basic Plus / Premium. A DIFFERENT product per
  // tier, never cross-match.
  kitTier?: string;
  // Ratchet/mechanism descriptor: "torque" vs "non-torque" — also a hard config
  // discriminator (Basic+NonTorque ≠ Basic Plus+Torque).
  torque?: "torque" | "non-torque";
  raw: string; // source text, for debugging/reasons
}

/** Configuration tier from a kit name. Ordered so "basic plus" wins over
 *  "basic" (substring) and the most specific grade is returned. */
function parseKitTier(lower: string): string | undefined {
  if (/\bpremium\b/.test(lower)) return "premium";
  if (/\bbasic\s*\+?\s*plus\b/.test(lower) || /\bbasic\s*\+/.test(lower)) return "basic plus";
  if (/\bbasic\b/.test(lower)) return "basic";
  if (/\bdeluxe\b/.test(lower)) return "deluxe";
  if (/\bstandard\b/.test(lower)) return "standard";
  if (/\bset\s+of\s+\d+\b/.test(lower)) return "set";
  if (/\bonly\b/.test(lower)) return "only";
  return undefined;
}

function parseTorque(lower: string): "torque" | "non-torque" | undefined {
  if (/\bnon[\s-]?torque\b/.test(lower)) return "non-torque";
  if (/\btorque\b/.test(lower)) return "torque";
  return undefined;
}

const NUM = String.raw`(\d+(?:\.\d+)?)`;

/** Numeric quantity in `<qtyUnit>` (e.g. "g"/"ml") closest to `contextWord`.
 *  Robust to "15 g Powder + 13.1 g (10.5mL) Liquid" — assigns each amount to the
 *  nearest keyword rather than greedily grabbing the first number. */
function near(text: string, qtyUnit: string, contextWord: string): number | undefined {
  const wordRe = new RegExp(`\\b${contextWord}\\b`, "gi");
  const numRe = new RegExp(`${NUM}\\s*${qtyUnit}\\b`, "gi");
  const wordPos = [...text.matchAll(wordRe)].map((m) => m.index ?? -1).filter((i) => i >= 0);
  if (!wordPos.length) return undefined;
  let best: { val: number; dist: number } | undefined;
  for (const m of text.matchAll(numRe)) {
    const pos = m.index ?? -1;
    const val = parseFloat(m[1]);
    if (pos < 0 || !Number.isFinite(val)) continue;
    for (const wp of wordPos) {
      const dist = Math.abs(wp - pos);
      if (dist <= 30 && (!best || dist < best.dist)) best = { val, dist };
    }
  }
  return best?.val;
}

function firstInt(text: string, re: RegExp, lo = 1, hi = 100000): number | undefined {
  const m = text.match(re);
  if (!m) return undefined;
  const n = parseInt(m[1], 10);
  return n >= lo && n <= hi ? n : undefined;
}

/**
 * Parse a composition spec out of arbitrary product text. Combine all the text
 * you have (name + packaging + description + variant title) into one string.
 */
export function parseVariantSpec(text: string): VariantSpec {
  const t = (text || "").replace(/\s+/g, " ").trim();
  const lower = t.toLowerCase();

  const powderG = near(t, "g", "powder");
  // Liquid can be measured in g and/or ml; capture whichever is present.
  const liquidG = near(t, "g", "liquid");
  // ml is almost always the liquid; take the first ml figure.
  const mlMatch = lower.match(new RegExp(`${NUM}\\s*ml\\b`, "i"));
  const liquidMl = mlMatch ? parseFloat(mlMatch[1]) : undefined;

  const capsules = firstInt(lower, new RegExp(`${NUM}\\s*caps?(?:ules?)?\\b`, "i"), 2);

  // Generic piece count: "pack of N", "set of N", "N pcs/pieces/nos".
  const pieces =
    firstInt(lower, /(?:pack|set|combo|pk|box)\s*(?:of\s*)?(\d+)/i, 2) ??
    firstInt(lower, /(\d+)\s*(?:pcs|pieces?|pc|nos?|units?)\b/i, 2);

  // "Extra" is a distinct formulation line, not a size. Match as a whole word.
  const isExtra = /\bextra\b/i.test(lower);

  let sizeTier: VariantSpec["sizeTier"] = null;
  if (/\bbig\b|\bmaxi\b/i.test(lower)) sizeTier = "big";
  else if (/\bmini\b|\bsmall\b|\btrial\b/i.test(lower)) sizeTier = "mini";

  return {
    powderG, liquidG, liquidMl, capsules, pieces, isExtra, sizeTier,
    kitTier: parseKitTier(lower), torque: parseTorque(lower), raw: t,
  };
}

/** True if the spec carries any size/composition/config signal at all. */
export function hasSizeSignal(s: VariantSpec): boolean {
  return (
    s.powderG !== undefined ||
    s.liquidG !== undefined ||
    s.liquidMl !== undefined ||
    s.capsules !== undefined ||
    s.pieces !== undefined ||
    s.sizeTier !== null ||
    s.kitTier !== undefined ||
    s.torque !== undefined
  );
}

/** Hard configuration mismatch — Extra line, kit tier, or torque type differs.
 *  These are different PRODUCTS and must never cross-match. */
function configMismatch(a: VariantSpec, b: VariantSpec): boolean {
  if (a.isExtra !== b.isExtra) return true;
  if (a.kitTier && b.kitTier && a.kitTier !== b.kitTier) return true;
  if (a.torque && b.torque && a.torque !== b.torque) return true;
  return false;
}

const REL_TOL = 0.05; // 5% — tolerate "13.1g" vs "13g" rounding across sites.

function approxEq(a?: number, b?: number): boolean | null {
  if (a === undefined || b === undefined) return null; // unknown on a side
  if (a === 0 && b === 0) return true;
  const bigger = Math.max(Math.abs(a), Math.abs(b)) || 1;
  return Math.abs(a - b) / bigger <= REL_TOL;
}

/** Coarse big/mini tier, derived from powder grams when no explicit label. */
function tierOf(s: VariantSpec): "big" | "mini" | null {
  if (s.sizeTier) return s.sizeTier;
  if (s.powderG !== undefined) return s.powderG >= 10 ? "big" : "mini";
  return null;
}

export type SpecMatch = "exact" | "same-tier" | "different" | "unknown";

/**
 * Compare a competitor spec against the Dentalkart (truth) spec.
 *  - "exact"      → same formulation + same measured size (safe to confirm + headline Δ)
 *  - "same-tier"  → same formulation + same Big/Mini tier but grams differ a bit
 *  - "different"  → different formulation (Extra vs not) or different size
 *  - "unknown"    → not enough signal to decide
 */
export function compareSpecToTruth(truth: VariantSpec, cand: VariantSpec): SpecMatch {
  // Configuration must agree: Extra line, kit tier, torque type. Any mismatch
  // means a different product, full stop.
  if (configMismatch(truth, cand)) return "different";

  if (!hasSizeSignal(truth) || !hasSizeSignal(cand)) return "unknown";

  const checks = [
    approxEq(truth.powderG, cand.powderG),
    approxEq(truth.liquidG, cand.liquidG),
    approxEq(truth.liquidMl, cand.liquidMl),
    approxEq(truth.capsules, cand.capsules),
    approxEq(truth.pieces, cand.pieces),
    // Categorical config: present on both ⇒ equal here (mismatch returned
    // above), so it's a positive exact signal.
    truth.kitTier && cand.kitTier ? true : null,
    truth.torque && cand.torque ? true : null,
  ];
  const comparable = checks.filter((c) => c !== null) as boolean[];

  if (comparable.length > 0) {
    if (comparable.every(Boolean)) return "exact";
    // Some measured field disagreed — fall back to tier comparison.
    const tt = tierOf(truth);
    const ct = tierOf(cand);
    if (tt && ct) return tt === ct ? "same-tier" : "different";
    return "different";
  }

  // No measured field comparable on both sides — lean on the tier label.
  const tt = tierOf(truth);
  const ct = tierOf(cand);
  if (tt && ct) return tt === ct ? "same-tier" : "different";
  return "unknown";
}

/**
 * Quantity used to normalize price to a per-unit basis, with its unit, so a
 * "15g" listing and a "5g" listing (or pack-of-6 vs single) compare fairly.
 * Falls back to 1 (treat as a single unit) when no quantity is known.
 */
export function baseQuantity(s: VariantSpec): { qty: number; unit: string } {
  if (s.powderG !== undefined && s.powderG > 0) return { qty: s.powderG, unit: "g powder" };
  if (s.capsules !== undefined && s.capsules > 0) return { qty: s.capsules, unit: "capsule" };
  if (s.pieces !== undefined && s.pieces > 0) return { qty: s.pieces, unit: "piece" };
  if (s.liquidMl !== undefined && s.liquidMl > 0) return { qty: s.liquidMl, unit: "ml" };
  return { qty: 1, unit: "unit" };
}

/** Short human-readable form for reasons/UI, e.g. "15g powder + 13.1g liquid". */
export function describeSpec(s: VariantSpec): string {
  const parts: string[] = [];
  if (s.isExtra) parts.push("Extra");
  if (s.kitTier) parts.push(s.kitTier);
  if (s.torque) parts.push(s.torque);
  if (s.powderG !== undefined) parts.push(`${s.powderG}g powder`);
  if (s.liquidG !== undefined) parts.push(`${s.liquidG}g liquid`);
  else if (s.liquidMl !== undefined) parts.push(`${s.liquidMl}ml liquid`);
  if (s.capsules !== undefined) parts.push(`${s.capsules} capsules`);
  if (s.pieces !== undefined) parts.push(`pack of ${s.pieces}`);
  if (!parts.length && s.sizeTier) parts.push(`${s.sizeTier} pack`);
  return parts.join(" + ") || "(no size spec)";
}
