"""Kitchens — progression stages, each with its own house rules.

Unlocking a kitchen changes the game: letter markets, timers, score
multipliers and special rules. Progression is by lifetime score (XP).
"""
from __future__ import annotations

from dataclasses import dataclass

from .letters import LetterProfile, LETTER_WEIGHTS


@dataclass(frozen=True)
class Kitchen:
    kitchen_id: str
    name: str
    tagline: str
    unlock_xp: int
    score_mult: float
    timer_mult: float
    letter_profile: LetterProfile
    rule: str               # special house rule id
    theme: str = ""         # preferred order theme for this kitchen


def _profile(**overrides: int) -> LetterProfile:
    weights = dict(LETTER_WEIGHTS)
    weights.update(overrides)
    return LetterProfile(weights)


KITCHENS: tuple[Kitchen, ...] = (
    Kitchen(
        "street", "Street Kitchen", "Where every chef starts. Honest letters.",
        0, 1.0, 1.0, LetterProfile(), "none", "street food",
    ),
    Kitchen(
        "bakery", "Bakery", "Vowels rise like dough. Sweet words earn more.",
        2_000, 1.05, 1.1, _profile(a=110, e=150, i=95, o=100, u=40), "vowels_worth_more",
        "bakery",
    ),
    Kitchen(
        "sushi", "Sushi Bar", "Precision cuts. Short dishes, sharp rewards.",
        6_000, 1.1, 0.9, _profile(t=110, a=95, o=90, i=85), "short_words_bonus",
        "sushi",
    ),
    Kitchen(
        "space", "Space Kitchen", "Zero gravity: letters drift in from the void.",
        14_000, 1.15, 1.0, _profile(), "wildcard_letter", "space",
    ),
    Kitchen(
        "cyber", "Cyber Kitchen", "Glitch letters mutate. So do the orders.",
        26_000, 1.2, 0.95, _profile(x=8, z=8, q=6, j=6), "glitch_letters", "cyber",
    ),
    Kitchen(
        "ancient", "Ancient Kitchen", "Runes remember every dish you ever served.",
        44_000, 1.25, 1.05, _profile(), "combo_shield", "ancient",
    ),
    Kitchen(
        "midnight", "Midnight Diner", "The final shift. Heat never cools here.",
        70_000, 1.35, 1.0, _profile(), "heat_locked", "night market",
    ),
)

KITCHEN_BY_ID = {k.kitchen_id: k for k in KITCHENS}


def kitchen_for_xp(xp: int) -> Kitchen:
    current = KITCHENS[0]
    for kitchen in KITCHENS:
        if xp >= kitchen.unlock_xp:
            current = kitchen
    return current


def next_kitchen(xp: int) -> Kitchen | None:
    for kitchen in KITCHENS:
        if xp < kitchen.unlock_xp:
            return kitchen
    return None
