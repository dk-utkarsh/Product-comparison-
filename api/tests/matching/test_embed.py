import numpy as np

from app.matching.embed import Embedder


def test_embedder_produces_normalized_384_vec():
    e = Embedder()
    v = e.encode_one("3M Filtek Z350 XT")
    assert v.shape == (384,)
    assert abs(np.linalg.norm(v) - 1.0) < 1e-3


def test_embedder_batch():
    e = Embedder()
    vs = e.encode_many(["GC Fuji IX", "3M Filtek Z350"])
    assert vs.shape == (2, 384)


def test_cosine_self_is_one():
    e = Embedder()
    v = e.encode_one("Dentsply ProTaper F2")
    sim = float(v @ v)
    assert sim > 0.999
