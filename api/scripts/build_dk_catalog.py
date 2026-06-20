"""
Build a LOCAL Dentalkart product catalog index from the public sitemaps.

Why: DK's on-site search has a recall floor — it won't return some products even
for their exact name (e.g. "Meril Filasilk #2-0"), though the product page exists
(Google finds it). This index lets us search the FULL catalog ourselves
(embedding nearest-neighbour) and recover the URL, then scrape that PDP.

Run once (re-run to refresh):
    cd api && uv run python -m scripts.build_dk_catalog

Output (gitignored): api/data/dk_catalog.npz  (embeddings + parallel url/name).
No Postgres needed — query-time search loads the npz and does a numpy dot product.
"""
from __future__ import annotations

import re
import ssl
import urllib.request
from pathlib import Path

import numpy as np

from app.matching.embed import get_embedder
from app.matching.normalize import normalize_for_match

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# The product sitemaps (sitemap-1-N). brands/categories sitemaps are excluded —
# they're listing pages, not products.
_PRODUCT_SITEMAPS = [
    f"https://www.dentalkart.com/pub/sitemap-1-{i}.xml" for i in range(1, 6)
]
_OUT = Path(__file__).resolve().parent.parent / "data" / "dk_catalog.npz"


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=40, context=_CTX) as r:
        return r.read().decode("utf-8", "replace")


def _name_from_url(url: str) -> str:
    """Derive a product name from the URL slug:
    '.../p/meril-filasilk-2-0-black-braided-silk-suture.html'
      -> 'meril filasilk 2 0 black braided silk suture'."""
    slug = url.rstrip("/").split("/")[-1]
    slug = re.sub(r"\.html?$", "", slug, flags=re.IGNORECASE)
    return re.sub(r"[-_]+", " ", slug).strip()


def collect_product_urls() -> list[str]:
    urls: dict[str, None] = {}
    for sm in _PRODUCT_SITEMAPS:
        try:
            xml = _fetch(sm)
        except Exception as e:  # a missing sitemap-1-5 is fine
            print(f"  skip {sm.split('/')[-1]}: {e}")
            continue
        locs = re.findall(r"<loc>([^<]+)</loc>", xml)
        prod = [
            u for u in locs
            if re.search(r"\.html?$", u) and "/c/" not in u and "/brands/" not in u
        ]
        print(f"  {sm.split('/')[-1]}: {len(prod)} products")
        for u in prod:
            urls.setdefault(u, None)
    return list(urls)


def main() -> None:
    print("Collecting product URLs from sitemaps…")
    urls = collect_product_urls()
    names = [_name_from_url(u) for u in urls]
    print(f"Total unique products: {len(urls)}")

    print("Embedding names (one-off, may take ~1 min)…")
    embedder = get_embedder()
    norms = [normalize_for_match(n) or n for n in names]
    vecs = embedder.encode_many(norms).astype(np.float32)  # already L2-normalized

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        _OUT,
        embeddings=vecs,
        urls=np.array(urls, dtype=object),
        names=np.array(names, dtype=object),
    )
    print(f"Saved {len(urls)} products -> {_OUT} ({_OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
