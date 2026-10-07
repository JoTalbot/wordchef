"""Load and validate the canonical WordChef content pack.

The pack is data, not executable rules. Its canonical JSON representation is
hashed so a replay can bind gameplay to the exact content that produced it.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

PACK_PATH = Path(__file__).with_name("content_pack.json")


class ContentError(ValueError):
    """Raised when a content pack violates the deterministic content contract."""


def _canonical_bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _read_pack() -> dict[str, Any]:
    return json.loads(PACK_PATH.read_text(encoding="utf-8"))


def validate_content(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ContentError("content pack must be an object")
    if data.get("schema_version") != 1:
        raise ContentError("unsupported content schema_version")
    if not isinstance(data.get("content_id"), str) or not data["content_id"]:
        raise ContentError("content_id is required")

    kitchens = data.get("kitchens")
    if not isinstance(kitchens, list) or not kitchens:
        raise ContentError("kitchens must be a non-empty list")
    kitchen_ids = [k.get("id") for k in kitchens]
    if any(not isinstance(kid, str) or not kid for kid in kitchen_ids):
        raise ContentError("every kitchen needs a non-empty id")
    if len(kitchen_ids) != len(set(kitchen_ids)):
        raise ContentError("kitchen ids must be unique")
    unlocks = [k.get("unlock_xp") for k in kitchens]
    if any(not isinstance(x, int) or x < 0 for x in unlocks):
        raise ContentError("unlock_xp must be non-negative integers")
    if unlocks != sorted(unlocks):
        raise ContentError("kitchens must be ordered by unlock_xp")

    themes = data.get("themes")
    if not isinstance(themes, dict) or not themes:
        raise ContentError("themes must be a non-empty object")
    for theme, words in themes.items():
        if not isinstance(theme, str) or not theme:
            raise ContentError("theme ids must be non-empty strings")
        if not isinstance(words, list) or not words:
            raise ContentError(f"theme {theme!r} must contain words")
        if any(not isinstance(word, str) or not word for word in words):
            raise ContentError(f"theme {theme!r} contains an invalid word")

    secrets = data.get("secrets")
    if not isinstance(secrets, list) or not secrets:
        raise ContentError("secrets must be a non-empty list")
    if len(secrets) != len(set(secrets)):
        raise ContentError("secrets must be unique")

    rules = data.get("order_rules")
    if not isinstance(rules, list) or not rules:
        raise ContentError("order_rules must be a non-empty list")
    if len(rules) != len(set(rules)):
        raise ContentError("order_rules must be unique")

    dishes = data.get("dishes")
    if not isinstance(dishes, list):
        raise ContentError("dishes must be a list")

    return data


@lru_cache(maxsize=1)
def load_content() -> dict[str, Any]:
    return validate_content(_read_pack())


@lru_cache(maxsize=1)
def content_digest() -> str:
    return hashlib.sha256(_canonical_bytes(load_content())).hexdigest()


CONTENT = load_content()
CONTENT_DIGEST = content_digest()
