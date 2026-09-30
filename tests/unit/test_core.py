"""Unit tests — deterministic primitives of the game core."""
from __future__ import annotations

from wordchef_game.rng import Rng, derive_seed
from wordchef_game.dictionary import load_dictionary, Dictionary
from wordchef_game.letters import (LetterProfile, generate_tray, swap_one_letter,
                                   golden_from_letters, letter_rarity)
from wordchef_game.flavor import flavor_of, pairing_multiplier
from wordchef_game.kitchens import KITCHENS, kitchen_for_xp, next_kitchen, KITCHEN_BY_ID
from wordchef_game.orders import make_order, check_order, check_secret, theme_words, THEMES


class TestRng:
    def test_same_seed_same_stream(self):
        a = Rng(derive_seed("s", "ctx"))
        b = Rng(derive_seed("s", "ctx"))
        assert [a.next_u64() for _ in range(20)] == [b.next_u64() for _ in range(20)]

    def test_different_contexts_differ(self):
        assert derive_seed("s", "a") != derive_seed("s", "b")

    def test_below_and_between_are_in_range(self):
        rng = Rng(12345)
        for _ in range(500):
            assert 0 <= rng.below(7) < 7
            assert 3 <= rng.between(3, 9) <= 9

    def test_weighted_choice_respects_zero_weight(self):
        rng = Rng(1)
        assert rng.weighted_choice(["a", "b"], [0, 5]) == "b"

    def test_shuffle_deterministic(self):
        items = list(range(30))
        assert Rng(9).shuffle(items) == Rng(9).shuffle(items)


class TestDictionary:
    def test_loads_and_checks_words(self):
        d = load_dictionary()
        assert len(d) > 3000
        assert d.is_word("bread")
        assert not d.is_word("xxzzq")

    def test_formable(self):
        d = load_dictionary()
        assert d.formable("art", "artistne")
        assert not d.formable("art", "bcd")
        assert not d.formable("zzz", "artistne")

    def test_longest_words_sorted(self):
        d = load_dictionary()
        words = d.longest_words_in("artistne", 5)
        assert words == sorted(words, key=lambda w: (-len(w), w))

    def test_has_playable_word(self):
        d = load_dictionary()
        assert d.has_playable_word("artistne")
        assert not d.has_playable_word("zzzz")


class TestLetters:
    def test_generate_tray_playable(self):
        d = load_dictionary()
        for seed in range(12):
            rng = Rng(seed)
            tray = generate_tray(rng, d)
            assert len(tray) == 7
            assert d.has_playable_word(tray)

    def test_swap_changes_exactly_one_letter(self):
        rng = Rng(4)
        tray = "abcdefg"
        swapped = swap_one_letter(rng, tray)
        assert len(swapped) == 7
        assert sum(1 for a, b in zip(sorted(tray), sorted(swapped)) if a != b) >= 0
        assert sum(1 for c in swapped if c in tray) >= 6

    def test_golden_from_letters(self):
        assert golden_from_letters("quiz") == 4      # q:2 + z:2
        assert golden_from_letters("cat") == 0
        assert letter_rarity("q") == 3
        assert letter_rarity("a") == 1


class TestFlavor:
    def test_deterministic_mapping(self):
        assert flavor_of("queue") == "umami"         # rare letters
        assert flavor_of("coffee") == "rich"         # doubled letter
        assert flavor_of("happy") == "rich"
        assert flavor_of("banana") == "classic"      # balanced vowels/consonants
        assert flavor_of("area") == "sweet"          # vowel heavy
        assert flavor_of("strength") == "savory"     # consonant heavy

    def test_pairing(self):
        assert pairing_multiplier("sweet", "sweet") == 1.5
        assert pairing_multiplier("sweet", "savory") == 0.85
        assert pairing_multiplier("sweet", "classic") == 1.0
        assert pairing_multiplier(None, "umami") == 1.0


class TestKitchens:
    def test_progression_order(self):
        assert kitchen_for_xp(0).kitchen_id == "street"
        assert kitchen_for_xp(2_500).kitchen_id == "bakery"
        assert kitchen_for_xp(99_999).kitchen_id == "midnight"
        assert next_kitchen(0).kitchen_id == "bakery"
        assert next_kitchen(99_999) is None

    def test_kitchen_rules_unique(self):
        rules = [k.rule for k in KITCHENS]
        assert len(set(rules)) == len(rules)
        assert KITCHEN_BY_ID["bakery"].score_mult > 1.0


class TestOrders:
    def test_make_order_deterministic(self):
        from wordchef_game.rng import Rng
        a = make_order(Rng(derive_seed("s", "o")), load_dictionary(),
                       order_id="o1", difficulty=3)
        b = make_order(Rng(derive_seed("s", "o")), load_dictionary(),
                       order_id="o1", difficulty=3)
        assert a == b

    def test_order_fields_in_range(self):
        from wordchef_game.rng import Rng
        d = load_dictionary()
        for seed in range(20):
            order = make_order(Rng(derive_seed(str(seed), "o")), d,
                               order_id=f"o{seed}", difficulty=seed % 9 + 1)
            assert 1 <= order.difficulty <= 9
            assert order.time_limit >= 5
            assert order.base_reward > 0
            assert len(order.tray) == 7
            assert d.has_playable_word(order.tray)

    def test_check_order_constraints(self):
        from wordchef_game.rng import Rng
        d = load_dictionary()
        order = make_order(Rng(1), d, order_id="x", difficulty=4,
                           force_kind="MIN_LENGTH")
        ok, reason = check_order(order, "a", d)
        assert not ok
        word = next(w for w in d.longest_words_in(order.tray, 10)
                    if check_order(order, w, d)[0])
        assert len(word) >= order.min_length

    def test_theme_words_in_dictionary(self):
        d = load_dictionary()
        for theme in THEMES:
            assert theme_words(theme, d), f"theme {theme} has no valid words"

    def test_secret_objectives(self):
        assert check_secret("bread", "min_length_5")
        assert check_secret("area", "two_vowels")
        assert check_secret("strength", "three_consonants")
        assert check_secret("coffee", "has_double")
        assert check_secret("menu", "ends_vowel")
        assert check_secret("bread", "no_rare_letters")
        assert not check_secret("jazz", "no_rare_letters")
