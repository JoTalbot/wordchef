"""Game-rule tests — combo, heat, spice, golden, prep, streaks, secrets,
chaos, kitchen rules, progression. All through the authoritative engine."""
from __future__ import annotations

from wordchef_game.engine import (MatchState, PlayerState, start_match, start_round,
                                  end_round, submit_dish, submit_prep, use_golden,
                                  ring_bell, skip_order, apply_intent,
                                  round_complete, leaderboard, ROUND_DISHES)
from wordchef_game.dictionary import load_dictionary
from wordchef_game.orders import check_order
from wordchef_game.scoring import score_dish, combo_multiplier, heat_multiplier, prep_score

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers import D, find_word


def serve_word(state, player_id="a", now=100.0):
    """Serve a valid dish for the player's current order."""
    player = state.players[player_id]
    order = player.order
    word = find_word(order)
    assert word is not None, f"no valid word for order {order.kind} {order.theme}"
    return submit_dish(state, player_id, word, now=now) + (word,)


def solo_match(seed="rules", mode="SOLO", players=("a",), now=0.0):
    state = start_match(match_id="m", mode=mode, seed=seed,
                        player_ids=list(players), now=now)
    return start_round(state, now=now)


class TestScoring:
    def test_combo_multiplier_growth_and_cap(self):
        assert combo_multiplier(0) == 1.0
        assert combo_multiplier(2) == 1.5
        assert combo_multiplier(99) == 4.0

    def test_heat_multiplier(self):
        assert heat_multiplier(0) == 1.0
        assert heat_multiplier(5) == 1.4

    def test_score_grows_with_length(self):
        state = solo_match()
        order = state.players["a"].order
        small = score_dish(word="at", order=order, combo_before=0, heat_before=0,
                           spice_active=False, prep_tokens=0, kitchen_mult=1.0,
                           elapsed_seconds=1)
        big = score_dish(word="artist", order=order, combo_before=0, heat_before=0,
                         spice_active=False, prep_tokens=0, kitchen_mult=1.0,
                         elapsed_seconds=1)
        if small.valid and big.valid:
            assert big.score_delta > small.score_delta

    def test_spice_doubles_but_risks(self):
        state = solo_match()
        order = state.players["a"].order
        word = find_word(order)
        plain = score_dish(word=word, order=order, combo_before=1, heat_before=1,
                           spice_active=False, prep_tokens=0, kitchen_mult=1.0,
                           elapsed_seconds=1)
        spicy = score_dish(word=word, order=order, combo_before=1, heat_before=1,
                           spice_active=True, prep_tokens=0, kitchen_mult=1.0,
                           elapsed_seconds=1)
        assert spicy.valid and plain.valid
        assert spicy.score_delta >= plain.score_delta * 2
        # failed spicy dish burns combo and drops heat harder
        failed = score_dish(word="zzzz", order=order, combo_before=4, heat_before=6,
                            spice_active=True, prep_tokens=0, kitchen_mult=1.0,
                            elapsed_seconds=1)
        assert not failed.valid and failed.burned
        assert failed.combo_after == 0
        assert failed.heat_after == 3

    def test_prep_tokens_boost_next_dish(self):
        state = solo_match()
        order = state.players["a"].order
        word = find_word(order)
        zero = score_dish(word=word, order=order, combo_before=0, heat_before=0,
                          spice_active=False, prep_tokens=0, kitchen_mult=1.0,
                          elapsed_seconds=1)
        three = score_dish(word=word, order=order, combo_before=0, heat_before=0,
                           spice_active=False, prep_tokens=3, kitchen_mult=1.0,
                           elapsed_seconds=1)
        if zero.valid and three.valid:
            assert three.score_delta > zero.score_delta

    def test_speed_bonus_windows(self):
        state = solo_match()
        order = state.players["a"].order
        word = find_word(order)
        fast = score_dish(word=word, order=order, combo_before=0, heat_before=0,
                          spice_active=False, prep_tokens=0, kitchen_mult=1.0,
                          elapsed_seconds=1)
        slow = score_dish(word=word, order=order, combo_before=0, heat_before=0,
                          spice_active=False, prep_tokens=0, kitchen_mult=1.0,
                          elapsed_seconds=order.time_limit)
        if fast.valid and slow.valid:
            assert fast.speed_mult > slow.speed_mult


