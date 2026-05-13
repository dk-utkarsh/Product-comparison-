"""
Smart progressive query builder. Port of lib/keyword-extractor.ts.

Given a product name + the canonical Dentalkart description/packaging/SKU,
emit 4-8 search queries in priority order (specific -> broad). Competitor
scrapers try each query and we pool the unique candidates before matching.

Why progressive: the xlsx name is often too specific for a competitor's
search index. "OrthoMetric Flexy NiTi Thermal 35C Round Archwire - 018
Upper(51.35.2018)" may return zero hits, but "OrthoMetric Archwire" finds
the listing instantly. We try both and dedup the candidates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Known dental product categories — ordered longest-first to prefer
# "diamond bur" over "bur" when both could match.
_PRODUCT_TYPES: list[str] = [
    # Instruments
    "rongeur", "forceps", "elevator", "excavator", "scaler", "curette",
    "explorer", "probe", "mirror", "retractor", "plugger", "spreader",
    "condenser", "scissors", "plier", "pliers", "cutter", "clamp",
    "tweezer", "needle holder", "matrix",
    # Rotary
    "handpiece", "bur", "burs", "diamond bur", "carbide bur",
    "contra angle", "turbine",
    # Endodontic
    "file", "files", "k-file", "h-file", "rotary file",
    "gutta percha", "obturator", "apex locator", "endomotor",
    # Restorative
    "composite", "resin", "cement", "adhesive", "bonding", "primer",
    "sealant", "sealer", "liner", "etchant", "etching",
    "filling", "restoration",
    # Prosthodontic
    "crown", "bridge", "veneer", "denture", "impression",
    "alginate", "silicone", "articular", "facebow", "articulator",
    # Orthodontic
    "bracket", "brackets", "wire", "wires", "elastic", "elastics",
    "band", "bands", "archwire", "ligature",
    # Consumables
    "gloves", "mask", "gown", "syringe", "needle", "needles",
    "cotton", "gauze", "suture", "blade", "polystrip", "polystrips",
    "sterilization", "pouch", "disinfectant",
    # Implant
    "implant", "abutment", "bone graft", "membrane",
    "bone plate", "bone screw",
    # Whitening
    "bleaching", "whitening",
    # Equipment
    "curing light", "light cure", "scanner", "camera",
    "chair", "unit", "autoclave", "ultrasonic",
    # Materials
    "wax", "investment", "acrylic", "stone", "plaster",
    "coating", "varnish", "protective coating",
    "refill", "refills", "tip refills", "cartridge",
    "composite system", "bulk fill composite",
    # Hygiene
    "toothbrush", "brush", "paste", "mouthwash", "floss", "gel",
    # Positioning tools
    "height gauge", "positioning gauge", "gauge",
    "caliper", "ruler", "measuring",
    "luxating instrument", "periotome",
    # Other
    "badge", "tray", "articulating paper",
]

_NOISE: frozenset[str] = frozenset({
    "dental", "dentist", "for", "with", "and", "the", "of", "in", "to",
    "a", "an", "is", "on", "by", "from", "new", "original", "genuine",
    "buy", "online", "price", "best", "india", "product", "products",
    "free", "delivery", "shipping", "sale", "offer", "discount",
    "medical", "surgical", "supplies", "equipment", "devices",
    "type", "shape", "style", "model", "series", "version",
    "ss", "stainless", "steel", "special", "pack",
})

_CODE_RE = re.compile(r"\b([A-Z]{1,3}-?\d{3,6}[A-Z]?)\b")
_PAREN_CODE_RE = re.compile(r"\(([A-Z0-9-]{3,12})\)")


@dataclass(slots=True, frozen=True)
class ProductContext:
    brand: str | None = None
    manufacturer: str | None = None
    description: str | None = None
    packaging: str | None = None
    sku: str | None = None


def _clean_text(text: str) -> str:
    out = re.sub(r"<[^>]*>", " ", text)
    out = re.sub(r"\([^)]*\)", "", out)
    out = re.sub(r"\b[A-Z]{2,5}\d{1,4}\b", "", out)
    out = re.sub(r"\bsize\s+[\d/.\-]+\s*(oz|mm|cm|ml|gm)?\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\b\d+/\d+\b", "", out)
    out = re.sub(
        r"\b\d+(\.\d+)?\s*(cm|mm|ml|gm|gms|kg|inch|inches|degree|oz)\b",
        "",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(r"\b(pack|set|combo|box|kit)\s*(of\s*)?\d+\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\b\d+\s*(pcs|pieces?|units?|nos?|pc|tips?)\b", "", out, flags=re.IGNORECASE)
    out = re.sub(
        r"\b(micro|mini|premium|professional|standard|regular|extra|super)\b",
        "",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(r"\b(small|medium|large|xl|xxl|light|heavy)\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s*-\s*", " ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _split_words(text: str) -> list[str]:
    return [w for w in text.split() if len(w) > 1]


_BRAND_PREFIX_RE = re.compile(r"^[a-z0-9]+", re.IGNORECASE)


def _strip_to_prefix(word: str) -> str:
    """Return the first alpha-numeric chunk of a hyphenated word.
    "LM-SlimLift" -> "LM", "3M" -> "3M", "OrthoMetric" -> "OrthoMetric"."""
    m = _BRAND_PREFIX_RE.match(word)
    return m.group(0) if m else word


def _pick_brand(name_words: list[str], ctx: ProductContext) -> str:
    """Pick brand. Prefer how it appears in the name over the DB brand —
    "LM" beats "LM Dental" because that's how DK catalogs it. Strip
    hyphen suffixes so "LM-SlimLift" reduces to "LM" for the brand."""
    brand_from_name = [
        _strip_to_prefix(w)
        for w in name_words
        if w.lower() not in _NOISE and not w.isdigit()
    ]
    db_brand = (ctx.brand or "").strip()
    if not db_brand or len(db_brand) <= 1 or "<" in db_brand:
        db_brand = (ctx.manufacturer or "").strip()

    if db_brand and len(db_brand) > 1:
        db_first = _strip_to_prefix(db_brand.split()[0]).lower()
        name_first = brand_from_name[0].lower() if brand_from_name else ""
        if name_first and (
            name_first == db_first
            or db_first in name_first
            or name_first in db_first
        ):
            return brand_from_name[0]
        return brand_from_name[0] if brand_from_name else _strip_to_prefix(db_brand.split()[0])
    return brand_from_name[0] if brand_from_name else ""


def _pick_product_type(clean_name: str, ctx: ProductContext) -> str:
    """Find the longest-matching dental product category in the name first,
    then in description/packaging."""
    name_lower = clean_name.lower()
    sorted_types = sorted(_PRODUCT_TYPES, key=len, reverse=True)
    for t in sorted_types:
        if t in name_lower:
            return t
    haystack = " ".join(
        [name_lower, (ctx.description or "").lower(), (ctx.packaging or "").lower()]
    )
    for t in sorted_types:
        if t in haystack:
            return t
    return ""


def _pick_model_from_description(clean_name: str, ctx: ProductContext) -> str:
    if not ctx.description:
        return ""
    for w in ctx.description.split():
        if (
            len(w) > 2
            and w[0].isupper()
            and w.lower() not in _NOISE
            and w.lower() not in clean_name.lower()
        ):
            return w
    return ""


def extract_smart_queries(name: str, ctx: ProductContext | None = None) -> list[str]:
    """Build progressive search queries (specific -> broad). Always returns
    at least one query; usually 4-8. Output is deduped case-insensitively."""
    ctx = ctx or ProductContext()
    clean = _clean_text(name)
    name_words = _split_words(clean)

    brand = _pick_brand(name_words, ctx)
    product_type = _pick_product_type(clean, ctx)
    type_lower = product_type.lower()
    brand_lower = brand.lower()

    product_line = [
        w for w in name_words
        if (
            w.lower() not in _NOISE
            and not w.isdigit()
            and brand_lower not in w.lower()
            and (not type_lower or type_lower not in w.lower())
            and len(w) > 1
        )
    ]

    model_from_desc = _pick_model_from_description(clean, ctx)

    queries: list[str] = []
    seen: set[str] = set()

    def add(parts: list[str]) -> None:
        q = " ".join(p for p in parts if p).strip()
        if len(q) >= 3 and q.lower() not in seen:
            seen.add(q.lower())
            queries.append(q)

    # Q1: brand + product line + product type (most specific)
    q1: list[str] = [brand, *product_line[:2]]
    if product_type and not any(type_lower in p.lower() for p in q1):
        q1.append(product_type)
    add(q1[:5])

    # Q2: brand + product type
    if product_type:
        add([brand, product_type])

    # Q3: brand + model from description + product type
    if model_from_desc:
        add([brand, model_from_desc, product_type])

    # Q4: brand + first product-line word
    if product_line:
        add([brand, product_line[0]])

    # Q5: brand + extracted product code(s)
    codes = []
    seen_codes: set[str] = set()
    for m in _CODE_RE.finditer(name):
        c = m.group(1)
        if len(c) >= 4 and not c.isdigit() and c not in seen_codes:
            seen_codes.add(c)
            codes.append(c)
    for m in _PAREN_CODE_RE.finditer(name):
        c = m.group(1)
        if len(c) >= 4 and not c.isdigit() and c not in seen_codes:
            seen_codes.add(c)
            codes.append(c)
    for code in codes[:2]:
        add([brand, code])
        add([code])

    # Q6: SKU from context
    if ctx.sku:
        add([brand, ctx.sku])
        add([ctx.sku])

    # Q7: just product line + type (no brand — some sites don't index by brand prefix)
    if len(product_line) >= 2:
        nobrand = list(product_line[:3])
        if product_type and not any(type_lower in p.lower() for p in nobrand):
            nobrand.append(product_type)
        add(nobrand)

    # Q8: just series name (2 words from product line)
    if product_line:
        series = " ".join(product_line[:2])
        if len(series) >= 4:
            add([series])

    # Fallback: meaningful words from the name
    if not queries:
        fallback = [w for w in name_words if w.lower() not in _NOISE and not w.isdigit()][:4]
        add(fallback)

    return queries
