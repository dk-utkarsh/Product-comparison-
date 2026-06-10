import asyncio
import json

import pytest

from app.matching import llm_judge
from app.matching.llm_judge import JudgeBudget, judge_pair
from app.matching.structured import ProductRecord


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, payload):
        self.content = [_Block(json.dumps(payload))]


class _Messages:
    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    async def create(self, **kw):
        self.calls += 1
        return _Resp(self._payload)


class _StubClient:
    def __init__(self, payload):
        self.messages = _Messages(payload)


@pytest.fixture
def _with_key(monkeypatch):
    from app.settings import get_settings
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-test")
    yield
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "")


def _records():
    return (
        ProductRecord(name="GC Fuji IX A2", description="glass ionomer"),
        ProductRecord(name="Fuji 9 GP shade A2", description="glass ionomer caps"),
    )


def test_judge_parses_structured_verdict(monkeypatch, _with_key):
    stub = _StubClient({
        "same_product": True, "same_variant": True,
        "differences": [], "confidence": 0.92, "reason": "same GI capsules",
    })
    monkeypatch.setattr(llm_judge, "_client", stub)
    s, c = _records()
    v = asyncio.run(judge_pair(s, c, JudgeBudget(5)))
    assert v is not None and v.same_product and v.same_variant
    assert v.confidence == 0.92


def test_budget_exhausted_returns_none(monkeypatch, _with_key):
    stub = _StubClient({"same_product": True, "same_variant": True,
                        "differences": [], "confidence": 1, "reason": ""})
    monkeypatch.setattr(llm_judge, "_client", stub)
    budget = JudgeBudget(0)
    s, c = _records()
    assert asyncio.run(judge_pair(s, c, budget)) is None
    assert stub.messages.calls == 0


def test_no_api_key_returns_none(monkeypatch):
    from app.settings import get_settings
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "")
    s, c = _records()
    assert asyncio.run(judge_pair(s, c, JudgeBudget(5))) is None
