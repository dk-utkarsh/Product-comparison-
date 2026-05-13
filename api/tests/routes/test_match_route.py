from fastapi.testclient import TestClient

from app.main import app


def test_match_endpoint_returns_ranked_results():
    client = TestClient(app)
    res = client.post(
        "/match",
        json={
            "search": "3M Filtek Z350 XT Shade A2",
            "candidates": [
                "3M Filtek Z350 XT A2",
                "GC Fuji IX GP Capsules",
                "3M Filtek Z350 XT Shade A3",
            ],
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert "ranked" in data
    assert len(data["ranked"]) == 3
    assert data["ranked"][0]["candidate"].endswith("A2")
    assert data["ranked"][0]["verdict"] in ("confirmed", "possible")
    rejected = [r for r in data["ranked"] if r["verdict"] == "rejected"]
    rejected_names = {r["candidate"] for r in rejected}
    assert "GC Fuji IX GP Capsules" in rejected_names
    assert "3M Filtek Z350 XT Shade A3" in rejected_names


def test_match_endpoint_rejects_empty_search():
    client = TestClient(app)
    res = client.post("/match", json={"search": "", "candidates": ["x"]})
    assert res.status_code == 422
