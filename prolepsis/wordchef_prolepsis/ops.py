"""Word Chef game operations → Prolepsis execution payloads.

Each function turns authoritative game data into event facts for the
matching Jacquard pattern. Facts are CLAIMS; the weaver recomputes them.
All request ids are deterministic and idempotent (retries never double-apply
because the Agent Gateway dedupes by request_id).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from wordchef_game.chaos import ChaosEvent
from wordchef_game.engine import MatchState, Outcome, PlayerState
from wordchef_game.orders import Order


def seed_fingerprint(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


# 1 · создание матча
def op_create_match(state: MatchState) -> tuple[str, list[dict], str]:
    request_id = f"match.create:{state.match_id}"
    event = {
        "type": "MATCH_CREATE",
        "payload": {
            "match_id": state.match_id,
            "mode": state.mode,
            "players": len(state.players),
            "rounds_total": state.rounds_total,
            "kitchen": state.kitchen_id,
            "seed_fingerprint": seed_fingerprint(state.seed),
        },
    }
    return "match.create", [event], request_id


# 2 · начало раунда
def op_start_round(state: MatchState) -> tuple[str, list[dict], str]:
    request_id = f"round.start:{state.match_id}:r{state.round_no}"
    event = {
        "type": "ROUND_START",
        "payload": {
            "match_id": state.match_id,
            "round_no": state.round_no,
            "mode": state.mode,
            "kitchen": state.kitchen_id,
            "players": len(state.players),
            "seed_fingerprint": seed_fingerprint(state.seed),
        },
    }
    return "round.start", [event], request_id


# 3 · генерация задания
def op_generate_order(state: MatchState, player: PlayerState,
                      order: Order) -> tuple[str, list[dict], str]:
    request_id = (f"order.generate:{order.order_id}")
    event = {
        "type": "ORDER_GENERATE",
        "payload": {
            "match_id": state.match_id,
            "player_id": player.player_id,
            "order_id": order.order_id,
            "kind": order.kind,
            "difficulty": order.difficulty,
            "tray": order.tray,
            "time_limit": order.time_limit,
            "base_reward": order.base_reward,
            "flavor_request": order.flavor_request or "any",
            "theme": order.theme or "none",
            "secret": order.secret or "none",
        },
    }
    complete = {
        "type": "ORDER_COMPLETE",
        "payload": {
            "order_id": order.order_id,
            "satisfied": 1 if order.used_words else 0,
            "dishes": len(order.used_words),
            "elapsed": 0,
            "bonus": 0,
        },
    }
    return "order.generate", [event], request_id


def op_close_order(state: MatchState, player: PlayerState, order: Order, *,
                   satisfied: int, dishes: int, elapsed: int,
                   bonus: int = 0) -> tuple[str, list[dict], str]:
    """Close a ticket: the completion pick rides with its generation pick."""
    request_id = f"order.generate:{order.order_id}:close"
    _, generate_events, _ = op_generate_order(state, player, order)
    return "order.generate", [generate_events[0], {
        "type": "ORDER_COMPLETE",
        "payload": {
            "order_id": order.order_id,
            "satisfied": 1 if satisfied else 0,
            "dishes": dishes,
            "elapsed": elapsed,
            "bonus": bonus,
        },
    }], request_id


# 4+5 · обработка ответа игрока и начисление очков — one atomic cloth
def op_submit_dish(*, state: MatchState, player: PlayerState, order: Order,
                   outcome: Outcome, score_total: int,
                   counter: int = 0) -> tuple[str, list[dict], str]:
    dish = outcome.payload["dish"]
    payload = {
        "match_id": state.match_id,
        "player_id": player.player_id,
        "order_id": order.order_id,
        "word": dish["word"] or "none",
        "word_len": dish["word_len"],
        "tray": order.tray,
        "elapsed": outcome.payload.get("elapsed_seconds", 0),
        "valid": 1 if dish["valid"] else 0,
        "reason": dish["reason"],
        "score_delta": outcome.payload.get("score_delta", 0),
        "combo_before": dish["combo_before"],
        "combo_after": dish["combo_after"],
        "heat_before": dish["heat_before"],
        "heat_after": dish["heat_after"],
        "spice": 1 if dish.get("spice_mult", 1) > 1 else 0,
        "golden_delta": dish["golden_delta"],
        "flavor": dish.get("flavor") or "none",
        "secret_hit": 1 if dish.get("secret_hit") else 0,
        "kitchen": state.kitchen_id,
        "order_snapshot": _json(order.to_dict()),
        "claimed_result": _json(dish),
        "prep_tokens": dish.get("prep_tokens_used", 0),
        "boost_applied": 1 if outcome.payload.get("boost_applied") else 0,
        "double_applied": 1 if outcome.payload.get("double_order_applied") else 0,
    }

    events = [{"type": "DISH_SUBMIT", "payload": payload}]
    if dish["valid"] or outcome.payload.get("score_delta", 0) > 0:
        events.append({
            "type": "SCORE_AWARD",
            "payload": {
                "match_id": state.match_id,
                "player_id": player.player_id,
                "order_id": order.order_id,
                "score_delta": outcome.payload.get("score_delta", 0),
                "score_total": score_total,
                "combo_after": dish["combo_after"],
                "heat_after": dish["heat_after"],
            },
        })
    request_id = f"dish.submit:{state.match_id}:{player.player_id}:{counter}"
    return "dish.submit", events, request_id


def _prep_tokens_before(player: PlayerState, outcome: Outcome) -> int:
    # kept for backwards compatibility of tests; prep tokens are claimed from
    # the scorer's own DishResult.prep_tokens_used field now.
    return int(outcome.payload["dish"].get("prep_tokens_used", 0))


# 4b · mise en place (prep words)
def op_prep_service(state: MatchState, player: PlayerState, order: Order,
                    outcome: Outcome) -> tuple[str, list[dict], str]:
    request_id = f"prep.service:{order.order_id}:{outcome.payload.get('word', 'w')}:{len(player.used_words)}"
    event = {
        "type": "PREP_SERVICE",
        "payload": {
            "match_id": state.match_id,
            "player_id": player.player_id,
            "order_id": order.order_id,
            "word": outcome.payload.get("word", "none"),
            "word_len": len(outcome.payload.get("word", "")),
            "points": outcome.payload.get("points", 0),
            "prep_tokens": outcome.payload.get("prep_tokens", 0),
        },
    }
    return "prep.service", [event], request_id


# 6 · применение бонусов
def op_bonus_apply(*, state: MatchState, player: PlayerState, effect: str,
                   cost: int, effect_value: int) -> tuple[str, list[dict], str]:
    request_id = f"bonus.apply:{state.match_id}:{player.player_id}:{effect}:{player.golden}"
    event = {
        "type": "BONUS_APPLY",
        "payload": {
            "match_id": state.match_id,
            "player_id": player.player_id,
            "effect": effect,
            "cost": cost,
            "effect_value": effect_value,
            "golden_after": player.golden,
        },
    }
    return "bonus.apply", [event], request_id


# 6b · chaos events (Chaos Kitchen)
def op_chaos_event(state: MatchState, event: ChaosEvent) -> tuple[str, list[dict], str]:
    request_id = f"chaos.event:{state.match_id}:{len(state.chaos_log)}"
    return "chaos.event", [{
        "type": "CHAOS_EVENT",
        "payload": {
            "match_id": state.match_id,
            "event_type": event.event_type,
            "actor_id": event.actor_id,
            "victim_id": event.victim_id,
            "param": event.param or "none",
        },
    }], request_id


# 7 · завершение раунда
def op_end_round(state: MatchState, *, score_delta: int, dishes: int,
                 combo_peak: int, heat_peak: int) -> tuple[str, list[dict], str]:
    request_id = f"round.end:{state.match_id}:r{state.round_no}"
    event = {
        "type": "ROUND_END",
        "payload": {
            "match_id": state.match_id,
            "round_no": state.round_no,
            "score_delta": score_delta,
            "dishes": dishes,
            "combo_peak": combo_peak,
            "heat_peak": heat_peak,
            "chaos_events": len(state.chaos_log),
        },
    }
    return "round.end", [event], request_id


# 8 · фиксация результата
def op_result_commit(state: MatchState, *, winner_id: str, total_score: int,
                     total_dishes: int, verified: int) -> tuple[str, list[dict], str]:
    request_id = f"result.commit:{state.match_id}"
    create, _, _ = op_create_match(state)
    events = [
        {
            "type": "MATCH_CREATE",
            "payload": {
                "match_id": state.match_id,
                "mode": state.mode,
                "players": len(state.players),
                "rounds_total": state.rounds_total,
                "kitchen": state.kitchen_id,
                "seed_fingerprint": seed_fingerprint(state.seed),
            },
        },
        {
            "type": "MATCH_FINISH",
            "payload": {
                "match_id": state.match_id,
                "winner_id": winner_id or "none",
                "total_score": total_score,
                "total_dishes": total_dishes,
                "verified": 1 if verified else 0,
            },
        },
    ]
    return "result.commit", events, request_id


# 9 · leaderboard
def op_leaderboard_update(*, season: str, player_id: str, match_id: str,
                          score: int, dishes: int,
                          position: int) -> tuple[str, list[dict], str]:
    request_id = f"leaderboard.update:{season}:{player_id}:{match_id}"
    event = {
        "type": "LEADERBOARD_UPDATE",
        "payload": {
            "season": season,
            "player_id": player_id,
            "match_id": match_id,
            "score": score,
            "dishes": dishes,
            "position": position,
        },
    }
    return "leaderboard.update", [event], request_id


def op_leaderboard_commit(*, season: str, entries: int, top_score: int,
                          snapshot_digest: str) -> tuple[str, list[dict], str]:
    request_id = f"leaderboard.commit:{season}:{snapshot_digest[:12]}"
    events = [
        {
            "type": "LEADERBOARD_UPDATE",
            "payload": {
                "season": season,
                "player_id": "snapshot",
                "match_id": "snapshot",
                "score": top_score,
                "dishes": entries,
                "position": 1,
            },
        },
        {
            "type": "LEADERBOARD_COMMIT",
            "payload": {
                "season": season,
                "entries": entries,
                "top_score": top_score,
                "snapshot_digest": snapshot_digest,
            },
        },
    ]
    return "leaderboard.commit", events, request_id


# 10 · replay / verification
def op_match_verify(*, match_id: str, replayed: int, digest_match: int,
                    executions: int, intent_count: int,
                    verdict: str) -> tuple[str, list[dict], str]:
    request_id = f"match.verify:{match_id}:{digest_match}"
    event = {
        "type": "MATCH_VERIFIED",
        "payload": {
            "match_id": match_id,
            "replayed": replayed,
            "digest_match": digest_match,
            "executions": max(1, executions),
            "intent_count": intent_count,
            "verdict": verdict,
        },
    }
    return "match.verify", [event], request_id
