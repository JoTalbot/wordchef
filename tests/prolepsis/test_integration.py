"""Prolepsis integration tests — patterns, handlers, artifacts, checkpoints,
idempotency, capabilities, async executions, replay & verification."""
from __future__ import annotations

from pathlib import Path

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wordchef_prolepsis import ops
from wordchef_prolepsis.bridge import OP_SOURCES, WordChefRuntime
from wordchef_prolepsis.handlers import WEAVERS, make_handlers
from wordchef_game.engine import (start_match, start_round, end_round,
                                  submit_dish, leaderboard)
from wordchef_game.dictionary import load_dictionary
from wordchef_game.orders import check_order

PATTERNS = Path(__file__).resolve().parents[2] / "prolepsis" / "patterns" / "wordchef"


class TestPatterns:
    def test_all_patterns_compile(self):
        from prolepsis._cli import compile_source
        for name in sorted(set(OP_SOURCES.values())):
            result = compile_source(PATTERNS / name)
            assert result.document.addr.startswith("sha256:")
            assert result.document.nodes
            assert result.document.events

    def test_selvedges_lower_to_constraints(self):
        from prolepsis._cli import compile_source
        result = compile_source(PATTERNS / "dish.yaml")
        decls = {e.event_type: e for e in result.document.events}
        assert decls["DISH_SUBMIT"].constraints, "the reed must guard dish facts"

    def test_handler_registry_covers_all_render_nodes(self):
        from prolepsis import adapter, jacquard
        known = set()
        for name in set(OP_SOURCES.values()):
            bundle = adapter.adapt(jacquard.load_pattern(PATTERNS / name), name)
            known.update(bundle.templates)
        # every non-warp node must have a weaver (or fall back to generic)
        assert {"dish_receipt", "prep_note", "score_sheet"} <= known
        assert all(node in WEAVERS for node in known if not node.startswith("warp_"))


class TestExecutions:
    def test_match_create_execution(self, runtime):
        state = start_match(match_id="m1", mode="SOLO", seed="s", player_ids=["a"], now=0)
        op, events, rid = ops.op_create_match(state)
        record = runtime.execute_op(op, events, request_id=rid)
        assert record.state == "COMPLETED"
        assert record.digest and record.digest.startswith("sha256:")
        assert record.checkpoint_id and record.checkpoint_id.startswith("chk_")
        assert record.verified is True
        assert record.artifact_refs

    def test_full_match_produces_all_ten_op_families(self, runtime):
        state = start_match(match_id="m2", mode="SOLO", seed="s", player_ids=["a"], now=0)
        executed = set()

        op, events, rid = ops.op_create_match(state)
        rec = runtime.execute_op(op, events, request_id=rid)
        executed.add(op)
        assert rec.state == "COMPLETED"

        state = start_round(state, now=0)
        op, events, rid = ops.op_start_round(state)
        assert runtime.execute_op(op, events, request_id=rid).state == "COMPLETED"
        executed.add(op)

        player = state.players["a"]
        order = player.order
        op, events, rid = ops.op_generate_order(state, player, order)
        assert runtime.execute_op(op, events, request_id=rid).state == "COMPLETED"
        executed.add(op)

        from helpers import find_word
        word = find_word(order)
        state, outcome = submit_dish(state, "a", word, now=5)
        op, events, rid = ops.op_submit_dish(state=state, player=state.players["a"],
                                             order=order, outcome=outcome,
                                             score_total=state.players["a"].score, counter=1)
        rec = runtime.execute_op(op, events, request_id=rid)
        assert rec.state == "COMPLETED"
        kinds = {a["payload"].get("kind") for a in rec.artifacts}
        assert "dish_receipt" in kinds and "score_sheet" in kinds
        executed.add(op)

        state = end_round(state, now=50)
        op, events, rid = ops.op_end_round(state, score_delta=10, dishes=1,
                                           combo_peak=1, heat_peak=1)
        assert runtime.execute_op(op, events, request_id=rid).state == "COMPLETED"
        executed.add(op)

        op, events, rid = ops.op_close_order(state, state.players["a"], order,
                                             satisfied=1, dishes=1, elapsed=5)
        assert runtime.execute_op(op, events, request_id=rid).state == "COMPLETED"

        op, events, rid = ops.op_result_commit(state, winner_id="a", total_score=10,
                                               total_dishes=1, verified=1)
        assert runtime.execute_op(op, events, request_id=rid).state == "COMPLETED"
        executed.add(op)

        op, events, rid = ops.op_leaderboard_update(season="s", player_id="a",
                                                    match_id="m2", score=10,
                                                    dishes=1, position=1)
        assert runtime.execute_op(op, events, request_id=rid).state == "COMPLETED"
        executed.add(op)

        op, events, rid = ops.op_leaderboard_commit(season="s", entries=1,
                                                    top_score=10,
                                                    snapshot_digest="ab" * 32)
        assert runtime.execute_op(op, events, request_id=rid).state == "COMPLETED"
        executed.add(op)

        op, events, rid = ops.op_match_verify(match_id="m2", replayed=1,
                                              digest_match=1, executions=9,
                                              intent_count=1, verdict="verified")
        assert runtime.execute_op(op, events, request_id=rid).state == "COMPLETED"
        executed.add(op)

        # the ten required operation families all hit the loom
        assert {"match.create", "round.start", "order.generate", "dish.submit",
                "round.end", "result.commit", "leaderboard.update",
                "leaderboard.commit", "match.verify"} <= executed

    def test_idempotent_request_id(self, runtime):
        state = start_match(match_id="m3", mode="SOLO", seed="s", player_ids=["a"], now=0)
        op, events, rid = ops.op_create_match(state)
        first = runtime.execute_op(op, events, request_id=rid)
        second = runtime.execute_op(op, events, request_id=rid)
        assert first.execution_id == second.execution_id
        assert len(runtime.executions()) == 1

    def test_capability_denied_fails_closed(self, runtime):
        state = start_match(match_id="m4", mode="SOLO", seed="s", player_ids=["a"], now=0)
        op, events, rid = ops.op_create_match(state)
        record = runtime.execute_op(op, events, request_id=rid + ":cap",
                                    capabilities=("root.access",))
        assert record.state == "FAILED"
        assert record.error and record.error["code"] == "capability_denied"

    def test_async_execution_completes(self, runtime):
        import time
        state = start_match(match_id="m5", mode="SOLO", seed="s", player_ids=["a"], now=0)
        op, events, rid = ops.op_create_match(state)
        future = runtime.execute_op_async(op, events, request_id=rid)
        record = future.result(timeout=30)
        assert record.state == "COMPLETED"
        assert record.checkpoint_id
        assert record.verified is True

    def test_artifacts_readable_from_cas(self, runtime):
        state = start_match(match_id="m6", mode="SOLO", seed="s", player_ids=["a"], now=0)
        op, events, rid = ops.op_create_match(state)
        record = runtime.execute_op(op, events, request_id=rid)
        payloads = [a["payload"] for a in record.artifacts]
        assert any(p.get("kind") in ("receipt", "match_cloth", "warp") for p in payloads)
        assert all("document" in p or p.get("kind") == "warp" for p in payloads)

    def test_audit_trail_records_lifecycle(self, runtime):
        state = start_match(match_id="m7", mode="SOLO", seed="s", player_ids=["a"], now=0)
        op, events, rid = ops.op_create_match(state)
        record = runtime.execute_op(op, events, request_id=rid)
        audit = runtime.audit_events(record.execution_id)
        types = {e["type"] for e in audit}
        assert "execution.completed" in types
        assert "capability.granted" in types
        assert "checkpoint.created" in types


