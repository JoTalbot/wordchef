"""GameService — the server-authoritative match orchestrator.

Clients send INTENTS. Everything else happens here:

    intent → engine (pure, deterministic) → Prolepsis execution (verified)
           → persistence → event broadcast

No client value is ever trusted: score, combo, heat, timers, rewards and
leaderboard positions are computed server-side and sealed as Prolepsis
executions (execution id + canonical digest + CAS artifacts + checkpoint).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Any, Callable

from app.config import PROLEPSIS_ROOT, SEASON
from app.store import Store

from wordchef_game import engine as eng
from wordchef_game.chaos import pick_chaos, ChaosEvent
from wordchef_game.dictionary import load_dictionary
from wordchef_game.kitchens import KITCHENS, KITCHEN_BY_ID, kitchen_for_xp, next_kitchen
from wordchef_game.orders import check_order

from wordchef_prolepsis import ops
from wordchef_prolepsis.bridge import WordChefRuntime, OpRecord

MATCH_MODES = ("SOLO", "QUICK_COOK", "CHAOS_KITCHEN")
EVENT_BUFFER = 300


class GameError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


class GameService:
    def __init__(self, store: Store, runtime: WordChefRuntime | None = None):
        self.store = store
        self.runtime = runtime or WordChefRuntime(PROLEPSIS_ROOT)
        self._matches: dict[str, eng.MatchState] = {}
        self._locks: dict[str, threading.RLock] = defaultdict(threading.RLock)
        self._events: dict[str, deque] = defaultdict(lambda: deque(maxlen=EVENT_BUFFER))
        self._sequences: dict[str, int] = defaultdict(int)
        self._listeners: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = defaultdict(list)
        self._intent_counter: dict[str, int] = defaultdict(int)

    # ─────────────────────── event bus ───────────────────────

    def publish(self, match_id: str, event: dict) -> dict:
        event = dict(event)
        self._sequences[match_id] += 1
        event["seq"] = self._sequences[match_id]
        event["ts"] = time.time()
        self._events[match_id].append(event)
        for loop, queue in list(self._listeners.get(match_id, ())):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                pass
        return event

    def subscribe(self, match_id: str) -> tuple[asyncio.AbstractEventLoop, asyncio.Queue]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._listeners[match_id].append((loop, queue))
        return loop, queue

    def unsubscribe(self, match_id: str, item) -> None:
        try:
            self._listeners[match_id].remove(item)
        except ValueError:
            pass

    def events_since(self, match_id: str, since: int = 0) -> list[dict]:
        return [e for e in self._events.get(match_id, ()) if e.get("seq", 0) > since]

    # ─────────────────────── players ───────────────────────

    def register_player(self, name: str) -> dict:
        name = (name or "").strip() or "Chef"
        player_id = "p_" + secrets.token_hex(6)
        player = self.store.upsert_player(player_id, name[:32])
        return player

    def progression(self, player_id: str) -> dict:
        player = self.store.get_player(player_id)
        if not player:
            raise GameError("unknown_player", "unknown player", 404)
        kitchen = kitchen_for_xp(player["xp"])
        nxt = next_kitchen(player["xp"])
        return {
            "player_id": player_id, "name": player["name"], "xp": player["xp"],
            "kitchen": kitchen.kitchen_id,
            "kitchen_name": kitchen.name,
            "next_kitchen": None if not nxt else {
                "kitchen_id": nxt.kitchen_id, "name": nxt.name,
                "unlock_xp": nxt.unlock_xp, "remaining": max(0, nxt.unlock_xp - player["xp"]),
            },
        }

    # ─────────────────────── match lifecycle ───────────────────────

    def create_match(self, *, mode: str, player_ids: list[str],
                     kitchen_id: str = "street", rounds: int = 3,
                     seed: str = "", display_names: dict | None = None) -> dict:
        if mode not in MATCH_MODES:
            raise GameError("bad_mode", f"mode must be one of {MATCH_MODES}")
        if not player_ids or len(player_ids) > 8:
            raise GameError("bad_players", "a match needs 1-8 players")
        if kitchen_id not in KITCHEN_BY_ID:
            raise GameError("bad_kitchen", "unknown kitchen")
        if not (1 <= rounds <= 12):
            raise GameError("bad_rounds", "rounds must be 1..12")
        match_id = "m_" + secrets.token_hex(6)
        seed = seed or secrets.token_hex(16)
        state = eng.start_match(
            match_id=match_id, mode=mode, seed=seed, player_ids=player_ids,
            kitchen_id=kitchen_id, rounds_total=rounds,
            display_names=display_names or {},
        )
        self._matches[match_id] = state
        self._persist(state, status="lobby")

        op, events, rid = ops.op_create_match(state)
        record = self.runtime.execute_op(op, events, request_id=rid)
        self._record_exec(match_id, record)
        if record.state != "COMPLETED":
            raise GameError("prolepsis_failed", f"match creation rejected: {record.error}", 500)

        self.publish(match_id, {"type": "MATCH_CREATED", "mode": mode,
                                "players": player_ids, "rounds": rounds})
        return self.match_view(match_id)

    def join_match(self, match_id: str, player_id: str) -> dict:
        """Join a lobby before its first round starts."""
        with self._lock(match_id):
            state = self._match(match_id)
            if state.mode == "SOLO":
                raise GameError("solo_match", "solo matches do not accept additional players", 409)
            if state.round_active or state.round_no > 0:
                raise GameError("match_started", "cannot join a match after it has started", 409)
            if state.finished:
                raise GameError("match_finished", "the match is over", 409)
            if player_id in state.players:
                return self.match_view(match_id)
            if len(state.players) >= 8:
                raise GameError("match_full", "match already has 8 players", 409)
            player = self.store.get_player(player_id)
            if not player:
                raise GameError("unknown_player", "unknown player", 404)
            state.players[player_id] = eng.PlayerState(
                player_id=player_id, display_name=player["name"])
            self._matches[match_id] = state
            self._persist(state, status="lobby")
            self.publish(match_id, {
                "type": "PLAYER_JOINED", "player_id": player_id,
                "display_name": player["name"], "player_count": len(state.players),
            })
            return self.match_view(match_id)

    def start_match(self, match_id: str, player_id: str | None = None) -> dict:
        with self._lock(match_id):
            state = self._match(match_id)
            if state.mode != "SOLO" and player_id and player_id != state.host_player_id:
                raise GameError("not_host", "only the match host can start the lobby", 403)
            if state.mode != "SOLO" and not player_id:
                raise GameError("host_required", "player_id is required to start a multiplayer match", 400)
            if state.round_active:
                raise GameError("already_started", "match already running")
            self._begin_round(state)
        return self.match_view(match_id)

    def _begin_round(self, state: eng.MatchState) -> None:
        # one clock reading per engine call, recorded so replay is exact
        now = time.time()
        state = eng.start_round(state, now=now)
        seq = self._next_seq(state.match_id)
        self.store.record_intent(
            state.match_id, "@system", seq,
            {"action": "ROUND_START"}, {"now": now, "round_no": state.round_no})
        self._matches[state.match_id] = state
        self._persist(state, status="playing")

        op, events, rid = ops.op_start_round(state)
        record = self.runtime.execute_op(op, events, request_id=rid)
        self._record_exec(state.match_id, record)

        for player in state.players.values():
            if player.order is None:
                continue
            op, events, rid = ops.op_generate_order(state, player, player.order)
            async_rec = self.runtime.execute_op_async(op, events, request_id=rid)
            async_rec.add_done_callback(
                lambda fut, mid=state.match_id: self._record_exec_async(mid, fut))

        self.publish(state.match_id, {
            "type": "ROUND_STARTED", "round_no": state.round_no,
            "orders": {pid: self._order_view(p) for pid, p in state.players.items()},
        })

    def _record_exec_async(self, match_id: str, fut) -> None:
        try:
            record = fut.result()
        except Exception as exc:  # pragma: no cover - defensive
            self.publish(match_id, {"type": "EXECUTION_ERROR", "error": str(exc)})
            return
        self._record_exec(match_id, record)

    def _record_exec(self, match_id: str, record: OpRecord) -> None:
        self.store.record_execution({
            "execution_id": record.execution_id, "op": record.op,
            "request_id": record.request_id, "match_id": match_id,
            "state": record.state, "digest": record.digest,
            "verified": record.verified, "checkpoint_id": record.checkpoint_id,
            "artifact_refs": record.artifact_refs,
        })

    # ─────────────────────── intents ───────────────────────

    def _next_seq(self, match_id: str) -> int:
        """Monotonic per-match step counter; survives service restarts."""
        if match_id not in self._intent_counter:
            rows = self.store.intents(match_id)
            self._intent_counter[match_id] = max(
                (r["seq"] for r in rows), default=0)
        self._intent_counter[match_id] += 1
        return self._intent_counter[match_id]

    def submit_intent(self, match_id: str, player_id: str, intent: dict) -> dict:
        with self._lock(match_id):
            state = self._match(match_id)
            if state.finished:
                raise GameError("match_finished", "the match is over", 409)
            if not state.round_active:
                raise GameError("no_active_round", "no active round", 409)
            if player_id not in state.players:
                raise GameError("unknown_player", "player not in match", 404)

            seq = self._next_seq(match_id)
            action = str(intent.get("action", "")).upper()
            player_before = state.players[player_id]
            order_before = player_before.order
            snapshot_before = {
                "combo": player_before.combo, "heat": player_before.heat,
                "golden": player_before.golden, "score": player_before.score,
            }

            # one clock reading for the whole action: engine + log + replay
            now = time.time()
            state, outcome = eng.apply_intent(state, player_id, intent, now=now)
            self._matches[match_id] = state

            # intent log first (replay material), then prolepsis, then state
            self.store.record_intent(
                match_id, player_id, seq, intent, outcome.to_dict(),
                created_at=now)

            record = self._execute_for_action(
                state, player_id, action, outcome, order_before, snapshot_before, seq)
            if record is not None and record.state not in ("COMPLETED",):
                # an unverified claim must not advance the game: roll back
                raise GameError(
                    "verification_failed",
                    f"prolepsis rejected the action: {record.error}", 422)

            self._persist(state, status="finished" if state.finished else "playing")

            player_after = state.players[player_id]
            payload = dict(outcome.payload)
            payload.update({
                "player": self._player_view(player_after),
                "leaderboard": eng.leaderboard(state),
                "round_complete": eng.round_complete(state),
                "execution": None if record is None else {
                    "execution_id": record.execution_id, "digest": record.digest,
                    "verified": record.verified, "checkpoint_id": record.checkpoint_id,
                },
            })
            result = {"accepted": outcome.accepted, "action": outcome.action,
                      "reason": outcome.reason, "player_id": player_id,
                      "payload": payload}
            self.publish(match_id, {"type": "INTENT", "result": result})

            if outcome.accepted and eng.round_complete(state):
                self._close_round(state)
            return result

    def _execute_for_action(self, state, player_id, action, outcome,
                            order_before, snapshot_before, seq) -> OpRecord | None:
        player = state.players[player_id]
        if action == "SUBMIT_DISH" and order_before is not None and outcome.accepted:
            op, events, rid = ops.op_submit_dish(
                state=state, player=player, order=order_before, outcome=outcome,
                score_total=player.score, counter=seq)
            record = self.runtime.execute_op(op, events, request_id=rid)
            self._record_exec(state.match_id, record)
            if record.state == "COMPLETED" and outcome.payload.get("dish", {}).get("valid"):
                # score award is part of the same cloth (DISH_SUBMIT→SCORE_AWARD)
                pass
            return record
        if action == "PREP" and outcome.accepted:
            op, events, rid = ops.op_prep_service(state, player, order_before, outcome)
            record = self.runtime.execute_op(op, events, request_id=rid)
            self._record_exec(state.match_id, record)
            return record
        if action == "GOLDEN" and outcome.accepted:
            costs = {"patience": 1, "restock": 3, "boost": 2}
            effect = outcome.reason  # patience|restock|boost_armed
            effect_name = "boost" if effect == "boost_armed" else effect
            op, events, rid = ops.op_bonus_apply(
                state=state, player=player, effect=effect_name,
                cost=costs.get(effect_name, 0),
                effect_value=int(outcome.payload.get("deadline", 0)) or len(
                    outcome.payload.get("tray", "")) or 1)
            record = self.runtime.execute_op(op, events, request_id=rid)
            self._record_exec(state.match_id, record)
            return record
        if action == "RING_BELL" and outcome.accepted:
            chaos = ChaosEvent(**outcome.payload["chaos"])
            op, events, rid = ops.op_chaos_event(state, chaos)
            record = self.runtime.execute_op(op, events, request_id=rid)
            self._record_exec(state.match_id, record)
            return record
        return None

    # ─────────────────────── round / match closing ───────────────────────

    def _close_round(self, state: eng.MatchState) -> None:
        now = time.time()
        score_delta = sum(p.score for p in state.players.values())
        dishes = sum(p.round_dishes for p in state.players.values())
        combo_peak = max((p.combo for p in state.players.values()), default=0)
        heat_peak = max((p.heat for p in state.players.values()), default=0)

        # close every open ticket on the beam
        for player in state.players.values():
            if player.order is None:
                continue
            op, events, rid = ops.op_close_order(
                state, player, player.order,
                satisfied=1 if player.used_words else 0,
                dishes=len(player.used_words),
                elapsed=int(max(0, now - player.order_started_at)))
            self.runtime.execute_op_async(op, events, request_id=rid)

        state = eng.end_round(state, now=now)
        self._matches[state.match_id] = state
        self._persist(state, status="finished" if state.finished else "playing")
        seq = self._next_seq(state.match_id)
        self.store.record_intent(
            state.match_id, "@system", seq,
            {"action": "ROUND_END"}, {"now": now, "round_no": state.round_no})

        op, events, rid = ops.op_end_round(
            state, score_delta=score_delta, dishes=dishes,
            combo_peak=combo_peak, heat_peak=heat_peak)
        record = self.runtime.execute_op(op, events, request_id=rid)
        self._record_exec(state.match_id, record)

        self.publish(state.match_id, {
            "type": "ROUND_ENDED", "round_no": state.round_no,
            "leaderboard": eng.leaderboard(state),
        })

        if state.finished:
            self._finish_match(state)
        else:
            self._begin_round(state)

    def _finish_match(self, state: eng.MatchState) -> None:
        board = eng.leaderboard(state)
        winner = board[0]["player_id"] if board else "none"
        total_score = sum(p.score for p in state.players.values())
        total_dishes = sum(p.dish_count for p in state.players.values())

        op, events, rid = ops.op_result_commit(
            state, winner_id=winner, total_score=total_score,
            total_dishes=total_dishes, verified=1)
        record = self.runtime.execute_op(op, events, request_id=rid)
        self._record_exec(state.match_id, record)

        for row in board:
            self.store.save_match_player(
                state.match_id, row["player_id"], row["score"],
                row["dishes"], row["best_word"], row["position"])
            self.store.upsert_leaderboard(SEASON, row["player_id"],
                                          row["score"], row["dishes"])
            self.store.add_xp(row["player_id"], row["score"],
                              golden=state.players[row["player_id"]].golden)
            op, events, rid = ops.op_leaderboard_update(
                season=SEASON, player_id=row["player_id"], match_id=state.match_id,
                score=row["score"], dishes=row["dishes"], position=row["position"])
            self.runtime.execute_op_async(op, events, request_id=rid)

        top = self.store.leaderboard(SEASON, limit=100)
        snapshot = json.dumps(top, sort_keys=True)
        digest = hashlib.sha256(snapshot.encode()).hexdigest()
        op, events, rid = ops.op_leaderboard_commit(
            season=SEASON, entries=max(1, len(top)),
            top_score=top[0]["score"] if top else 0, snapshot_digest=digest)
        record = self.runtime.execute_op(op, events, request_id=rid)
        self._record_exec(state.match_id, record)

        self.publish(state.match_id, {
            "type": "MATCH_FINISHED", "winner_id": winner,
            "leaderboard": board, "result_execution": record.execution_id,
        })

    # ─────────────────────── verification / replay ───────────────────────

    def verify_match(self, match_id: str) -> dict:
        """Replay the whole match from (seed + intents) and compare digests."""
        row = self.store.get_match(match_id)
        if not row:
            raise GameError("unknown_match", "unknown match", 404)
        state = self._match(match_id)
        intents = self.store.intents(match_id)

        replay = eng.start_match(
            match_id=match_id, mode=state.mode, seed=state.seed,
            # Replay must preserve the original lobby order because it is part of the
            # deterministic stream context. Sorting player IDs changes RNG inputs
            # for multiplayer matches even though the player set is identical.
            player_ids=list(state.players),
            kitchen_id=state.kitchen_id, rounds_total=state.rounds_total,
            display_names={pid: p.display_name for pid, p in state.players.items()},
        )
        timeline = self._replay_timeline(state, intents)
        digest_match = True
        try:
            for step in timeline:
                if step["kind"] == "round":
                    replay = eng.start_round(replay, now=step["now"])
                elif step["kind"] == "intent":
                    replay, outcome = eng.apply_intent(
                        replay, step["player_id"], step["intent"], now=step["now"])
                    if outcome.to_dict() != step["outcome"]:
                        digest_match = False
                elif step["kind"] == "end":
                    replay = eng.end_round(replay, now=step["now"])
        except Exception:
            digest_match = False

        final_a = self._canonical_state(state)
        final_b = self._canonical_state(replay)
        state_match = final_a == final_b

        verdict = "verified" if (digest_match and state_match) else "diverged"
        op, events, rid = ops.op_match_verify(
            match_id=match_id, replayed=1,
            digest_match=1 if state_match else 0,
            executions=len(self.store.executions(match_id)),
            intent_count=len(intents), verdict=verdict)
        record = self.runtime.execute_op(op, events, request_id=rid)
        self._record_exec(match_id, record)

        return {
            "match_id": match_id, "verdict": verdict,
            "replayed_intents": len(intents),
            "state_match": state_match, "outcome_match": digest_match,
            "verify_execution": {
                "execution_id": record.execution_id, "digest": record.digest,
                "verified": record.verified, "checkpoint_id": record.checkpoint_id,
            },
        }

    def _replay_timeline(self, state: eng.MatchState, intents: list[dict]) -> list[dict]:
        """Rebuild the deterministic timeline. Round boundaries and intents are
        replayed with the exact clock readings recorded at play time; a legacy
        synthetic fallback covers matches recorded before boundary logging."""
        timeline: list[dict] = []
        has_steps = any(i.get("player_id") == "@system" for i in intents)
        if has_steps:
            for item in sorted(intents, key=lambda x: x["seq"]):
                if item.get("player_id") == "@system":
                    action = str(item.get("intent", {}).get("action", "")).upper()
                    if action == "ROUND_START":
                        timeline.append(
                            {"kind": "round", "now": item["outcome"]["now"]})
                    elif action == "ROUND_END":
                        timeline.append(
                            {"kind": "end", "now": item["outcome"]["now"]})
                    continue
                timeline.append({
                    "kind": "intent", "player_id": item["player_id"],
                    "intent": item["intent"], "outcome": item["outcome"],
                    "now": item["created_at"],
                })
            return timeline

        rounds = state.round_no
        intents_by_round: dict[int, list[dict]] = defaultdict(list)
        # derive round membership from the outcome payloads (order ids carry -rN-)
        for item in intents:
            round_no = self._round_of_intent(item)
            intents_by_round[round_no].append(item)
        base = state.created_at
        for round_no in range(1, rounds + 1):
            timeline.append({"kind": "round", "now": base + round_no * 1000.0})
            for item in sorted(intents_by_round.get(round_no, ()), key=lambda x: x["seq"]):
                timeline.append({
                    "kind": "intent", "player_id": item["player_id"],
                    "intent": item["intent"], "outcome": item["outcome"],
                    "now": item["created_at"],
                })
            timeline.append({"kind": "end", "now": base + round_no * 1000.0 + 500.0})
        return timeline

    def _round_of_intent(self, item: dict) -> int:
        outcome = item.get("outcome", {})
        payload = outcome.get("payload", {}) or {}
        order_id = str(payload.get("order_id", "") or
                       (payload.get("next_order", {}) or {}).get("order_id", ""))
        for part in order_id.split("-"):
            if part.startswith("r") and part[1:].isdigit():
                return int(part[1:])
        return 1

    @staticmethod
    def _canonical_state(state: eng.MatchState) -> str:
        data = state.to_dict()
        # created_at timestamps are wall-clock; the game's truth excludes them
        data.pop("created_at", None)
        for player in data["players"].values():
            player.pop("order_started_at", None)
            player.pop("order_deadline", None)
            if player.get("order"):
                player["order"].pop("order_id", None)
        return json.dumps(data, sort_keys=True)

    # ─────────────────────── campaign levels ───────────────────────

    def get_level(self, level_no: int) -> dict:
        from wordchef_game.levels import generate_level
        return generate_level(level_no).to_dict()

    def complete_level(self, player_id: str | None, level_no: int,
                       words: list[str], bonus: list[str]) -> dict:
        """Authoritative level completion: the server re-validates every word
        against the deterministic level and seals the reward through Prolepsis."""
        from wordchef_game.levels import generate_level
        pid = player_id or "anon"
        lv = generate_level(level_no)
        board_words = {b.word for b in lv.board}
        valid_board = [w for w in words if w in board_words]
        valid_bonus = [w for w in bonus if w in set(lv.bonus)]
        if set(valid_board) != board_words:
            raise GameError(
                "level_incomplete", "not all level words were served", 422)

        done = self.store.level_done(pid, level_no)
        coins = (sum(2 * len(w) for w in valid_board)
                 + sum(len(w) for w in valid_bonus) + 30 + level_no)
        if done is not None:
            return {"level_no": level_no, "coins": done["coins"],
                    "board": valid_board, "bonus": valid_bonus,
                    "already_done": True, "execution": None}

        state = eng.start_match(
            match_id=f"level:{level_no}:{pid}", mode="SOLO",
            seed=f"level-{level_no}", player_ids=[pid],
            kitchen_id=lv.kitchen, rounds_total=1)
        op, events, rid = ops.op_result_commit(
            state, winner_id=pid, total_score=coins,
            total_dishes=len(valid_board), verified=1)
        record = self.runtime.execute_op(op, events, request_id=rid)
        self._record_exec(state.match_id, record)
        self.store.mark_level(pid, level_no, coins)
        if pid != "anon":
            self.store.add_xp(pid, coins, golden=0)

        return {"level_no": level_no, "coins": coins,
                "board": valid_board, "bonus": valid_bonus,
                "already_done": False,
                "execution": {
                    "execution_id": record.execution_id, "digest": record.digest,
                    "verified": record.verified,
                    "checkpoint_id": record.checkpoint_id}}

    # ─────────────────────── views ───────────────────────

    def match_view(self, match_id: str) -> dict:
        state = self._match(match_id)
        return {
            "match_id": state.match_id, "mode": state.mode,
            "host_player_id": state.host_player_id,
            "kitchen": state.kitchen_id, "round_no": state.round_no,
            "rounds_total": state.rounds_total,
            "round_active": state.round_active, "finished": state.finished,
            "leaderboard": eng.leaderboard(state),
            "chaos_log": state.chaos_log[-10:],
        }

    def player_view(self, match_id: str, player_id: str) -> dict:
        state = self._match(match_id)
        player = state.players.get(player_id)
        if not player:
            raise GameError("unknown_player", "player not in match", 404)
        view = self._player_view(player)
        view["leaderboard"] = eng.leaderboard(state)
        view["round_no"] = state.round_no
        view["rounds_total"] = state.rounds_total
        view["finished"] = state.finished
        view["mode"] = state.mode
        return view

    def _player_view(self, player: eng.PlayerState) -> dict:
        return {
            "player_id": player.player_id, "name": player.display_name,
            "score": player.score, "combo": player.combo, "heat": player.heat,
            "spice_charges": player.spice_charges,
            "golden": player.golden, "prep_tokens": player.prep_tokens,
            "boost_armed": player.boost_armed,
            "round_dishes": player.round_dishes,
            "dishes": player.dish_count,
            "order": self._order_view(player),
            "order_deadline": player.order_deadline,
            "order_started_at": player.order_started_at,
        }

    @staticmethod
    def _order_view(player: eng.PlayerState) -> dict | None:
        if player.order is None:
            return None
        order = player.order
        view = order.to_dict()
        # the secret objective is revealed only when it lands
        view["secret"] = "" if order.secret else ""
        view["has_secret"] = bool(order.secret)
        view["streak_left"] = player.streak_left
        return view

    # ─────────────────────── helpers ───────────────────────

    def _match(self, match_id: str) -> eng.MatchState:
        state = self._matches.get(match_id)
        if state is not None:
            return state
        row = self.store.get_match(match_id)
        if not row:
            raise GameError("unknown_match", "unknown match", 404)
        state = eng.MatchState.from_dict(row["state"])
        self._matches[match_id] = state
        return state

    def _persist(self, state: eng.MatchState, status: str) -> None:
        self.store.save_match(
            state.match_id, state.mode, state.seed, state.kitchen_id,
            state.rounds_total, status, state.to_dict(),
            created_at=state.created_at,
            finished_at=time.time() if state.finished else None)

    def _lock(self, match_id: str) -> threading.RLock:
        return self._locks[match_id]

    def leaderboard(self, limit: int = 50) -> list[dict]:
        return self.store.leaderboard(SEASON, limit=limit)

    def audit_executions(self, match_id: str | None = None) -> list[dict]:
        return self.store.executions(match_id)

    def audit_execution_detail(self, execution_id: str) -> dict:
        status = self.runtime.status(execution_id)
        artifacts = self.runtime.read_artifacts(execution_id)
        return {"status": status, "artifacts": artifacts}

    def prolepsis_health(self) -> dict:
        return self.runtime.health()

    def prolepsis_ready(self) -> dict:
        return self.runtime.ready()

    def prolepsis_version(self) -> dict:
        return self.runtime.version()
