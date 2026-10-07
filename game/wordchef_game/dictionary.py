"""Word dictionary with fast formability queries.

The dictionary is a curated list of common English words (2-9 letters).
Two indices power gameplay:

* ``words_by_signature`` — anagrams of a letter multiset (dish discovery);
* ``words_by_length``    — order generation and "longest dish" queries.

``formable(word, tray)`` is the anti-cheat primitive: a submitted dish must be
a real word AND be constructible from the letters the SERVER gave out.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from .content import dictionary_path

DATA_FILE = Path(__file__).parent / "data" / "dictionary.txt"

MIN_WORD_LEN = 2
MAX_WORD_LEN = 9


class Dictionary:
    def __init__(self, words: list[str]):
        self.words: tuple[str, ...] = tuple(words)
        self._set = set(self.words)
        self.by_length: dict[int, list[str]] = defaultdict(list)
        self.by_signature: dict[tuple, list[str]] = defaultdict(list)
        for word in self.words:
            self.by_length[len(word)].append(word)
            self.by_signature[signature(word)].append(word)

    def __contains__(self, word: str) -> bool:
        return word in self._set

    def __len__(self) -> int:
        return len(self.words)

    def is_word(self, word: str) -> bool:
        return isinstance(word, str) and word.lower() in self._set

    def formable(self, word: str, tray: str) -> bool:
        """True when `word` can be spelled from the letters in `tray`."""
        if not self.is_word(word):
            return False
        need = Counter(word.lower())
        have = Counter(tray.lower())
        for letter, count in need.items():
            if have[letter] < count:
                return False
        return True

    def longest_words_in(self, tray: str, limit: int = 20) -> list[str]:
        """All dictionary words formable from `tray`, longest first."""
        have = Counter(tray.lower())
        found: list[str] = []
        for word in self.words:
            if len(word) > len(tray):
                continue
            need = Counter(word)
            ok = True
            for letter, count in need.items():
                if have[letter] < count:
                    ok = False
                    break
            if ok:
                found.append(word)
        found.sort(key=lambda w: (-len(w), w))
        return found[:limit]

    def has_playable_word(self, tray: str, min_len: int = 3) -> bool:
        have = Counter(tray.lower())
        for word in self.by_length.get(min_len, ()):
            need = Counter(word)
            if all(have[letter] >= count for letter, count in need.items()):
                return True
        for length in range(min_len + 1, min(MAX_WORD_LEN, len(tray)) + 1):
            for word in self.by_length.get(length, ()):
                need = Counter(word)
                if all(have[letter] >= count for letter, count in need.items()):
                    return True
        return False

    def anagrams(self, letters: str) -> list[str]:
        return list(self.by_signature.get(signature(letters), ()))


def signature(letters: str) -> tuple:
    return tuple(sorted(Counter(letters.lower()).items()))


@lru_cache(maxsize=1)
def load_dictionary(path: str | None = None, *, name: str = "default") -> Dictionary:
    source = Path(path) if path else dictionary_path(name)
    words = [line.strip().lower() for line in source.read_text(encoding="utf-8").splitlines()]
    words = [w for w in words if w and w.isalpha() and MIN_WORD_LEN <= len(w) <= MAX_WORD_LEN]
    return Dictionary(words)
