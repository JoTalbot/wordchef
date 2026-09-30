"""Persistence & restart tests — the kitchen survives a blackout.

* the SQLite store round-trips players/matches/intents/executions
* a service restart recovers match state and continues play
* Prolepsis execution envelopes survive a runtime restart (verified replay)
* checkpoints exist for the important executions
"""
from __future__ import annotations

from pathlib import Path

from wordchef_prolepsis import ops
from wordchef_prolepsis.bridge import WordChefRuntime
from wordchef_game.engine import start_match, start_round, submit_dish
from wordchef_game.orders import check_order, Order
from wordchef_game.dictionary import load_dictionary

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers import D, find_word


class TestStore:
    def test_players_roundtrip(self, tmp_root):
        from app.store import Store
        store = Store(tmp_root / "s.db")
        store.upsert_player("p1", "Alice")
        assert store.get_player("p1")["name"] == "Alice"
        store.add_xp("p1", 500, golden=3)
        assert store.get_player("p1")["xp"] == 500
        assert store.get_player("p1")["golden_earned"] == 3
        store.close()

    def test_match_and_intents_roundtrip(self, tmp_root):
        from app.store import Store
        store = Store(tmp_root / "s.db")
        store.save_match("m1", "SOLO", "seed", "street", 3, "playing", {"x": 1})
        row = store.get_match("m1")
        assert row["state"] == {"x": 1}
        store.record_intent("m1", "p1", 1, {"action": "SKIP"}, {"accepted": True})
        intents = store.intents("m1")
        assert intents[0]["intent"]["action"] == "SKIP"
        assert intents[0]["outcome"]["accepted"] is True
        store.close()

    def test_executions_and_leaderboard(self, tmp_root):
        from app.store import Store
        store = Store(tmp_root / "s.db")
        store.record_execution({"execution_id": "e1", "op": "dish.submit",
                                "request_id": "r1", "match_id": "m1",
                                "state": "COMPLETED", "digest": "sha256:x",
                                "verified": True, "checkpoint_id": "chk_1",
                                "artifact_refs": [{"id": "sha256:a"}]})
        rows = store.executions("m1")
        assert rows[0]["verified"] is True
        assert rows[0]["artifact_refs"][0]["id"] == "sha256:a"
        store.upsert_leaderboard("s", "p1", 100, 5)
        store.upsert_leaderboard("s", "p1", 50, 2)
        board = store.leaderboard("s")
        assert board[0]["score"] == 150 and board[0]["dishes"] == 7
        assert board[0]["position"] == 1
        store.close()


class TestRestart:
    def test_service_restart_resumes_match(self, tmp_root):
        from app.service import GameService
        from app.store import Store

        store = Store(tmp_root / "game.db")
        rt = WordChefRuntime(tmp_root / "prolepsis")
        svc = GameService(store, rt)
        player = svc.register_player("Restarto")
        view = svc.create_match(mode="SOLO", player_ids=[player["player_id"]],
                                rounds=2, seed="restart-seed")
        mid = view["match_id"]
        svc.start_match(mid)
        before = svc.player_view(mid, player["player_id"])
        word = find_word(Order.from_dict(before["order"]))
        svc.submit_intent(mid, player["player_id"],
                          {"action": "SUBMIT_DISH", "word": word})
        score_before = svc.player_view(mid, player["player_id"])["score"]
        rt.shutdown()
        store.close()

        # ── blackout: brand new service objects, same disk ──
        store2 = Store(tmp_root / "game.db")
        rt2 = WordChefRuntime(tmp_root / "prolepsis")
        svc2 = GameService(store2, rt2)
        after = svc2.player_view(mid, player["player_id"])
        assert after["score"] == score_before
        assert after["order"] is not None
        # play continues after restart
        word2 = find_word(Order.from_dict(after["order"]))
        res = svc2.submit_intent(mid, player["player_id"],
                                 {"action": "SUBMIT_DISH", "word": word2})
        assert res["accepted"] is True
        rt2.shutdown()
        store2.close()

    def test_prolepsis_executions_survive_restart(self, tmp_root):
        rt = WordChefRuntime(tmp_root / "prolepsis")
        state = start_match(match_id="m1", mode="SOLO", seed="s", player_ids=["a"], now=0)
        op, events, rid = ops.op_create_match(state)
        record = rt.execute_op(op, events, request_id=rid)
        execution_id = record.execution_id
        digest = record.digest
        rt.shutdown()

        rt2 = WordChefRuntime(tmp_root / "prolepsis")
        status = rt2.status(execution_id)
        assert status["digest"] == digest
        verdict = rt2.verify(execution_id)
        assert verdict["verified"] is True
        artifacts = rt2.read_artifacts(execution_id)
        assert artifacts
        rt2.shutdown()

    def test_checkpoint_records_exist(self, tmp_root):
        rt = WordChefRuntime(tmp_root / "prolepsis")
        state = start_match(match_id="m1", mode="SOLO", seed="s", player_ids=["a"], now=0)
        state = start_round(state, now=0)
        player = state.players["a"]
        order = player.order
        word = find_word(order)
        state, outcome = submit_dish(state, "a", word, now=5)
        op, events, rid = ops.op_submit_dish(state=state, player=player, order=order,
                                             outcome=outcome, score_total=player.score,
                                             counter=1)
        record = rt.execute_op(op, events, request_id=rid)
        status = rt.status(record.execution_id)
        assert status["checkpoints"], "important executions must be checkpointed"
        checkpoint = status["checkpoints"][0]
        assert checkpoint["checkpoint_id"] == record.checkpoint_id
        assert checkpoint["digest"] == record.digest
        rt.shutdown()
