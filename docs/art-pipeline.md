# Word Chef · art & content pipeline

> 🇷🇺 Русская версия: [`docs/ru/art-pipeline.md`](ru/art-pipeline.md)

Everything a future "add more dishes / kitchens / guests" commit needs to know.
Scope: the campaign UI (`frontend/components/ChefGame.tsx`), the level engine
(`game/wordchef_game/levels.py` + its TS mirror) and the asset set in
`frontend/public/img/`.

---

## 1 · Asset conventions

All art is the same "3D clay / CGI toy" style: chunky soft shapes, glossy
plasticine material, warm palette, **seamless cream-beige background**
(`#f6e7cd`-ish), soft studio light, gentle contact shadow, no text.

| Kind | Files | Canvas shipped | Display size |
|---|---|---|---|
| Dishes | `dish_<latin>.webp` | 512 × 512 (≤ 60 KB) | 92 px (`.wc-dish-img`), 140 px (`.big-img`) |
| Guests | `guest_<kitchen>.webp` | 512 × 512 (≤ 60 KB) | 52 px round (`.wc-guest`) |
| Kitchens | `kitchen_<id>.webp` | 512 × 512 (≤ 60 KB) | 48 px (map) |
| UI icons | `ui_<name>.webp` | 256 × 256 | 18–54 px inline |
| Medals | `ach_{gold,silver,bronze}.webp` | 256 × 256 | 34–56 px |
| Banners | `hero.webp` 820 × 410, `splash.webp` 349 × 620, others 1:1–2:1 | — | CSS `background-size: cover` |

**Формат — WebP.** JPEG в `public/img` не остаётся: генераторы отдают JPEG,
`scripts/to_webp.py` конвертирует и переписывает все ссылки, а
`tests/frontend/test_art_integrity.py::TestArtFormat` следит, чтобы `.jpg`
не вернулся ни в код, ни в каталог.

**Banners are a separate visual family.** Dishes and characters are
"plasticine"; banners (`mode_*`, `lobby_banner`, `results_banner`, `howto`,
`hero`, `splash`, `grandtour`, `grand_map`, `recipe_book`, `celebrate`) are
detailed 2D painterly cartoon: dark wood, brass lamps, steam, flying tickets,
warm amber palette. A new banner must join *that* family — prompt it as
`Vibrant 2D cartoon game banner: …` and end with
`wide banner composition 2:1, no text, no lettering, no watermark`.

Reference prompt skeleton (swap the subject line, keep the rest):

```
<SUBJECT>, served on a simple pale ceramic plate, top-down three-quarter view —
3D clay/CGI render in a mobile game icon style: chunky soft cartoon shapes,
glossy plasticine texture, warm cream-beige seamless background, soft studio
lighting, gentle drop shadow, centered composition, square 1:1, appetizing,
no text, no watermark, no hands
```

Icons (no plate) use the same tail with `<SUBJECT>, single object isolated on a
plain warm cream-beige background — …`.

## 2 · Wiring a new dish (4 files, in this order)

1. `game/wordchef_game/levels.py` → append `{"emoji": "…", "name": "…"}` to
   `DISHES`.
2. `frontend/lib/levels.ts` → append the **same entry at the same position** to
   its `DISHES`. Order is part of the contract between the two engines.
3. `frontend/components/ChefGame.tsx` → add `"<Название>": "img/dish_<latin>.jpg"`
   to `DISH_ART` (fallback is `dish_salad.jpg`).
4. Drop the art into `frontend/public/img/`, then run the tools — the
   normaliser resizes to the shipped canvas and walks quality down to fit the
   byte budget; the converter moves anything still in JPEG over to WebP:

   ```bash
   python3 scripts/to_webp.py                # если пришёл JPEG
   python3 scripts/to_webp.py --write
   python3 scripts/normalize_art.py          # что будет сделано
   python3 scripts/normalize_art.py --write  # применить
   ```

   Never commit the raw 1024² generator output — see §5 for why.

5. Add a one-line recipe note to `frontend/lib/dishNotes.ts`. The integrity
   test requires it: a dish without a note fails the suite.

Dish → level mapping is deterministic:

```python
dish = DISHES[(level_no // 10) % len(DISHES)]
```

Adding dishes therefore re-maps levels `10 · len(DISHES)` onward — a content
change, not a bug. Levels 1–9 (`Салат`) never move.

## 3 · Guests & flavour text

`GUEST_ART` (portrait + nick) and `GUEST_LINES` (3 lines per kitchen) live in
`ChefGame.tsx`. The line shown for a level is

```ts
lines[(max(1, levelNo) - 1) % lines.length]
```

i.e. deterministic per level, so a replay of the same level reads identically.
When adding a kitchen: add it to `KITCHENS` (`levels.py` **and** `levels.ts`),
to `KITCHEN_RU`, to `GUEST_ART`, to `GUEST_LINES`, and ship
`kitchen_<id>.jpg` + `guest_<id>.jpg`.

## 4 · Golden-ingredient tile

