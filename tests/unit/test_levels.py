"""Campaign level engine — determinism, golden parity with the TS mirror."""
from __future__ import annotations

import json
from pathlib import Path

from wordchef_game.levels import (
    Lcg, classify_word, generate_level, load_words_ru,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures_levels_golden.json"


class TestLevelEngine:
    def test_word_list_quality(self):
        words = load_words_ru()
        assert len(words) >= 1200
        assert all(3 <= len(w) <= 8 for w in words)
        assert all(all("а" <= c <= "я" for c in w) for w in words)

    def test_lcg_deterministic(self):
        a = Lcg(42)
        b = Lcg(42)
        assert [a.next() for _ in range(10)] == [b.next() for _ in range(10)]

    def test_same_level_same_result(self):
        for n in (1, 17, 99):
            assert generate_level(n).to_dict() == generate_level(n).to_dict()

    def test_board_words_formable_from_pool(self):
        from wordchef_game.levels import _subset
        for n in range(1, 60):
            lv = generate_level(n)
            assert all(_subset(b.word, lv.pool) for b in lv.board)
            assert len(lv.wheel) == len(lv.pool)
            assert sorted(lv.wheel) == sorted(lv.pool)

    def test_classify(self):
        lv = generate_level(2)
        w = lv.board[0].word
        assert classify_word(lv, w) == "board"
        assert classify_word(lv, w.upper()) == "board"
        if lv.bonus:
            assert classify_word(lv, lv.bonus[0]) == "bonus"
        assert classify_word(lv, "яяяяя") == "invalid"

    def test_golden_parity_with_ts_mirror(self):
        """The TypeScript generator must produce byte-identical levels."""
        golden = json.loads(FIXTURES.read_text(encoding="utf-8"))
        for key, expected in golden.items():
            got = generate_level(int(key)).to_dict()
            assert got == expected, f"level {key} diverges from the TS mirror"


class TestLevelFlow:
    def test_generate_levels_are_playable(self):
        for n in range(1, 40):
            lv = generate_level(n)
            assert len(lv.board) >= 2, f"level {n} too small"
            assert len({b.word for b in lv.board}) == len(lv.board)
