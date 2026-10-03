# Word Chef · art & content pipeline

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
| Dishes | `dish_<latin>.jpg` | 512 × 512 (≤ 60 KB) | 92 px (`.wc-dish-img`), 140 px (`.big-img`) |
| Guests | `guest_<kitchen>.jpg` | 512 × 512 (≤ 60 KB) | 52 px round (`.wc-guest`) |
| Kitchens | `kitchen_<id>.jpg` | 512 × 512 (≤ 60 KB) | 48 px (map) |
| UI icons | `ui_<name>.jpg` | 256 × 256 | 18–54 px inline |
| Medals | `ach_{gold,silver,bronze}.jpg` | 360 × 360 | 34–56 px |
| Banners | `hero.jpg` 820 × 410, `splash.jpg` 349 × 620, others 1:1–2:1 | — | CSS `background-size: cover` |

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
4. Drop the art into `frontend/public/img/`, then run the normaliser — it
   resizes to the shipped canvas, walks JPEG quality down to fit the byte
   budget and is safe to re-run:

   ```bash
   python3 scripts/normalize_art.py          # что будет сделано
   python3 scripts/normalize_art.py --write  # применить
   ```

   Never commit the raw 1024² generator output — see §5 for why.

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

Rules of thumb:

* never commit 1024² JPEGs — downloads are big and the UI shows them at ≤ 140 px;
* keep a single dish asset ≈ 30–55 KB;
* before adding a large batch, check with
  `python3 -c "from pathlib import Path;print(sum(p.stat().st_size for p in Path('frontend/out').rglob('*') if p.is_file())/1048576)"`.

`scripts/normalize_art.py` is the enforcement tool: budgets live in its
`RULES` table, and it will not go below `QUALITY_FLOOR = 70` to hit them — a
file that cannot fit its budget is reported instead of being mangled.

Recommended follow-up when headroom drops below ~200 KB: migrate `public/img`
to **WebP** (`quality ≈ 78`) and keep JPEGs only as fallback. That is worth
roughly 40 % of the image payload and is a mechanical, repo-wide change
(`normalize_art.py` already owns the encode step).

## 6 · Level fixtures & parity

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