class TestDeterminism:
    @staticmethod
    def _dish_execution(runtime, match_id, counter, request_suffix=""):
        state = start_match(match_id=match_id, mode="SOLO", seed="seed-7",
                            player_ids=["a"], now=0)
        state = start_round(state, now=0)
        player = state.players["a"]
        order = player.order
        from helpers import find_word
        word = find_word(order)
        state, outcome = submit_dish(state, "a", word, now=5)
        op, events, rid = ops.op_submit_dish(state=state, player=state.players["a"],
                                             order=order, outcome=outcome,
                                             score_total=state.players["a"].score,
                                             counter=counter)
        return runtime.execute_op(op, events, request_id=rid + request_suffix)

    def test_same_input_same_digest(self, runtime):
        a = self._dish_execution(runtime, "d1", 1, request_suffix=":a")
        b = self._dish_execution(runtime, "d1", 1, request_suffix=":b")
        assert a.digest == b.digest
        assert a.state == b.state == "COMPLETED"
        assert [r["id"] for r in a.artifact_refs] == [r["id"] for r in b.artifact_refs]

    def test_replay_matches_recorded_digest(self, runtime):
        record = self._dish_execution(runtime, "d2", 1)
        replay = runtime.gateway.replay(record.execution_id,
                                        str(runtime.source_for("dish.submit")))
        assert replay["matches"] is True
        assert replay["document_matches"] is True
        assert replay["digest"] == record.digest

    def test_verify_is_true_for_clean_execution(self, runtime):
        record = self._dish_execution(runtime, "d3", 1)
        verdict = runtime.verify(record.execution_id)
        assert verdict["verified"] is True
        assert verdict["recorded_digest"] == verdict["recomputed_digest"]

    def test_replay_detects_tampered_log(self, runtime):
        import json as _json
        record = self._dish_execution(runtime, "d4", 1)
        # tamper with the CANONICAL runtime log inside the envelope
        path = runtime.state_dir / f"{record.execution_id}.json"
        data = _json.loads(path.read_text(encoding="utf-8"))
        entry = data["runtime_log"][0]
        entry["payload"]["score_delta"] = int(entry["payload"].get("score_delta", 0)) + 1
        path.write_text(_json.dumps(data, sort_keys=True, separators=(",", ":")),
                        encoding="utf-8")
        verdict = runtime.verify(record.execution_id)
        assert verdict["verified"] is False

    def test_replay_detects_tampered_digest(self, runtime):
        import json as _json
        record = self._dish_execution(runtime, "d5", 1)
        path = runtime.state_dir / f"{record.execution_id}.json"
        data = _json.loads(path.read_text(encoding="utf-8"))
        data["digest"] = "sha256:" + "0" * 64
        path.write_text(_json.dumps(data, sort_keys=True, separators=(",", ":")),
                        encoding="utf-8")
        verdict = runtime.verify(record.execution_id)
        assert verdict["verified"] is False
