"""Customer Orders — the heart of the Word Chef round loop.

An order is a constraint bundle the chef must satisfy with one dish (word),
plus its tray of letters and a timer. Orders are generated deterministically
from the match seed; the server is the only party that may create them.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from .content import CONTENT
from .dictionary import Dictionary
from .flavor import FLAVORS, flavor_of, pairing_multiplier
from .letters import LetterProfile, VOWELS, generate_tray
from .rng import Rng

KINDS = tuple(CONTENT["order_rules"])

BASE_TIME_LIMIT = 30          # seconds
MIN_TIME_LIMIT = 12


@dataclass(frozen=True)
class Order:
    order_id: str
    kind: str
    difficulty: int            # 1..9 — drives rewards and constraints
    tray: str                  # letters the SERVER dealt
    time_limit: int            # seconds, server-authoritative
    base_reward: int
    min_length: int = 0
    required_letter: str = ""
    theme: str = ""
    streak_needed: int = 1
    flavor_request: str = ""
    secret: str = ""           # hidden bonus objective (deterministic)
    used_words: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["used_words"] = list(self.used_words)
        return data

    @staticmethod
    def from_dict(data: dict) -> "Order":
        data = dict(data)
        data["used_words"] = tuple(data.get("used_words") or ())
        known = {f for f in Order.__dataclass_fields__}
        data = {k: v for k, v in data.items() if k in known}
        return Order(**data)


THEMES: dict[str, tuple[str, ...]] = {
    theme: tuple(words) for theme, words in CONTENT["themes"].items()
}

SECRETS = tuple(CONTENT["secrets"])



def theme_words(theme: str, dictionary: Dictionary) -> list[str]:
    return [w for w in THEMES.get(theme, ()) if w in dictionary]


TRAY_TARGET = 7


def build_tray(rng: Rng, dictionary: Dictionary, profile: LetterProfile | None = None,
               *, anchor: str = "", min_words: int = 1) -> str:
    """Build a 7-letter tray that always contains the anchor word (and at
    least `min_words` distinct playable dishes)."""
    profile = profile or LetterProfile()
    size = 7
    for attempt in range(40):
        letters = [c for c in anchor.lower() if c.isalpha()][:size]
        while len(letters) < size:
            letters.append(profile.draw(rng))
        if not (2 <= sum(1 for c in letters if c in VOWELS) <= 4):
            continue
        tray = "".join(letters)
        if anchor and not dictionary.formable(anchor, tray):
            continue
        playable = [w for w in dictionary.words if len(w) >= 2 and dictionary.formable(w, tray)]
        if len(playable) >= min_words:
            return tray
    # deterministic, always-playable fallback
    return (anchor.lower() + "aeiornst")[:size]


def make_order(rng: Rng, dictionary: Dictionary, *, order_id: str, difficulty: int,
               profile: LetterProfile | None = None, theme: str = "",
               streak_needed: int = 0, force_kind: str = "") -> Order:
    """Deterministically build one customer order."""
    difficulty = max(1, min(9, difficulty))
    kind = force_kind or rng.choice(list(KINDS))
    # Higher difficulty unlocks the constraint-heavy kinds more often.
    if difficulty <= 2 and kind in ("STREAK", "THEME", "SPEED") and not force_kind:
        kind = rng.choice(("SINGLE_WORD", "MIN_LENGTH", "CONTAINS_LETTER"))

    min_length = 0
    required_letter = ""
    use_theme = ""
    streak = max(1, streak_needed or 1)
    time_limit = BASE_TIME_LIMIT + max(0, 8 - difficulty) * 2

    # Every order is SOLVABLE by construction: the tray is built around an
    # anchor word that already satisfies the order constraint.
    anchor = ""
    if kind == "MIN_LENGTH":
        min_length = min(7, 3 + difficulty // 2)
        pool = [w for w in dictionary.words
                if min_length <= len(w) <= TRAY_TARGET]
        anchor = rng.choice(pool) if pool else ""
    elif kind == "CONTAINS_LETTER":
        pool = [w for w in dictionary.words if 3 <= len(w) <= TRAY_TARGET]
        anchor = rng.choice(pool) if pool else ""
        required_letter = rng.choice(sorted(set(anchor))) if anchor else ""
    elif kind == "THEME":
        use_theme = theme or rng.choice(sorted(THEMES))
        pool = [w for w in theme_words(use_theme, dictionary)
                if len(w) <= TRAY_TARGET]
        anchor = rng.choice(pool) if pool else ""
        time_limit += 8
    elif kind == "STREAK":
        streak = 2 if difficulty < 5 else 3
        time_limit += 12
    elif kind == "SPEED":
        time_limit = max(MIN_TIME_LIMIT, 14 + difficulty)

    tray = build_tray(rng, dictionary, profile, anchor=anchor,
                      min_words=streak if kind == "STREAK" else 1)

    flavor_request = rng.choice(list(FLAVORS)) if difficulty >= 2 and rng.chance(1, 2) else ""

    # Secret menu: ~1 in 4 orders, deterministic.
    secret = rng.choice(list(SECRETS)) if rng.chance(1, 4) else ""

    base_reward = 40 + difficulty * 18 + len(tray) * 3
    if kind == "STREAK":
        base_reward = int(base_reward * 1.4)
    if kind == "THEME":
        base_reward = int(base_reward * 1.3)
    if flavor_request:
        base_reward = int(base_reward * 1.15)

    return Order(
        order_id=order_id,
        kind=kind,
        difficulty=difficulty,
        tray=tray,
        time_limit=max(MIN_TIME_LIMIT, time_limit),
        base_reward=base_reward,
        min_length=min_length,
        required_letter=required_letter,
        theme=use_theme,
        streak_needed=streak,
        flavor_request=flavor_request,
        secret=secret,
    )


def check_secret(word: str, secret: str) -> bool:
    word = word.lower()
    if not secret:
        return False
    vowels = sum(1 for c in word if c in "aeiou")
    consonants = len(word) - vowels
    if secret == "min_length_5":
        return len(word) >= 5
    if secret == "two_vowels":
        return vowels >= 2
    if secret == "three_consonants":
        return consonants >= 3
    if secret == "has_double":
        return any(word[i] == word[i + 1] for i in range(len(word) - 1))
    if secret == "ends_vowel":
        return word[-1] in "aeiou"
    if secret == "no_rare_letters":
        return not any(c in "jqxz" for c in word)
    return False


def check_order(order: Order, word: str, dictionary: Dictionary) -> tuple[bool, str]:
    """Validate a dish against the order. Returns (ok, reason)."""
    word = word.lower()
    if not word:
        return False, "empty_word"
    if not dictionary.formable(word, order.tray):
        if word not in dictionary:
            return False, "not_in_dictionary"
        return False, "not_formable_from_tray"
    if word in order.used_words:
        return False, "word_already_served"
    if order.kind == "MIN_LENGTH" and len(word) < order.min_length:
        return False, f"needs_at_least_{order.min_length}_letters"
    if order.kind == "CONTAINS_LETTER" and order.required_letter not in word:
        return False, f"needs_letter_{order.required_letter}"
    if order.kind == "THEME":
        allowed = theme_words(order.theme, dictionary)
        if word not in allowed:
            return False, f"not_on_the_{order.theme.replace(' ', '_')}_menu"
    return True, "ok"


def flavor_bonus(order: Order, word: str) -> tuple[float, str]:
    actual = flavor_of(word)
    return pairing_multiplier(order.flavor_request or None, actual), actual
