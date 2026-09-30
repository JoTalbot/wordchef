"""Flavor Profiles — an original Word Chef mechanic.

Every dish (word) maps deterministically to a flavor profile based on its
letter composition. Customers crave a flavor; matching it multiplies the
reward, clashing with it slightly hurts. The mapping is pure and stable:

* vowels dominate            -> ``sweet``     (pastries, desserts)
* consonants dominate hard   -> ``savory``    (grills, stews)
* rare letters present       -> ``umami``     (aged, fermented, luxurious)
* ends with 'y' or 'o'       -> ``tangy``     (dressings, citrus)
* contains a doubled letter  -> ``rich``      (slow-cooked, creamy)
* otherwise                  -> ``classic``   (house style)
"""
from __future__ import annotations

FLAVORS = ("sweet", "savory", "umami", "tangy", "rich", "classic")

RARE = frozenset("jqxz")

# Pairing matrix: requested x actual -> multiplier.
_PAIR_BONUS = 1.5
_PAIR_NEUTRAL = 1.0
_PAIR_CLASH = 0.85

_CLASHES = {
    ("sweet", "savory"), ("savory", "sweet"),
    ("umami", "tangy"), ("tangy", "umami"),
    ("rich", "tangy"), ("tangy", "rich"),
}


def flavor_of(word: str) -> str:
    word = word.lower()
    letters = [c for c in word if c.isalpha()]
    if not letters:
        return "classic"
    if any(c in RARE for c in letters):
        return "umami"
    doubles = any(letters[i] == letters[i + 1] for i in range(len(letters) - 1))
    if doubles:
        return "rich"
    if word[-1] in "yo":
        return "tangy"
    vowels = sum(1 for c in letters if c in "aeiou")
    consonants = len(letters) - vowels
    if vowels > consonants:
        return "sweet"
    if consonants >= vowels * 2 and consonants >= 4:
        return "savory"
    return "classic"


def pairing_multiplier(requested: str | None, actual: str) -> float:
    if requested is None or requested == actual:
        return _PAIR_BONUS if requested is not None else _PAIR_NEUTRAL
    if (requested, actual) in _CLASHES:
        return _PAIR_CLASH
    return _PAIR_NEUTRAL
