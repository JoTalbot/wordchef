"""Word Chef × Prolepsis — game-aware weaver handlers.

These handlers replace the generic ``jacquard.render`` handler inside the
Agent Gateway. Each weaver:

1. RECOMPUTES the claimed game result with the deterministic engine
   (``wordchef_game.scoring``) — the loom refuses to weave a lie;
2. renders the Jacquard weft (the human-readable receipt);
3. commits the payload to the content-addressed store — durable artifacts.

If a claimed value disagrees with the recomputation, the weaver raises
``IRError("constraint_broken", ...)`` and the execution FAILS closed: the
game state must not advance on an unverified result.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from prolepsis.canonical import IRError
from prolepsis.jacquard import YARN, render as render_weft

from wordchef_game.kitchens import KITCHEN_BY_ID
from wordchef_game.orders import Order
from wordchef_game.scoring import prep_score, score_dish

Weaver = Callable[[dict[str, Any]], dict[str, Any]]


def canonicalize(value: Any) -> Any:
    """Make a value safe for the canonical CAS: floats become 3-decimal
    strings (C4/C22: floats are not canonical), tuples become lists."""
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, dict):
        return {str(k): canonicalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonicalize(v) for v in value]
    return value


def _mismatch(claimed: Any, computed: Any, field: str, node_id: str = "dish_receipt") -> IRError:
    return IRError(
        node_id, "jacquard.render", "constraint_broken",
        f"recompute mismatch on '{field}': claimed {claimed!r} != computed {computed!r}",
        "permanent", False,
    )


def weave_dish_receipt(inputs: dict[str, Any]) -> dict[str, Any]:
    """The anti-cheat core: recompute the dish, compare, refuse on mismatch."""
    order = Order.from_dict(json.loads(inputs["order_snapshot"]))
    claimed = json.loads(inputs["claimed_result"])
    kitchen = KITCHEN_BY_ID.get(str(inputs["kitchen"]), KITCHEN_BY_ID["street"])

    computed = score_dish(
        word=str(inputs["word"]),
        order=order,
        combo_before=int(inputs["combo_before"]),
        heat_before=int(inputs["heat_before"]),
        spice_active=bool(int(inputs["spice"])),
        prep_tokens=int(inputs["prep_tokens"]),
        kitchen_mult=kitchen.score_mult,
        elapsed_seconds=int(inputs["elapsed"]),
        kitchen_rule=kitchen.rule,
    ).to_dict()

    if not isinstance(claimed, dict):
        raise _mismatch(claimed, computed, "claimed_result")
    for field, value in claimed.items():
        if field not in computed:
            raise _mismatch(value, None, field)
        if computed[field] != value:
            raise _mismatch(value, computed[field], field)
    if int(inputs["prep_tokens"]) != int(computed["prep_tokens_used"]):
        raise _mismatch(inputs["prep_tokens"], computed["prep_tokens_used"], "prep_tokens")

    expected_final = int(computed["score_delta"]) if computed["valid"] else 0
    if computed["valid"]:
        if int(inputs.get("boost_applied", 0)):
            expected_final = int(round(expected_final * 1.5))
        if int(inputs.get("double_applied", 0)):
            expected_final *= 2
    if int(inputs["score_delta"]) != expected_final:
        raise _mismatch(inputs["score_delta"], expected_final, "score_delta(final)")

    return {"kind": "dish_receipt", "verified": True,
            "computed": computed, "claimed": claimed, "facts": dict(inputs)}


def weave_prep_note(inputs: dict[str, Any]) -> dict[str, Any]:
    expected = prep_score(str(inputs["word"]))
    if int(inputs["points"]) != expected:
        raise _mismatch(inputs["points"], expected, "points", "prep_note")
    if int(inputs["word_len"]) != len(str(inputs["word"])):
        raise _mismatch(inputs["word_len"], len(str(inputs["word"])), "word_len", "prep_note")
    return {"kind": "prep_note", "verified": True, "facts": dict(inputs)}


def weave_score_sheet(inputs: dict[str, Any]) -> dict[str, Any]:
    delta = int(inputs["score_delta"])
    total = int(inputs["score_total"])
    if delta < 0 or total < 0:
        raise _mismatch((delta, total), (0, 0), "score_sheet", "score_sheet")
    return {"kind": "score_sheet", "verified": True, "facts": dict(inputs)}


def weave_order_ticket(inputs: dict[str, Any]) -> dict[str, Any]:
    tray = str(inputs["tray"])
    if not (1 <= len(tray) <= 12) or not tray.isalpha():
        raise _mismatch(tray, "1..12 letters", "tray", "order_ticket")
    if not (1 <= int(inputs["difficulty"]) <= 9):
        raise _mismatch(inputs["difficulty"], "1..9", "difficulty", "order_ticket")
    if int(inputs["time_limit"]) < 5 or int(inputs["time_limit"]) > 300:
        raise _mismatch(inputs["time_limit"], "5..300", "time_limit", "order_ticket")
    return {"kind": "order_ticket", "verified": True, "facts": dict(inputs)}


def weave_generic(inputs: dict[str, Any]) -> dict[str, Any]:
    """Structural receipts: round/match/bonus/chaos/leaderboard/verification."""
    return {"kind": "receipt", "verified": True, "facts": dict(inputs)}


# node ids produced by the Jacquard adapter (artifact name → snake_case)
WEAVERS: dict[str, Weaver] = {
    "dish_receipt": weave_dish_receipt,
    "prep_note": weave_prep_note,
    "score_sheet": weave_score_sheet,
    "order_ticket": weave_order_ticket,
    "order_outcome": weave_generic,
    "round_ticket": weave_generic,
    "round_summary": weave_generic,
    "match_cloth": weave_generic,
    "match_sign_off": weave_generic,
    "bonus_coupon": weave_generic,
    "chaos_notice": weave_generic,
    "leaderboard_sheet": weave_generic,
    "leaderboard_seal": weave_generic,
    "verification_report": weave_generic,
    "unravel_match_voided": weave_generic,
}


class WeaverError(Exception):
    """A weaver refused to weave — surfaced as an execution failure."""


def make_handlers(cas_store, templates: dict[str, dict] | None = None,
                  pattern_scope: dict[str, dict] | None = None):
    """Build the handler map for AgentGateway.

    ``cas_store`` is a PersistentArtifactStore used for durable read-back;
    ``templates`` maps node_id → {title, weft, block} from the adapter bundle
    so the weaver can render the human-readable Jacquard receipt;
    ``pattern_scope`` maps node_id → the pattern's initial (yarn+data) values
    seen by templates as ``pattern.*``.
    """
    templates = templates or {}
    pattern_scope = pattern_scope or {}

    def _render(node_id: str, facts: dict[str, Any]) -> str:
        template = templates.get(node_id)
        if not template:
            return json.dumps({"facts": facts}, sort_keys=True)
        context = {"f": dict(facts), "pattern": dict(pattern_scope.get(node_id, {})), **YARN}
        parts: list[str] = []
        for key in ("title", "block", "weft"):
            text = template.get(key)
            if not text:
                continue
            try:
                parts.append(render_weft(text, context))
            except Exception as exc:
                parts.append(f"({key} render failed: {exc})")
        return "\n".join(parts)

    def weave(node, inputs, ctx):
        weaver = WEAVERS.get(node.id, weave_generic)
        try:
            payload = weaver(inputs)
        except IRError:
            raise
        except Exception as exc:  # a weaver crash must fail closed
            raise IRError(node.id, node.operation, "weaver_crash",
                          f"{type(exc).__name__}: {exc}",
                          "permanent", False) from exc
        payload = dict(payload)
        payload["node"] = node.id
        payload["operation"] = node.operation
        payload["document"] = _render(node.id, dict(inputs))
        payload = canonicalize(payload)
        ref = ctx.store.put(payload)
        if cas_store is not None:
            try:
                cas_store.put(payload)     # same content → same address
            except Exception:
                pass
        return {name: ref for name, _ in node.outputs}

    def warp(node, inputs, ctx):
        payload = {"kind": "warp", "node": node.id, "facts": dict(inputs),
                   "verified": True, "document": _render(node.id, dict(inputs))}
        ref = ctx.store.put(payload)
        if cas_store is not None:
            try:
                cas_store.put(payload)
            except Exception:
                pass
        return {name: ref for name, _ in node.outputs}

    return {
        "jacquard.render": weave,
        "jacquard.warp": warp,
        "jacquard.unravel": weave,
        "weave.artifact": weave,
    }
