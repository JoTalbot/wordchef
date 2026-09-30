"""Shared test helpers — word discovery against the full dictionary."""
from __future__ import annotations

from wordchef_game.dictionary import load_dictionary
from wordchef_game.orders import Order, check_order

D = load_dictionary()


def find_word(order: Order, allow_skip=True) -> str | None:
    """A valid dish for the order, longest-first (or None if hopeless)."""
    candidates = [w for w in D.words if check_order(order, w, D)[0]]
    if not candidates:
        return None
    candidates.sort(key=lambda w: (-len(w), w))
    return candidates[0]


def serve_valid(state, player_id="a", now=5.0):
    """Serve a valid dish; returns (state, outcome, order, word)."""
    from wordchef_game.engine import submit_dish
    player = state.players[player_id]
    order = player.order
    word = find_word(order)
    assert word is not None, f"no valid word for order {order.kind}/{order.theme}"
    state, outcome = submit_dish(state, player_id, word, now=now)
    return state, outcome, order, word
