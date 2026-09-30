"""Deterministic scoring — one pure function, two callers.

The SAME code runs in the game engine and inside the Prolepsis weaver
handlers: the execution recomputes every dish and refuses to weave a score
sheet whose claimed numbers differ from the recomputation. That is the
anti-cheat core: a tampered client cannot make the loom commit a lie.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from .letters import golden_from_letters, letter_rarity
from .orders import Order, check_secret, flavor_bonus

COMBO_STEP = 0.25          # +25% per combo level
COMBO_CAP = 4.0            # hard cap on the combo multiplier
HEAT_STEP = 0.08           # +8% per heat level
SPICE_MULT = 2.0
SPEED_BONUS_THRESHOLD = 0.5   # submit within 50% of the timer -> al dente
SPEED_BONUS = 1.2
LATE_PENALTY = 0.9            # last 25% of the timer -> overcooked
PREP_TOKEN_CAP = 3
PREP_TOKEN_BONUS = 0.1        # +10% per prep token on the next dish
PREP_SCORE = 2                # points per letter for a prep word
SECRET_BONUS = 0.5            # +50% when the hidden objective lands


@dataclass(frozen=True)
class DishResult:
    valid: bool
    reason: str
    word: str
    word_len: int
    base: int
    rarity_bonus: int
    combo_mult: float
    heat_mult: float
    spice_mult: float
    kitchen_mult: float
    flavor_mult: float
    speed_mult: float
    prep_mult: float
    prep_tokens_used: int
    secret_hit: bool
    secret_mult: float
    score_delta: int
    combo_before: int
    combo_after: int
    heat_before: int
    heat_after: int
    golden_delta: int
    flavor: str
    burned: bool               # spice risk realized on a failed dish

    def to_dict(self) -> dict:
        data = asdict(self)
        # floats rounded to 3 decimals for stable canonical serialization
        for key, value in list(data.items()):
            if isinstance(value, float):
                data[key] = round(value, 3)
        return data


def combo_multiplier(combo: int) -> float:
    return min(COMBO_CAP, 1.0 + combo * COMBO_STEP)


def heat_multiplier(heat: int) -> float:
    return 1.0 + max(0, heat) * HEAT_STEP


def score_dish(
    *,
    word: str,
    order: Order,
    combo_before: int,
    heat_before: int,
    spice_active: bool,
    prep_tokens: int,
    kitchen_mult: float,
    elapsed_seconds: int,
    kitchen_rule: str = "none",
) -> DishResult:
    """The one authoritative dish→score computation.

    Pure and total: no clocks, no randomness, no IO. Same inputs, same output.
    """
    from .dictionary import load_dictionary  # local import keeps module light

    dictionary = load_dictionary()
    word = (word or "").lower()
    combo_before = max(0, combo_before)
    heat_before = max(0, heat_before)
    prep_tokens = max(0, min(PREP_TOKEN_CAP, prep_tokens))

    def _reject(reason: str, *, burned: bool = False) -> DishResult:
        # Failed dishes: combo resets (spice burns harder), heat cools.
        combo_after = 0
        if kitchen_rule == "combo_shield" and combo_before > 0:
            combo_after = max(0, combo_before - 1)   # ancient kitchen protects combo
        heat_drop = 3 if burned else 2
        if kitchen_rule == "heat_locked":
            heat_after = heat_before
        else:
            heat_after = max(0, heat_before - heat_drop)
        return DishResult(
            valid=False, reason=reason, word=word, word_len=len(word),
            base=0, rarity_bonus=0,
            combo_mult=1.0, heat_mult=1.0,
            spice_mult=SPICE_MULT if spice_active else 1.0,
            kitchen_mult=kitchen_mult, flavor_mult=1.0, speed_mult=1.0,
            prep_mult=1.0, prep_tokens_used=0,
            secret_hit=False, secret_mult=1.0,
            score_delta=0,
            combo_before=combo_before, combo_after=combo_after,
            heat_before=heat_before, heat_after=heat_after,
            golden_delta=0, flavor="", burned=burned,
        )

    from .orders import check_order
    ok, reason = check_order(order, word, dictionary)
    if not ok:
        return _reject(reason, burned=spice_active)

    length = len(word)
    base = length * length * 10
    rarity_bonus = sum(letter_rarity(c) * 4 for c in word)

    combo_mult = combo_multiplier(combo_before)
    heat_mult = heat_multiplier(heat_before)
    spice_mult = SPICE_MULT if spice_active else 1.0

    flavor_mult, flavor = flavor_bonus(order, word)

    total_time = max(1, order.time_limit)
    progress = min(1.0, max(0.0, elapsed_seconds / total_time))
    if progress <= SPEED_BONUS_THRESHOLD:
        speed_mult = SPEED_BONUS
    elif progress >= 1.0 - SPEED_BONUS_THRESHOLD / 2:
        speed_mult = LATE_PENALTY
    else:
        speed_mult = 1.0

    prep_mult = 1.0 + PREP_TOKEN_BONUS * prep_tokens

    secret_hit = check_secret(word, order.secret) if order.secret else False
    secret_mult = 1.0 + SECRET_BONUS if secret_hit else 1.0

    if kitchen_rule == "vowels_worth_more":
        vowels = sum(1 for c in word if c in "aeiou")
        base += vowels * 12
    elif kitchen_rule == "short_words_bonus" and length <= 4:
        base += 30
    elif kitchen_rule == "glitch_letters" and any(c in "jqxz" for c in word):
        base += 60

    raw = (base + rarity_bonus) * combo_mult * heat_mult * spice_mult \
        * kitchen_mult * flavor_mult * speed_mult * prep_mult * secret_mult
    score_delta = int(round(raw))

    combo_after = combo_before + 1
    heat_after = min(15, heat_before + 1)
    if kitchen_rule == "heat_locked":
        heat_after = max(heat_before, 8)

    golden_delta = golden_from_letters(word)

    return DishResult(
        valid=True, reason="ok", word=word, word_len=length,
        base=base, rarity_bonus=rarity_bonus,
        combo_mult=round(combo_mult, 3), heat_mult=round(heat_mult, 3),
        spice_mult=spice_mult, kitchen_mult=kitchen_mult,
        flavor_mult=round(flavor_mult, 3), speed_mult=speed_mult,
        prep_mult=round(prep_mult, 3), prep_tokens_used=prep_tokens,
        secret_hit=secret_hit, secret_mult=secret_mult,
        score_delta=score_delta,
        combo_before=combo_before, combo_after=combo_after,
        heat_before=heat_before, heat_after=heat_after,
        golden_delta=golden_delta, flavor=flavor, burned=False,
    )


def prep_score(word: str) -> int:
    """Points for a 'mise en place' prep word (short, safe, keeps combo)."""
    return PREP_SCORE * max(0, len(word or ""))
