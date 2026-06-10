"""
LLM borderline judge (Approach C of the exact-match spec).

Claude Haiku decides "same exact product? same variant?" for pairs the
structured matcher couldn't settle. Strict JSON via structured outputs.
Every failure path degrades to None — the caller maps that to POSSIBLE,
never to a silent CONFIRMED.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import anthropic

from app.matching.structured import ProductRecord
from app.settings import get_settings

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)
    return _client


@dataclass(slots=True)
class JudgeVerdict:
    same_product: bool
    same_variant: bool
    differences: list[str]
    confidence: float
    reason: str


class JudgeBudget:
    """Per-run cap on judge calls. take() returns False once exhausted."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def take(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "same_product": {"type": "boolean"},
        "same_variant": {"type": "boolean"},
        "differences": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["same_product", "same_variant", "differences", "confidence", "reason"],
    "additionalProperties": False,
}


def _render(label: str, r: ProductRecord) -> str:
    parts = [f"{label} name: {r.name}"]
    if r.sku:
        parts.append(f"{label} sku: {r.sku}")
    if r.packaging:
        parts.append(f"{label} packaging: {r.packaging[:400]}")
    if r.description:
        parts.append(f"{label} description: {r.description[:800]}")
    if r.pack_size > 1:
        parts.append(f"{label} pack size: {r.pack_size}")
    if r.unit_price > 0:
        parts.append(f"{label} unit price: INR {r.unit_price:.2f}")
    return "\n".join(parts)


def _prompt(search: ProductRecord, candidate: ProductRecord) -> str:
    return (
        "You are a dental-products catalog expert. Decide whether these two "
        "listings (from different Indian dental e-commerce sites) are the SAME "
        "exact product, and whether they are the same VARIANT (same shade, "
        "size, dimension, concentration, type). Pack quantity differences do "
        "NOT make a different variant. Be strict: if the variant cannot be "
        "established as identical, same_variant is false.\n\n"
        f"{_render('A', search)}\n\n{_render('B', candidate)}\n\n"
        "confidence is 0..1. differences lists concrete attribute differences. "
        "reason is one short sentence."
    )


async def judge_pair(
    search: ProductRecord, candidate: ProductRecord, budget: JudgeBudget
) -> JudgeVerdict | None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    if not budget.take():
        return None
    try:
        resp = await _get_client().messages.create(
            model=settings.llm_judge_model,
            max_tokens=1024,
            output_config={"format": {"type": "json_schema", "schema": _JUDGE_SCHEMA}},
            messages=[{"role": "user", "content": _prompt(search, candidate)}],
        )
    except anthropic.APIError:
        return None
    text = "".join(
        getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text"
    )
    try:
        data = json.loads(text)
    except ValueError:
        return None
    try:
        return JudgeVerdict(
            same_product=bool(data["same_product"]),
            same_variant=bool(data["same_variant"]),
            differences=[str(d) for d in data.get("differences", [])],
            confidence=float(data["confidence"]),
            reason=str(data.get("reason", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None
