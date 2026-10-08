"""Kitchens loaded from the canonical WordChef content pack."""
from __future__ import annotations

from dataclasses import dataclass

from .content import CONTENT
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
    rule: str
    theme: str = ""


def _profile(overrides: dict[str, int]) -> LetterProfile:
    weights = dict(LETTER_WEIGHTS)
    weights.update(overrides)
    return LetterProfile(weights)


KITCHENS: tuple[Kitchen, ...] = tuple(
    Kitchen(
        kitchen_id=item["id"],
        name=item["name"],
        tagline=item["tagline"],
        unlock_xp=item["unlock_xp"],
        score_mult=item["score_mult"],
        timer_mult=item["timer_mult"],
        letter_profile=_profile(item.get("letter_overrides", {})),
        rule=item["rule"],
        theme=item.get("theme", ""),
    )
    for item in CONTENT["kitchens"]
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
