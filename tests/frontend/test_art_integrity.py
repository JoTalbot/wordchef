"""Art completeness & budget guards.

Three classes of mistake this catches (all three happened for real):

1. a dish added to `DISHES` but never wired into `DISH_ART` — the UI then
   silently falls back to `dish_salad.jpg`;
2. a dish wired to a file that does not exist in `public/img`;
3. art committed oversized (1024² originals blew the 4 MB bundle budget).

Budgets come from `scripts/normalize_art.py`, so the numbers live in one place.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / "frontend" / "public" / "img"
CHEFGAME = ROOT / "frontend" / "components" / "ChefGame.tsx"
LEVELS_PY = ROOT / "game" / "wordchef_game" / "levels.py"

sys.path.insert(0, str(ROOT / "scripts"))
from normalize_art import target_for  # noqa: E402


def _dishes() -> list[str]:
    src = LEVELS_PY.read_text(encoding="utf-8")
    return [name for _, name in re.findall(r'\{"emoji": "(.+?)", "name": "(.+?)"\}', src)]


def _dish_art() -> dict[str, str]:
    src = CHEFGAME.read_text(encoding="utf-8")
    block = re.search(r"const DISH_ART[^{]*\{(.*?)\n\};", src, re.S).group(1)
    return dict(re.findall(r'"([^"]+)": "img/([^"]+)"', block))


class TestDishArt:
    def test_every_dish_has_art_wired(self):
        missing = [d for d in _dishes() if d not in _dish_art()]
        assert not missing, (
            f"у блюд нет арта в DISH_ART: {missing} — UI молча покажет dish_salad.jpg"
        )

    def test_every_wired_file_exists(self):
        missing = [f"{n} → {f}" for n, f in _dish_art().items() if not (IMG / f).is_file()]
        assert not missing, f"файлов нет в public/img: {missing}"

    def test_no_orphan_art(self):
        """Art files nothing points at are dead weight in the bundle."""
        used = set(_dish_art().values())
        src = (CHEFGAME.parent / "GameClient.tsx").read_text(encoding="utf-8")
        used |= set(re.findall(r"img/([a-z_0-9]+\.jpg)", src))
        orphans = [
            p.name for p in IMG.glob("dish_*.jpg")
            if p.name not in used and p.suffix == ".jpg"
        ]
        assert not orphans, f"арт не подключён нигде: {orphans}"


class TestArtBudget:
    def test_no_asset_exceeds_its_budget_by_more_than_floor(self):
        """At quality floor a file may exceed its target — but never wildly."""
        over = []
        for p in sorted(IMG.glob("*.jpg")):
            _, _, budget = target_for(p)
            limit = int(budget * 1.6)  # floor-q tolerance
            if p.stat().st_size > limit:
                over.append(
                    f"{p.name}: {p.stat().st_size // 1024}K > {limit // 1024}K")
        assert not over, (
            "арт превышает бюджет (запусти scripts/normalize_art.py --write): "
            + "; ".join(over)
        )

    def test_dish_canvas_is_mobile_sized(self):
        from PIL import Image

        oversized = []
        for p in sorted(IMG.glob("dish_*.jpg")):
            with Image.open(p) as im:
                if max(im.size) > 512:
                    oversized.append(f"{p.name}: {im.size}")
        assert not oversized, f"блюда не нормализованы к 512²: {oversized}"


@pytest.mark.skipif(not (ROOT / "frontend" / "out").is_dir(),
                    reason="нужна сборка фронтенда (npm run build)")
class TestArtInBundle:
    def test_every_used_asset_ships_in_the_export(self):
        out_img = ROOT / "frontend" / "out" / "img"
        exported = {p.name for p in out_img.glob("*.jpg")}
        used = set(_dish_art().values())
        src = (CHEFGAME.parent / "GameClient.tsx").read_text(encoding="utf-8")
        used |= set(re.findall(r"img/([a-z_0-9]+\.jpg)", src))
        missing = sorted(f for f in used if f not in exported)
        assert not missing, f"нет в статическом экспорте: {missing}"
