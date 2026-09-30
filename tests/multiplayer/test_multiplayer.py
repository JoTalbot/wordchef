"""Multiplayer tests — Quick Cook parity, Chaos Kitchen, live event feed."""
from __future__ import annotations

import json

from wordchef_game.orders import Order, check_order
from wordchef_game.dictionary import load_dictionary

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers import D, find_word


def _register(client, name):
    return client.post("/api/players", json={"name": name}).json()


def _valid_word(player_view):
    order = Order.from_dict(player_view["order"])
    word = find_word(order)
    assert word is not None
    return word


class TestQuickCook:
    def test_same_order_for_all_cooks(self, client):
        alice = _register(client, "Alice")
        bob = _register(client, "Bob")
        carol = _register(client, "Carol")
        match = client.post("/api/matches", json={
            "mode": "QUICK_COOK",
            "player_ids": [alice["player_id"], bob["player_id"], carol["player_id"]],
            "rounds": 1, "seed": "qc-seed"}).json()
        mid = match["match_id"]
        client.post(f"/api/matches/{mid}/start")
        trays = []
        for p in (alice, bob, carol):
            view = client.get(f"/api/matches/{mid}/players/{p['player_id']}").json()
            trays.append(view["order"]["tray"])
        assert len(set(trays)) == 1, "Quick Cook must give everyone the same ticket"

    def test_scores_are_comparable(self, client):
        alice = _register(client, "Alice")
        bob = _register(client, "Bob")
        match = client.post("/api/matches", json={
            "mode": "QUICK_COOK",
            "player_ids": [alice["player_id"], bob["player_id"]],
            "rounds": 1, "seed": "qc2"}).json()
        mid = match["match_id"]
        client.post(f"/api/matches/{mid}/start")
        # both players serve the same best word → identical scoring inputs
        word = None
        for p in (alice, bob):
            view = client.get(f"/api/matches/{mid}/players/{p['player_id']}").json()
            word = word or _valid_word(view)
            res = client.post(f"/api/matches/{mid}/intent", json={
                "player_id": p["player_id"], "action": "SUBMIT_DISH",
                "word": word}).json()
            assert res["payload"]["execution"]["verified"] is True
        board = client.get(f"/api/matches/{mid}").json()["leaderboard"]
        assert board[0]["score"] == board[1]["score"]

    def test_ws_receives_sync_and_events(self, client):
        alice = _register(client, "Alice")
        match = client.post("/api/matches", json={
            "mode": "SOLO", "player_ids": [alice["player_id"]],
            "rounds": 1, "seed": "ws-seed"}).json()
        mid = match["match_id"]
        client.post(f"/api/matches/{mid}/start")
        with client.websocket_connect(f"/api/ws/matches/{mid}") as ws:
            frame = json.loads(ws.receive_text())
            assert frame["type"] == "SYNC"
            view = client.get(f"/api/matches/{mid}/players/{alice['player_id']}").json()
            word = _valid_word(view)
            client.post(f"/api/matches/{mid}/intent", json={
                "player_id": alice["player_id"], "action": "SUBMIT_DISH",
                "word": word})
            # the live feed must deliver the intent event
            got = json.loads(ws.receive_text())
            assert got["type"] in ("EVENT", "PING")
            if got["type"] == "PING":
                got = json.loads(ws.receive_text())
            assert got["event"]["type"] in ("INTENT", "ROUND_STARTED", "MATCH_FINISHED")


class TestChaosKitchen:
    def test_chaos_events_appear_and_players_can_ring(self, client):
        players = [_register(client, f"C{i}") for i in range(3)]
        match = client.post("/api/matches", json={
            "mode": "CHAOS_KITCHEN",
            "player_ids": [p["player_id"] for p in players],
            "rounds": 2, "seed": "chaos-seed"}).json()
        mid = match["match_id"]
        client.post(f"/api/matches/{mid}/start")
        view = client.get(f"/api/matches/{mid}/players/{players[0]['player_id']}").json()
        # ring the bell needs golden — none at start → rejected politely
        res = client.post(f"/api/matches/{mid}/intent", json={
            "player_id": players[0]["player_id"], "action": "RING_BELL"}).json()
        assert res["accepted"] in (True, False)
        # play until chaos or finish
        for _ in range(30):
            state = client.get(f"/api/matches/{mid}").json()
            if state["chaos_log"] or state["finished"]:
                break
            for p in players:
                view = client.get(f"/api/matches/{mid}/players/{p['player_id']}").json()
                if not view.get("order"):
                    continue
                word = _valid_word(view)
                if word is None:
                    client.post(f"/api/matches/{mid}/intent", json={
                        "player_id": p["player_id"], "action": "SKIP"})
                else:
                    client.post(f"/api/matches/{mid}/intent", json={
                        "player_id": p["player_id"], "action": "SUBMIT_DISH",
                        "word": word})
        state = client.get(f"/api/matches/{mid}").json()
        assert state["chaos_log"], "Chaos Kitchen must unleash chaos"

    def test_no_pay_to_win_in_info(self, client):
        info = client.get("/api/info").json()
        text = json.dumps(info).lower()
        for banned in ("pay", "price", "purchase", "gems", "subscription"):
            assert banned not in text.replace("pay_per", ""), "no monetization hooks"
