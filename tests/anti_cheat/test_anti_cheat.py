"""Anti-cheat tests — the client is never trusted; the loom refuses lies.

Covers:
* tampered score claims → Prolepsis execution FAILS closed
* forged intents (score/combo/timer/inventory) → rejected by the engine
* expired timers are decided by the server clock only
* leaderboard rows cannot be injected
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from wordchef_prolepsis import ops
from wordchef_game.engine import (start_match, start_round, submit_dish,
                                  use_golden, apply_intent)
from wordchef_game.dictionary import load_dictionary
from wordchef_game.orders import check_order

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers import D, find_word, serve_valid as _serve


class TestLoomRefusesLies:
    def test_inflated_score_claim_fails_execution(self, runtime):
        state = start_match(match_id="ac1", mode="SOLO", seed="s", player_ids=["a"], now=0)
        state = start_round(state, now=0)
        state, outcome, order, word = _serve(state)

        dish = dict(outcome.payload["dish"])
        dish["score_delta"] = 999_999
        forged = replace(outcome, payload=dict(outcome.payload, dish=dish,
                                               score_delta=999_999))
        op, events, rid = ops.op_submit_dish(
            state=state, player=state.players["a"], order=order, outcome=forged,
            score_total=999_999, counter=1)
        record = runtime.execute_op(op, events, request_id=rid)
        assert record.state == "FAILED"
        assert record.error["code"] == "execution_failed"
        failure = record.error["failures"][0]
        assert failure["reason"] == "constraint_broken"
        assert "recompute mismatch" in failure["message"]

    def test_inflated_combo_claim_fails_execution(self, runtime):
        state = start_match(match_id="ac2", mode="SOLO", seed="s", player_ids=["a"], now=0)
        state = start_round(state, now=0)
        state, outcome, order, word = _serve(state)

        dish = dict(outcome.payload["dish"])
        dish["combo_after"] = 42
        forged = replace(outcome, payload=dict(outcome.payload, dish=dish))
        op, events, rid = ops.op_submit_dish(
            state=state, player=state.players["a"], order=order, outcome=forged,
            score_total=state.players["a"].score, counter=1)
        record = runtime.execute_op(op, events, request_id=rid)
        assert record.state == "FAILED"

    def test_bonus_multiplier_forgery_fails_execution(self, runtime):
        state = start_match(match_id="ac3", mode="SOLO", seed="s", player_ids=["a"], now=0)
        state = start_round(state, now=0)
        state, outcome, order, word = _serve(state)

        # claim the boost multiplier without the engine ever granting it
        forged_payload = dict(outcome.payload)
        forged_payload["boost_applied"] = 1
        forged_payload["score_delta"] = int(outcome.payload["score_delta"] * 1.5)
        dish = dict(forged_payload["dish"])
        dish["score_delta"] = forged_payload["score_delta"]
        forged_payload["dish"] = dish
        forged = replace(outcome, payload=forged_payload)
        op, events, rid = ops.op_submit_dish(
            state=state, player=state.players["a"], order=order, outcome=forged,
            score_total=forged_payload["score_delta"], counter=1)
        record = runtime.execute_op(op, events, request_id=rid)
        assert record.state == "FAILED"

    def test_negative_score_breaks_selvedge(self, runtime):
        state = start_match(match_id="ac4", mode="SOLO", seed="s", player_ids=["a"], now=0)
        op, events, rid = ops.op_leaderboard_update(
            season="s", player_id="a", match_id="ac4",
            score=-500, dishes=1, position=1)
        record = runtime.execute_op(op, events, request_id=rid)
        assert record.state == "FAILED"

    def test_bogus_position_breaks_selvedge(self, runtime):
        op, events, rid = ops.op_leaderboard_update(
            season="s", player_id="a", match_id="ac4",
            score=100, dishes=1, position=0)
        record = runtime.execute_op(op, events, request_id=rid)
        assert record.state == "FAILED"


class TestClientCannotInjectState:
    def test_unknown_action_rejected(self):
        state = start_match(match_id="ac5", mode="SOLO", seed="s", player_ids=["a"], now=0)
        state = start_round(state, now=0)
        state, outcome = apply_intent(state, "a", {"action": "SET_SCORE", "score": 99999}, now=1)
        assert not outcome.accepted
        assert state.players["a"].score == 0

    def test_extra_intent_fields_ignored(self):
        state = start_match(match_id="ac6", mode="SOLO", seed="s", player_ids=["a"], now=0)
        state = start_round(state, now=0)
        player = state.players["a"]
        word = next(w for w in D.longest_words_in(player.order.tray, 10)
                    if check_order(player.order, w, D)[0])
        state, outcome = apply_intent(state, "a", {
            "action": "SUBMIT_DISH", "word": word,
            "score": 99999, "combo": 99, "heat": 99, "golden": 999,
        }, now=1)
        assert state.players["a"].score < 99999
        assert state.players["a"].combo <= 1
        assert state.players["a"].golden < 999

    def test_server_clock_decides_timers(self):
        state = start_match(match_id="ac7", mode="SOLO", seed="s", player_ids=["a"], now=1000.0)
        state = start_round(state, now=1000.0)
        player = state.players["a"]
        word = next(w for w in D.longest_words_in(player.order.tray, 10)
                    if check_order(player.order, w, D)[0])
        # the client cannot backdate its submission time
        state, outcome = submit_dish(state, "a", word, now=player.order_deadline + 1)
        assert outcome.reason == "order_expired"
        assert state.players["a"].score == 0

    def test_golden_cannot_be_minted(self):
        state = start_match(match_id="ac8", mode="SOLO", seed="s", player_ids=["a"], now=0)
        state = start_round(state, now=0)
        state, outcome = use_golden(state, "a", "patience", now=1)
        assert not outcome.accepted
        assert state.players["a"].golden == 0

    def test_prep_cannot_be_farmed_unboundedly(self):
        state = start_match(match_id="ac9", mode="SOLO", seed="s", player_ids=["a"], now=0)
        state = start_round(state, now=0)
        for _ in range(6):
            player = state.players["a"]
            word = next((w for w in D.longest_words_in(player.order.tray, 6)
                         if 2 <= len(w) <= 3 and w not in player.used_words), None)
            if not word:
                break
            state, _ = apply_intent(state, "a", {"action": "PREP", "word": word}, now=1)
        assert state.players["a"].prep_tokens <= 3

    def test_players_not_in_match_cannot_act(self):
        state = start_match(match_id="ac10", mode="SOLO", seed="s", player_ids=["a"], now=0)
        state = start_round(state, now=0)
        state, outcome = apply_intent(state, "intruder", {"action": "SUBMIT_DISH", "word": "art"}, now=1)
        assert not outcome.accepted
