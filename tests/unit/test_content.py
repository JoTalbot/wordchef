from __future__ import annotations

import json

from wordchef_game.content import CONTENT, CONTENT_DIGEST, dictionary_path, dishes_for_theme, validate_content
from wordchef_game.dictionary import load_dictionary
from wordchef_game.kitchens import KITCHENS
from wordchef_game.orders import KINDS, SECRETS, THEMES


def test_content_pack_is_valid_and_versioned() -> None:
    assert CONTENT["schema_version"] == 1
    assert CONTENT["content_id"] == "wordchef-core"
    assert validate_content(CONTENT) is CONTENT


def test_content_digest_is_canonical_and_stable() -> None:
    first = CONTENT_DIGEST
    shuffled = json.loads(json.dumps(CONTENT, ensure_ascii=False))
    shuffled["kitchens"] = list(reversed(shuffled["kitchens"]))
    # Kitchen order is semantically meaningful for progression, so the digest
    # must change when that order changes.
    assert CONTENT_DIGEST == first
    assert __import__("hashlib").sha256(
        json.dumps(
            shuffled, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest() != first


def test_runtime_uses_pack_for_kitchens_and_orders() -> None:
    assert tuple(k.kitchen_id for k in KITCHENS) == tuple(
        item["id"] for item in CONTENT["kitchens"]
    )
    assert KINDS == tuple(CONTENT["order_rules"])
    assert SECRETS == tuple(CONTENT["secrets"])
    assert THEMES == {
        theme: tuple(words) for theme, words in CONTENT["themes"].items()
    }


def test_content_rejects_duplicate_kitchen_ids() -> None:
    broken = json.loads(json.dumps(CONTENT, ensure_ascii=False))
    broken["kitchens"].append(dict(broken["kitchens"][0]))
    try:
        validate_content(broken)
    except ValueError as exc:
        assert "kitchen ids must be unique" in str(exc)
    else:
        raise AssertionError("duplicate kitchen id was accepted")


def test_content_dictionaries_and_dishes_are_real_runtime_data() -> None:
    assert dictionary_path("default").name == "dictionary.txt"
    assert dictionary_path("ru").name == "words_ru.txt"
    assert len(load_dictionary()) > 1000
    assert len(load_dictionary(name="ru")) > 1000
    assert len(CONTENT["dishes"]) == 7
    assert len({dish["id"] for dish in CONTENT["dishes"]}) == 7
    for dish in CONTENT["dishes"]:
        assert dish["word"] in load_dictionary()
        assert dish in dishes_for_theme(dish["theme"])


def test_content_rejects_duplicate_dish_ids() -> None:
    broken = json.loads(json.dumps(CONTENT, ensure_ascii=False))
    broken["dishes"].append(dict(broken["dishes"][0]))
    try:
        validate_content(broken)
    except ValueError as exc:
        assert "dish ids must be unique" in str(exc)
    else:
        raise AssertionError("duplicate dish id was accepted")
