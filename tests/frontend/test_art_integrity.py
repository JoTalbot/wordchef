"""Art completeness & budget guards.

Four classes of mistake this catches (all four happened for real):

1. a dish added to `DISHES` but never wired into `DISH_ART` — the UI then
   silently falls back to `dish_salad.webp`;
2. a dish wired to a file that does not exist in `public/img`;
3. art committed oversized (1024² originals blew the 4 MB bundle budget);
4. a `.jpg` reference surviving the WebP migration.

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
GAMECLIENT = ROOT / "frontend" / "components" / "GameClient.tsx"
LEVELS_PY = ROOT / "game" / "wordchef_game" / "levels.py"
NOTES_TS = ROOT / "frontend" / "lib" / "dishNotes.ts"

sys.path.insert(0, str(ROOT / "scripts"))
from normalize_art import target_for  # noqa: E402

ART_GLOB = ("*.webp", "*.jpg", "*.png")
REF_RE = re.compile(r"img/([A-Za-z0-9_${}.\-]+\.(?:webp|jpg|png))")


def _dishes() -> list[str]:
    src = LEVELS_PY.read_text(encoding="utf-8")
    return [name for _, name in re.findall(r'\{"emoji": "(.+?)", "name": "(.+?)"\}', src)]


def _dish_art() -> dict[str, str]:
    src = CHEFGAME.read_text(encoding="utf-8")
    block = re.search(r"const DISH_ART[^{]*\{(.*?)\n\};", src, re.S).group(1)
    return dict(re.findall(r'"([^"]+)": "img/([^"]+)"', block))


def _dish_notes() -> dict[str, str]:
    src = NOTES_TS.read_text(encoding="utf-8")
    block = re.search(r"DISH_NOTES[^{]*\{(.*?)\n\};", src, re.S).group(1)
    return dict(re.findall(r'"([^"]+)": "([^"]+)"', block))


def _all_art_files() -> list[Path]:
    files: list[Path] = []
    for pattern in ART_GLOB:
        files += list(IMG.glob(pattern))
    return sorted(files)


# `img/kitchen_${id}.webp` is built at runtime — expand it over the real ids
# instead of treating it as a missing file.
KITCHEN_IDS = ("street", "bakery", "sushi", "space", "cyber", "ancient", "midnight")


def _expand(ref: str) -> set[str]:
    if "${id}" not in ref:
        return {ref}
    return {ref.replace("${id}", kid) for kid in KITCHEN_IDS}


def _code_files() -> list[Path]:
    """Every source file that may reference a shipped asset.

    Art is wired in three places: the campaign component, the multiplayer
    component, and `app/globals.css` (game-screen theming uses background
    images). Scanning only the components once cost us a false orphan report.
    """
    root = ROOT / "frontend"
    files: list[Path] = []
    for pattern in ("components/*.tsx", "components/*.ts", "app/*.css", "app/*.tsx", "lib/*.ts"):
        files += [p for p in (root / pattern).parent.glob(Path(pattern).name)
                  if "node_modules" not in p.parts and "out" not in p.parts]
    return sorted(set(files))


def _referenced_files() -> set[str]:
    """Every shipped name the source mentions.

    Matching on the file *name* rather than on an `img/…` regex keeps this
    test honest for paths built inline, e.g.
    `url(img/${cond ? 'mode_chaos' : 'mode_quick'}.webp)`.
    """
    texts = "".join(f.read_text(encoding="utf-8") for f in _code_files())
    used = set(_dish_art().values())
    for p in _all_art_files():
        # full name, or bare stem for paths assembled inline
        # (`… 'mode_chaos' : 'mode_quick'}.webp`), or a runtime template.
        if p.name in texts or p.stem in texts:
            used.add(p.name)
        elif p.name.startswith("kitchen_") and "img/kitchen_${id}." in texts:
            used.add(p.name)
    return used


class TestDishArt:
    def test_every_dish_has_art_wired(self):
        missing = [d for d in _dishes() if d not in _dish_art()]
        assert not missing, (
            f"у блюд нет арта в DISH_ART: {missing} — UI молча покажет fallback"
        )

    def test_every_wired_file_exists(self):
        missing = [f"{n} → {f}" for n, f in _dish_art().items() if not (IMG / f).is_file()]
        assert not missing, f"файлов нет в public/img: {missing}"

    def test_every_dish_has_a_recipe_note(self):
        notes = _dish_notes()
        missing = [d for d in _dishes() if d not in notes]
        assert not missing, f"у блюд нет записи в dishNotes.ts: {missing}"

    def test_no_orphan_art(self):
        """Art files nothing points at are dead weight in the bundle."""
        used = _referenced_files()
        orphans = [p.name for p in _all_art_files() if p.name not in used]
        assert not orphans, f"арт не подключён нигде: {orphans}"


class TestArtFormat:
    def test_no_jpeg_references_left_in_code(self):
        """The set ships as WebP — a stray .jpg ref is a broken image."""
        offenders = []
        for f in _code_files():
            for ref in re.findall(r"img/[^\"'`)\s]*\.jpe?g", f.read_text(encoding="utf-8")):
                offenders.append(f"{f.name}: {ref}")
        assert not offenders, f"остались .jpg-ссылки: {offenders}"

    def test_public_dir_has_no_jpegs(self):
        jpgs = [p.name for p in IMG.glob("*.jpg")]
        assert not jpgs, f"в public/img остались JPEG: {jpgs}"


class TestArtBudget:
    def test_no_asset_exceeds_its_budget_by_more_than_floor(self):
        """At quality floor a file may exceed its target — but never wildly."""
        over = []
        for p in _all_art_files():
            _, _, budget = target_for(p)
            limit = int(budget * 1.6)  # floor-quality tolerance
            if p.stat().st_size > limit:
                over.append(f"{p.name}: {p.stat().st_size // 1024}K > {limit // 1024}K")
        assert not over, (
            "арт превышает бюджет (запусти scripts/normalize_art.py --write): "
            + "; ".join(over)
        )

    def test_dish_canvas_is_mobile_sized(self):
        from PIL import Image

        oversized = []
        for pattern in ("dish_*.webp", "dish_*.jpg", "dish_*.png"):
            for p in IMG.glob(pattern):
                with Image.open(p) as im:
                    if max(im.size) > 512:
                        oversized.append(f"{p.name}: {im.size}")
        assert not oversized, f"блюда не нормализованы к 512²: {oversized}"


@pytest.mark.skipif(not (ROOT / "frontend" / "out").is_dir(),
                    reason="нужна сборка фронтенда (npm run build)")
class TestArtInBundle:
    def test_every_used_asset_ships_in_the_export(self):
        out_img = ROOT / "frontend" / "out" / "img"
        exported = {p.name for p in out_img.iterdir() if p.is_file()}
        missing = sorted(f for f in _referenced_files() if f not in exported)
        assert not missing, f"нет в статическом экспорте: {missing}"

    def test_css_background_images_resolve_in_export(self):
        """`url(/img/…)` in the built CSS must point at a shipped file."""
        css_dir = ROOT / "frontend" / "out" / "_next" / "static" / "css"
        if not css_dir.is_dir():
            pytest.skip("нужна сборка фронтенда")
        out_img = ROOT / "frontend" / "out" / "img"
        broken = []
        for css in css_dir.glob("*.css"):
            for ref in re.findall(r"url\(/img/([A-Za-z0-9_.\-]+)\)", css.read_text(encoding="utf-8")):
                if not (out_img / ref).is_file():
                    broken.append(f"{css.name} → {ref}")
        assert not broken, f"CSS ссылается на отсутствующий арт: {broken}"