class TestEngineFlow:
    def test_success_updates_combo_heat_score(self):
        state = solo_match()
        state, outcome, word = serve_word(state, now=5.0)
        assert outcome.accepted and outcome.reason == "dish_served"
        player = state.players["a"]
        assert player.combo == 1 and player.heat == 1 and player.score > 0
        assert word in player.used_words

    def test_failure_resets_combo(self):
        state = solo_match()
        state, _, _ = serve_word(state, now=5.0)
        state, outcome, _ = serve_word(state, now=6.0)
        assert state.players["a"].combo == 2
        state, outcome = submit_dish(state, "a", "zzzz", now=7.0)
        assert outcome.accepted and outcome.reason == "not_in_dictionary"
        assert state.players["a"].combo == 0

    def test_duplicate_word_rejected(self):
        state = solo_match()
        state, _, word = serve_word(state, now=5.0)
        state, outcome = submit_dish(state, "a", word, now=6.0)
        assert outcome.reason in ("word_already_served", "not_formable_from_tray",
                                  "not_in_dictionary")

    def test_prep_scores_without_breaking_combo(self):
        state = solo_match()
        state, _, _ = serve_word(state, now=5.0)
        player = state.players["a"]
        word = next((w for w in D.longest_words_in(player.order.tray, 6)
                     if 2 <= len(w) <= 3 and w not in player.used_words), None)
        if word is None:
            return
        combo_before = player.combo
        state, outcome = submit_prep(state, "a", word, now=6.0)
        assert outcome.accepted
        assert state.players["a"].combo == combo_before
        assert state.players["a"].prep_tokens == 1
        assert state.players["a"].score >= prep_score(word)

    def test_spice_charge_consumed(self):
        state = solo_match()
        player = state.players["a"]
        player.spice_charges = 1
        state, outcome, _ = serve_word(state, now=5.0)
        state, outcome = submit_dish(state, "a", "qqqq", use_spice=True, now=6.0)
        assert state.players["a"].spice_charges == 0

    def test_golden_buys_time_and_restock(self):
        state = solo_match()
        state.players["a"].golden = 10
        deadline_before = state.players["a"].order_deadline
        state, outcome = use_golden(state, "a", "patience", now=5.0)
        assert outcome.accepted
        assert state.players["a"].order_deadline > deadline_before
        tray_before = state.players["a"].order.tray
        state, outcome = use_golden(state, "a", "restock", now=6.0)
        assert outcome.accepted and outcome.payload["tray"] != tray_before
        state, outcome = use_golden(state, "a", "boost", now=7.0)
        assert outcome.accepted and state.players["a"].boost_armed

    def test_golden_requires_funds(self):
        state = solo_match()
        state, outcome = use_golden(state, "a", "patience", now=5.0)
        assert not outcome.accepted and outcome.reason == "not_enough_golden"

    def test_round_completes_after_quota(self):
        state = solo_match()
        for i in range(ROUND_DISHES):
            assert not round_complete(state)
            state, _, _ = serve_word(state, now=10.0 + i)
        assert round_complete(state)

    def test_skip_replaces_order_and_resets_combo(self):
        state = solo_match()
        state, _, _ = serve_word(state, now=5.0)
        old_order = state.players["a"].order.order_id
        state, outcome = skip_order(state, "a", now=6.0)
        assert outcome.accepted
        assert state.players["a"].combo == 0
        assert state.players["a"].order.order_id != old_order

    def test_expired_order_is_replaced(self):
        state = solo_match()
        player = state.players["a"]
        word = find_word(player.order)
        state, outcome = submit_dish(state, "a", word, now=player.order_deadline + 5)
        assert outcome.reason == "order_expired"
        assert state.players["a"].order is not None
        assert outcome.payload.get("next_order")

    def test_unknown_intent_rejected(self):
        state = solo_match()
        state, outcome = apply_intent(state, "a", {"action": "HACK_SCORE"}, now=1.0)
        assert not outcome.accepted and outcome.reason == "unknown_action"
        state, outcome = apply_intent(state, "ghost", {"action": "SUBMIT_DISH"}, now=1.0)
        assert not outcome.accepted

    def test_leaderboard_order(self):
        state = solo_match(players=("a", "b"), mode="QUICK_COOK")
        state, _, _ = serve_word(state, "a", now=5.0)
        board = leaderboard(state)
        assert board[0]["player_id"] == "a"
        assert board[0]["position"] == 1 and board[1]["position"] == 2


class TestMultiplayerRules:
    def test_quick_cook_shared_order(self):
        state = solo_match(players=("a", "b", "c"), mode="QUICK_COOK")
        orders = [p.order for p in state.players.values()]
        assert orders[0].tray == orders[1].tray == orders[2].tray
        assert orders[0].kind == orders[1].kind

    def test_chaos_kitchen_independent_orders_and_events(self):
        state = start_match(match_id="m", mode="CHAOS_KITCHEN", seed="chaos",
                            player_ids=["a", "b"], now=0.0)
        state = start_round(state, now=0.0)
        state = end_round(state, now=50.0)
        state = start_round(state, now=60.0)   # chaos fires on round > 1
        assert state.chaos_log, "chaos kitchen must produce chaos events"
        assert state.players["a"].order.tray != state.players["b"].order.tray \
            or state.players["a"].order.kind != state.players["b"].order.kind

    def test_bell_triggers_chaos_with_golden(self):
        state = start_match(match_id="m", mode="CHAOS_KITCHEN", seed="bell",
                            player_ids=["a", "b"], now=0.0)
        state = start_round(state, now=0.0)
        state, outcome = ring_bell(state, "a", now=1.0)
        assert not outcome.accepted
        state.players["a"].golden = 2
        state, outcome = ring_bell(state, "a", now=2.0)
        assert outcome.accepted
        assert state.chaos_log
        assert state.players["a"].golden == 1

    def test_deterministic_replay_of_full_match(self):
        def run():
            state = start_match(match_id="m", mode="CHAOS_KITCHEN", seed="det",
                                player_ids=["a", "b"], now=0.0)
            log = []
            for rnd in range(2):
                state = start_round(state, now=100.0 * rnd)
                for pid in ("a", "b"):
                    for _ in range(2):
                        p = state.players[pid]
                        if p.order is None:
                            continue
                        word = find_word(p.order)
                        if word is None:
                            continue
                        state, out = submit_dish(state, pid, word, now=110.0 * rnd)
                        log.append((pid, word, out.payload.get("score_delta")))
                state = end_round(state, now=150.0 * rnd)
            return state, log

        state_a, log_a = run()
        state_b, log_b = run()
        assert log_a == log_b
        assert leaderboard(state_a) == leaderboard(state_b)
