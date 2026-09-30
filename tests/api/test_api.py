"""API tests — the full REST surface, server-authoritative semantics."""
from __future__ import annotations

from wordchef_game.dictionary import load_dictionary
from wordchef_game.orders import check_order, Order

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers import D, find_word


def _register(client, name="Chef"):
    return client.post("/api/players", json={"name": name}).json()


def _match(client, mode="SOLO", rounds=1, seed="api-seed"):
    players = [_register(client, "Alice")]
    if mode != "SOLO":
        players.append(_register(client, "Bob"))
    body = {"mode": mode, "player_ids": [p["player_id"] for p in players],
            "rounds": rounds, "seed": seed}
    match = client.post("/api/matches", json=body).json()
    return match, players


def _valid_word(player_view):
    order = Order.from_dict(player_view["order"])
    word = find_word(order)
    assert word is not None
    return word


class TestApi:
    def test_info(self, client):
        data = client.get("/api/info").json()
        assert data["game"] == "Word Chef"
        assert "QUICK_COOK" in data["modes"]
        assert len(data["kitchens"]) >= 7

    def test_register_and_progression(self, client):
        player = _register(client, "Nigella")
        assert player["player_id"].startswith("p_")
        prog = client.get(f"/api/players/{player['player_id']}/progression").json()
        assert prog["kitchen"] == "street"

    def test_dictionary_check(self, client):
        data = client.get("/api/dictionary/check", params={"word": "Bread"}).json()
        assert data["is_word"] is True
        data = client.get("/api/dictionary/check", params={"word": "xqzv"}).json()
        assert data["is_word"] is False

    def test_match_lifecycle(self, client):
        match, players = _match(client)
        mid = match["match_id"]
        started = client.post(f"/api/matches/{mid}/start").json()
        assert started["round_active"] is True
        view = client.get(f"/api/matches/{mid}/players/{players[0]['player_id']}").json()
        assert view["order"] is not None
        word = _valid_word(view)
        res = client.post(f"/api/matches/{mid}/intent", json={
            "player_id": players[0]["player_id"], "action": "SUBMIT_DISH",
            "word": word}).json()
        assert res["reason"] in ("dish_served", "streak_progress")
        assert res["payload"]["execution"]["verified"] is True
        assert res["payload"]["player"]["score"] > 0

    def test_invalid_word_scores_zero(self, client):
        match, players = _match(client)
        mid = match["match_id"]
        client.post(f"/api/matches/{mid}/start")
        res = client.post(f"/api/matches/{mid}/intent", json={
            "player_id": players[0]["player_id"], "action": "SUBMIT_DISH",
            "word": "zzzz"}).json()
        assert res["accepted"] is True
        assert res["reason"] == "not_in_dictionary"
        assert res["payload"]["player"]["score"] == 0

    def test_unknown_match_404(self, client):
        assert client.get("/api/matches/m_missing").status_code == 404

    def test_bad_mode_rejected(self, client):
        player = _register(client)
        resp = client.post("/api/matches", json={
            "mode": "PAY_TO_WIN", "player_ids": [player["player_id"]], "rounds": 1})
        assert resp.status_code == 400

    def test_intent_before_start_rejected(self, client):
        match, players = _match(client)
        resp = client.post(f"/api/matches/{match['match_id']}/intent", json={
            "player_id": players[0]["player_id"], "action": "SUBMIT_DISH", "word": "art"})
        assert resp.status_code == 409

    def test_leaderboard_updates_after_match(self, client):
        match, players = _match(client, rounds=1, seed="lb-seed")
        mid = match["match_id"]
        client.post(f"/api/matches/{mid}/start")
        for _ in range(12):
            state = client.get(f"/api/matches/{mid}").json()
            if state["finished"]:
                break
            for p in players:
                view = client.get(f"/api/matches/{mid}/players/{p['player_id']}").json()
                if not view.get("order"):
                    continue
                try:
                    word = _valid_word(view)
                except StopIteration:
                    client.post(f"/api/matches/{mid}/intent", json={
                        "player_id": p["player_id"], "action": "SKIP"})
                    continue
                client.post(f"/api/matches/{mid}/intent", json={
                    "player_id": p["player_id"], "action": "SUBMIT_DISH",
                    "word": word})
        board = client.get("/api/leaderboard").json()["entries"]
        assert board and board[0]["score"] > 0
        assert board[0]["position"] == 1

    def test_audit_endpoints(self, client):
        match, players = _match(client)
        mid = match["match_id"]
        client.post(f"/api/matches/{mid}/start")
        view = client.get(f"/api/matches/{mid}/players/{players[0]['player_id']}").json()
        word = _valid_word(view)
        res = client.post(f"/api/matches/{mid}/intent", json={
            "player_id": players[0]["player_id"], "action": "SUBMIT_DISH",
            "word": word}).json()
        execution_id = res["payload"]["execution"]["execution_id"]
        rows = client.get("/api/audit/executions", params={"match_id": mid}).json()["executions"]
        assert rows and all(r["digest"] for r in rows)
        detail = client.get(f"/api/audit/executions/{execution_id}").json()
        assert detail["status"]["execution_id"] == execution_id
        assert detail["artifacts"]
        events = client.get("/api/audit/events", params={"execution_id": execution_id}).json()["events"]
        assert any(e["type"] == "execution.completed" for e in events)

    def test_prolepsis_platform_endpoints(self, client):
        assert client.get("/api/prolepsis/health").json()["status"] == "ok"
        assert client.get("/api/prolepsis/ready").json()["status"] == "ready"
        version = client.get("/api/prolepsis/version").json()
        assert version["prolepsis"] == "0.39.0"

    def test_verify_endpoint(self, client):
        match, players = _match(client, rounds=1, seed="ver-seed")
        mid = match["match_id"]
        client.post(f"/api/matches/{mid}/start")
        for _ in range(10):
            state = client.get(f"/api/matches/{mid}").json()
            if state["finished"]:
                break
            for p in players:
                view = client.get(f"/api/matches/{mid}/players/{p['player_id']}").json()
                if not view.get("order"):
                    continue
                try:
                    word = _valid_word(view)
                except StopIteration:
                    continue
                client.post(f"/api/matches/{mid}/intent", json={
                    "player_id": p["player_id"], "action": "SUBMIT_DISH",
                    "word": word})
        verdict = client.post(f"/api/matches/{mid}/verify").json()
        assert verdict["verdict"] == "verified"
        assert verdict["verify_execution"]["verified"] is True


class TestLevels:
    def test_level_endpoint(self, client):
        data = client.get("/api/levels/3").json()
        assert data["level_no"] == 3
        assert data["wheel"] and data["board"]

    def test_level_complete_validates(self, client):
        lv = client.get("/api/levels/3").json()
        words = [b["word"] for b in lv["board"]]
        res = client.post("/api/levels/complete", json={"level_no": 3, "words": words}).json()
        assert res["coins"] > 0
        assert res["execution"]["verified"] is True
        # idempotent on replay
        res2 = client.post("/api/levels/complete", json={"level_no": 3, "words": words}).json()
        assert res2["already_done"] is True
        # faked completion rejected
        bad = client.post("/api/levels/complete",
                          json={"level_no": 4, "words": ["ыыыы"]})
        assert bad.status_code == 422