`ui_gold_tile.jpg` is the blank beveled golden square from the very first
`splash.jpg` render, cropped out as a tile. In the campaign UI it is applied as
an inline `background-image` to a wheel letter that is currently picked — see
the `wc-tile` render in `ChefGame.tsx`. Multiplayer keeps its own ⭐ economy
(`me.golden`); the tile is purely a visual cue and does not change scoring.

## 5 · Bundle budget (hard test)

`tests/frontend/test_smoke.py::TestFrontendBuild::test_bundle_stays_small`
fails if the whole static export exceeds **4 MB** — mobile-first, and the
Android build embeds `frontend/out` in the APK assets.

| Date | Bundle | Delta |
|---|---|---|
| before `0f3558f` | ~3.5 MB | — |
| after 22-dish art (`0f3558f`) | 4.2 MB (1024² art) | **over budget** |
| after 620² normalisation + 28 dishes + icons | 3.59 MB | 417 KB headroom |
| after 38 dishes + UI icons + RU build | 3.47 MB | 538 KB headroom |
| after 48 dishes + WebP migration | **2.64 MB** | 1393 KB headroom |
| after 54 dishes + refreshed banners | 2.69 MB | 1338 KB headroom |
| after game-screen theming (11 UI assets) | 2.93 MB | 1093 KB headroom |

Rules of thumb:

* never commit 1024² JPEGs — downloads are big and the UI shows them at ≤ 140 px;
* keep a single dish asset ≈ 30–55 KB;
* before adding a large batch, check with
  `python3 -c "from pathlib import Path;print(sum(p.stat().st_size for p in Path('frontend/out').rglob('*') if p.is_file())/1048576)"`.

`scripts/normalize_art.py` is the enforcement tool: budgets live in its
`RULES` table, and it will not go below `QUALITY_FLOOR = 70` to hit them — a
file that cannot fit its budget is reported instead of being mangled.

WebP migration is **done** (`scripts/to_webp.py`): image payload fell from
3.50 MB to 2.04 MB (−42 %) with no visible change at these display sizes. If
headroom ever gets tight again, the next levers are: 384² dishes (still above
the 140 px display size), and dropping the two 820-wide banners to 640.


## 6 · Game-screen theming

The core loop is themed with generated art, not gradients:

| UI slot | Asset | How |
|---|---|---|
| Campaign background | `ui_wood_table.webp` | wooden table under a warm wash (`html, body`) |
| Crossword board | `ui_board_surface.webp` | same cutting board as the dish art |
| Board cells | — | dark "sockets"; `hot` cells get a warm rim inside the socket |
| Wheel / letter tiles | `ui_tile_wood.webp` | wooden tiles; a picked letter flips to `ui_gold_tile.webp` |
| Rare letters (J/Q/X/Z) | `ui_gold_tile.webp` | golden ingredients are visible in the tray |
| Progress bars | `ui_progress_strip.webp` | ember-strip fill |
| Multiplayer HUD | `ui_hud_plaque.webp` | brass plaque on wood (light text) |
| Order card | `ui_ticket_paper.webp` | waiter's paper ticket |
| Primary button | `ui_btn_plate.webp` | wooden plate (trimmed tight) |
| Served dish | `ui_plate_round.webp` | empty plate under the dish |
| Game-screen backdrop | `bg_game_kitchen.webp` | night kitchen bokeh instead of a flat wash |

Tiles and plates use `background-blend-mode: multiply` so the light wood from
the generator lands on the game's warmer tone while text stays legible.

### Android trap: `url()` inside CSS

`url(/img/…)` in a stylesheet resolves against **the stylesheet**, not the
document — under `file://` in a WebView it points at the filesystem root.
`scripts/build_android.sh` rewrites such URLs to the right number of `../` for
the file's depth (three for `next/static/css/`). Verified on the assembled APK:
all nine CSS references resolve inside `assets/www`.

## 7 · Android build

```bash
TOOLS_DIR=~/.cache/wc-tools bash scripts/bootstrap_android_tools.sh   # JDK 17 + Gradle 8.7 + SDK 34
JAVA_HOME=… ANDROID_HOME=… GRADLE=…/gradle-8.7/bin/gradle bash scripts/build_android.sh
```

Verified end to end: `assembleRelease` produces a signed APK in ~50 s;
`assets/www` inside it carries 93 WebP files, zero JPEG, `_next` renamed to
`next`, all references relative (`./next/…`, `img/…`), signature scheme v2
valid (`apksigner verify`).

Why the rename matters: AAPT skips asset directories whose name starts with
`_`, so WebView would find neither JS nor images under `file://`. The build
script rewrites `_next` → `next` and makes URLs relative; if a future asset
directory starts with `_`, add it to that same rewrite step.

## 8 · Level fixtures & parity

```bash
python3 scripts/regen_levels_golden.py            # show churn (no writes)
python3 scripts/regen_levels_golden.py --write    # lock new golden output
python3 scripts/check_levels_parity.py            # compile TS mirror, diff vs Python
```

`check_levels_parity.py` compiles `frontend/lib/levels.ts` with the repo's own
TypeScript and compares it against the Python engine for every level in the
fixture, printing the first diverging field on failure. Use it — a silent
divergence between the two generators means the client and the server disagree
about the board, which is exactly the class of bug this game is built to make
impossible.
