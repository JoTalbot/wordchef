"""Chaos Kitchen events — players sabotage each other (never pay-to-win).

Every chaos event is deterministic: targets and parameters come from the
match RNG stream, so a match replay reproduces the exact same chaos.
"""
from __future__ import annotations

from dataclasses import dataclass

EVENTS = (
    "STEAL_INGREDIENT",   # actor steals one letter from the victim's tray
    "BURN_TIMER",         # victim's order timer is cut
    "SPICE_STORM",        # everyone's next dish is forced spicy (x2, risky)
    "INGREDIENT_SWAP",    # two players swap trays
    "DOUBLE_ORDER",       # victim's next order pays double
)


@dataclass(frozen=True)
class ChaosEvent:
    event_type: str
    actor_id: str
    victim_id: str
    param: str = ""

    def to_dict(self) -> dict:
        return {"event_type": self.event_type, "actor_id": self.actor_id,
                "victim_id": self.victim_id, "param": self.param}


def pick_chaos(rng, player_ids: list[str], actor_id: str = "") -> ChaosEvent:
    """Deterministically pick a chaos event and its victim."""
    actor = actor_id or rng.choice(list(player_ids))
    others = [p for p in player_ids if p != actor]
    if not others:
        others = list(player_ids)
    victim = rng.choice(others)
    event_type = rng.choice(list(EVENTS))
    param = ""
    if event_type == "BURN_TIMER":
        param = str(rng.between(6, 12))          # seconds burned
    elif event_type == "STEAL_INGREDIENT":
        param = str(rng.between(1, 2))           # letters stolen
    return ChaosEvent(event_type, actor, victim, param)
