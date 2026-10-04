from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_reports_version_and_security_headers(client: TestClient):
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["game"] == "wordchef"
    assert payload["version"] == "1.4.3"
    assert isinstance(payload["uptime_s"], (int, float))
    assert payload["uptime_s"] >= 0
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert response.headers["cache-control"] == "no-store"


def test_registration_normalizes_name(client: TestClient):
    response = client.post("/api/players", json={"name": "  Chef   Bruno  "})
    assert response.status_code == 200
    assert response.json()["name"] == "Chef Bruno"


def test_registration_rejects_blank_name(client: TestClient):
    response = client.post("/api/players", json={"name": "   "})
    assert response.status_code == 422


def test_match_round_and_player_limits_are_rejected(client: TestClient):
    player = client.post("/api/players", json={"name": "Chef"}).json()
    response = client.post("/api/matches", json={
        "mode": "SOLO",
        "player_ids": [player["player_id"]],
        "rounds": 13,
    })
    assert response.status_code == 422


def test_leaderboard_limit_is_bounded(client: TestClient):
    response = client.get("/api/leaderboard?limit=101")
    assert response.status_code == 422


def test_dictionary_query_has_length_limit(client: TestClient):
    response = client.get("/api/dictionary/check", params={"word": "x" * 65})
    assert response.status_code == 422
