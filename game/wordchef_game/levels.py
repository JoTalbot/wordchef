"""Campaign levels — «letter-pool crossword» (Повар Слов-style).

A level is a scrambled letter pool (the wheel) and a crossword board whose
answers are all formable from that pool; leftover valid words go to the bonus
jar. Generation is pure and deterministic per level number and is mirrored
1:1 in `frontend/lib/levels.ts` (same LCG, same sort orders) — parity is
enforced by tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).parent / "data" / "words_ru.txt"

LCG_MULT = 1103515245
LCG_ADD = 12345
LCG_MOD = 2 ** 31


class Lcg:
    """Tiny LCG shared with the TypeScript mirror."""

    def __init__(self, seed: int) -> None:
        self.state = seed % LCG_MOD

    def next(self) -> int:
        self.state = (self.state * LCG_MULT + LCG_ADD) % LCG_MOD
        return self.state


@lru_cache(maxsize=1)
def load_words_ru() -> tuple[str, ...]:
    """Sorted, filtered Russian words (3–8 letters, а-я only, no ё)."""
    words: set[str] = set()
    for line in _DATA.read_text(encoding="utf-8").splitlines():
        w = line.strip().lower().replace("ё", "е")
        if 3 <= len(w) <= 8 and all("а" <= ch <= "я" for ch in w):
            words.add(w)
    return tuple(sorted(words))


def _subset(word: str, pool: str) -> bool:
    """word letters ⊆ pool multiset."""
    left = list(pool)
    for ch in word:
        if ch in left:
            left.remove(ch)
        else:
            return False
    return True


@dataclass
class LevelWord:
    word: str
    r: int
    c: int
    across: bool


@dataclass
class Level:
    level_no: int
    pool: str
    wheel: list[str]
    board: list[LevelWord]
    bonus: list[str]
    dish: dict
    kitchen: str

    def to_dict(self) -> dict:
        return {
            "level_no": self.level_no,
            "pool": self.pool,
            "wheel": list(self.wheel),
            "board": [
                {"word": w.word, "r": w.r, "c": w.c, "across": w.across}
                for w in self.board
            ],
            "bonus": list(self.bonus),
            "dish": dict(self.dish),
            "kitchen": self.kitchen,
        }


DISHES = [
    {"emoji": "🥗", "name": "Салат"},
    {"emoji": "🍕", "name": "Пицца"},
    {"emoji": "🍣", "name": "Суши"},
    {"emoji": "🍜", "name": "Лапша"},
    {"emoji": "🍰", "name": "Торт"},
    {"emoji": "🥘", "name": "Рагу"},
    {"emoji": "🥟", "name": "Пельмени"},
    {"emoji": "🍤", "name": "Креветки"},
    {"emoji": "🧁", "name": "Капкейк"},
    {"emoji": "🥪", "name": "Сэндвич"},
    {"emoji": "🍲", "name": "Суп"},
    {"emoji": "🍥", "name": "Рулет"},
    {"emoji": "🥣", "name": "Борщ"},
    {"emoji": "🍢", "name": "Шашлык"},
    {"emoji": "🥞", "name": "Блины"},
    {"emoji": "🥧", "name": "Пирог"},
    {"emoji": "🍩", "name": "Пончик"},
    {"emoji": "🧇", "name": "Вафли"},
    {"emoji": "🍦", "name": "Мороженое"},
    {"emoji": "🌮", "name": "Тако"},
    {"emoji": "🍚", "name": "Плов"},
    {"emoji": "🥤", "name": "Смузи"},
]

KITCHENS = ["street", "bakery", "sushi", "space", "cyber", "ancient", "midnight"]


def _pool_length(level_no: int) -> int:
    if level_no < 10:
        return 5
    if level_no < 30:
        return 6
    return 7


def _board_size(level_no: int) -> int:
    return min(4 + level_no // 20, 8)


def _place(words: list[str]) -> list[LevelWord]:
    """Deterministic greedy crossword: longest first, first fit wins."""
    ordered = sorted(words, key=lambda w: (-len(w), w))
    grid: dict[tuple[int, int], str] = {}
    placed: list[LevelWord] = []

    def can_place(word: str, r: int, c: int, across: bool) -> bool:
        dr, dc = (0, 1) if across else (1, 0)
        # end caps free
        if (r - dr, c - dc) in grid or (r + dr * len(word), c + dc * len(word)) in grid:
            return False
        crossings = 0
        for i, ch in enumerate(word):
            rr, cc = r + dr * i, c + dc * i
            cur = grid.get((rr, cc))
            if cur is not None:
                if cur != ch:
                    return False
                crossings += 1
                continue
            # side neighbours must be empty
            if across:
                if (rr - 1, cc) in grid or (rr + 1, cc) in grid:
                    return False
            else:
                if (rr, cc - 1) in grid or (rr, cc + 1) in grid:
                    return False
        return crossings == (1 if placed else 0)

    def do_place(word: str, r: int, c: int, across: bool) -> None:
        dr, dc = (0, 1) if across else (1, 0)
        for i, ch in enumerate(word):
            grid[(r + dr * i, c + dc * i)] = ch
        placed.append(LevelWord(word, r, c, across))

    first = ordered[0]
    do_place(first, 0, 0, True)

    for word in ordered[1:]:
        # scan crossing opportunities in the order cells were laid down
        cells = sorted(grid.items(), key=lambda kv: (kv[0][0], kv[0][1]))
        done = False
        for (rr, cc), letter in cells:
            if done:
                break
            for i, ch in enumerate(word):
                if done:
                    break
                if ch != letter:
                    continue
                for across in (True, False):
                    # the crossing cell must belong to a perpendicular word
                    fits_existing = any(
                        (p.across != across and p.r <= rr <= p.r + (0 if p.across else len(p.word) - 1)
                         and p.c <= cc <= p.c + (len(p.word) - 1 if p.across else 0))
                        for p in placed
                    )
                    if not fits_existing:
                        continue
                    r0 = rr if across else rr - i
                    c0 = cc - i if across else cc
                    if can_place(word, r0, c0, across):
                        do_place(word, r0, c0, across)
                        done = True
                        break
            # end for i
        # not placeable → skip (kept as bonus)

    return placed


def generate_level(level_no: int) -> Level:
    words = load_words_ru()
    rng = Lcg(level_no * 2654435761 + 12345)

    pool_len = _pool_length(level_no)
    candidates = [w for w in words if len(w) == pool_len]
    size = _board_size(level_no)
    needed = size + 2

    # pick the richest of 24 seeded samples (mirrored exactly in TS)
    pool = ""
    formable: list[str] = []
    for _ in range(24):
        cand = candidates[rng.next() % len(candidates)]
        f = [w for w in words if len(w) >= 3 and _subset(w, cand)]
        if len(f) >= needed:
            pool, formable = cand, f
            break
        if pool == "" or len(f) > len(formable):
            pool, formable = cand, f

    formable_rest = sorted(
        (w for w in formable if w != pool), key=lambda w: (len(w), w))
    size = _board_size(level_no)
    board_words = [pool] + formable_rest[: size - 1]
    bonus = sorted(set(formable) - set(board_words))

    wheel = list(pool)
    for i in range(len(wheel) - 1, 0, -1):
        j = rng.next() % (i + 1)
        wheel[i], wheel[j] = wheel[j], wheel[i]

    placed = _place(board_words)
    placed_words = {p.word for p in placed}
    bonus = sorted(set(bonus) | (set(board_words) - placed_words))

    return Level(
        level_no=level_no,
        pool=pool,
        wheel=wheel,
        board=placed,
        bonus=bonus,
        dish=dict(DISHES[(level_no // 10) % len(DISHES)]),
        kitchen=KITCHENS[min(len(KITCHENS) - 1, level_no // 20)],
    )


def classify_word(level: Level, word: str) -> str:
    """→ 'board' | 'bonus' | 'invalid'."""
    w = word.strip().lower().replace("ё", "е")
    if any(p.word == w for p in level.board):
        return "board"
    if w in level.bonus:
        return "bonus"
    return "invalid"
