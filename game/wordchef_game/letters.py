"""Letter trays ("ingredient baskets") — deterministic generation.

A tray must always contain at least one playable dish (>= 3 letters), so the
player can never get stuck. Rare letters are the "Golden Ingredients" source.
"""
from __future__ import annotations

from .dictionary import Dictionary
from .rng import Rng

# Base English letter frequency weights (relative, sum arbitrary).
LETTER_WEIGHTS: dict[str, int] = {
    "a": 83, "b": 16, "c": 31, "d": 39, "e": 125, "f": 21, "g": 18, "h": 47,
    "i": 76, "j": 3, "k": 7, "l": 45, "m": 25, "n": 73, "o": 77, "p": 22,
    "q": 2, "r": 68, "s": 66, "t": 91, "u": 27, "v": 11, "w": 16, "x": 3,
    "y": 17, "z": 2,
}

VOWELS = "aeiou"

# Letters that grant Golden Ingredients when used in a successful dish.
RARE_LETTERS = frozenset("jqxz")

# A slightly kinder secondary pool keeps the game lively without breaking rarity.
UNCOMMON_LETTERS = frozenset("kvwb")

TRAY_SIZE = 7
MIN_VOWELS = 2
MAX_VOWELS = 4


def letter_rarity(letter: str) -> int:
    """Rarity points of a letter (used for score bonuses + golden ingredients)."""
    letter = letter.lower()
    if letter in RARE_LETTERS:
        return 3
    if letter in UNCOMMON_LETTERS:
        return 2
    return 1


def golden_from_letters(word: str) -> int:
    """Golden Ingredients earned from the rare letters of a successful dish."""
    total = 0
    for letter in word.lower():
        if letter in RARE_LETTERS:
            total += 2
        elif letter in UNCOMMON_LETTERS and letter in "qzxjkv":
            total += 1
    return total


class LetterProfile:
    """Per-kitchen letter weights (kitchens change the ingredient market)."""

    def __init__(self, weights: dict[str, int] | None = None, vowel_bonus: int = 0):
        self.weights = dict(weights or LETTER_WEIGHTS)
        if vowel_bonus:
            for vowel in VOWELS:
                self.weights[vowel] = self.weights.get(vowel, 0) + vowel_bonus

    def draw(self, rng: Rng) -> str:
        letters = sorted(self.weights)  # stable order for determinism
        return rng.weighted_choice(letters, [self.weights[letter] for letter in letters])


def generate_tray(rng: Rng, dictionary: Dictionary, profile: LetterProfile | None = None,
                  size: int = TRAY_SIZE) -> str:
    """Generate a tray that always contains a playable word (>= 3 letters)."""
    profile = profile or LetterProfile()
    for attempt in range(24):
        tray: list[str] = []
        vowels = 0
        for _ in range(size):
            letter = profile.draw(rng)
            if letter in VOWELS:
                vowels += 1
            # re-draw extreme vowel imbalance on the last slots
            tray.append(letter)
        vowels = sum(1 for letter in tray if letter in VOWELS)
        if vowels < MIN_VOWELS or vowels > MAX_VOWELS:
            continue
        tray_str = "".join(tray)
        if dictionary.has_playable_word(tray_str, min_len=3):
            return tray_str
    # Deterministic fallback: a known-good basket built from the same rng.
    fallback = ["a", "e", "r", "s", "t", "i", "n"]
    return "".join(fallback[:size])


def swap_one_letter(rng: Rng, tray: str, profile: LetterProfile | None = None) -> str:
    """Golden restock: swap exactly one letter for a fresh ingredient."""
    profile = profile or LetterProfile()
    letters = list(tray.lower())
    index = rng.below(len(letters))
    replacement = profile.draw(rng)
    for _ in range(12):
        if replacement != letters[index]:
            break
        replacement = profile.draw(rng)
    letters[index] = replacement
    return "".join(letters)
