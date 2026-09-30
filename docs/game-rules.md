# Game rules

## The loop

```
ORDER → TRAY (7 letters) → build a DISH (word) → serve → score → next order
```

A **round** closes when every chef has served `ROUND_DISHES = 3` dishes
(skipping an order counts as progress but resets combo). A match is
`rounds_total` rounds (default 3).

## Orders (customer tickets)

| Kind | Requirement |
|---|---|
| `SINGLE_WORD` | any valid dish |
| `MIN_LENGTH` | ≥ N letters (N grows with difficulty) |
| `CONTAINS_LETTER` | must contain a specific letter |
| `THEME` | must be on the themed menu (street food, bakery, sushi, space, cyber, ancient, night market) |
| `STREAK` | 2–3 distinct dishes in a row inside one ticket |
| `SPEED` | harsh timer |

Every order is **solvable by construction**: the tray is built around an
anchor word that already satisfies the constraint.

## Scoring

```
score = (base + rarity) × combo × heat × spice × kitchen × flavor × speed × prep × secret
```

* `base = len² × 10` (+ kitchen bonuses for vowels/short/rare words)
* `rarity` = Σ letter rarity (J/Q/X/Z = 3, K/V/W/B = 2, else 1) × 4
* **combo** ×(1 + 0.25·combo), capped ×4
* **heat** ×(1 + 0.08·heat)
* **spice** ×2 when armed (charges earned every 3 combo)
* **flavor** ×1.5 match / ×1.0 neutral / ×0.85 clash (see Flavor Profiles)
* **speed** ×1.2 al dente (first 50% of the timer) / ×0.9 overcooked (last 25%)
* **prep** ×(1 + 0.1·prep tokens), max 3 tokens
* **secret** ×1.5 when the hidden objective lands

Failures: combo resets (Ancient Kitchen: −1 instead), heat −2 (spice burn: −3,
combo to 0). Midnight Diner never loses heat.

## Flavor Profiles (original mechanic)

`flavor_of(word)`: rare letters → **umami**; doubled letter → **rich**;
ends in y/o → **tangy**; vowel-heavy → **sweet**; consonant-heavy → **savory**;
else **classic**. Customers crave one flavor per ticket.

## Mise en place (original mechanic)

Submit a 2–3 letter word as **PREP**: +2 pts/letter, combo untouched, one prep
token banked (max 3) boosting the next dish.

## Secret Menu (original mechanic)

~25% of orders hide an objective: `min_length_5`, `two_vowels`,
`three_consonants`, `has_double`, `ends_vowel`, `no_rare_letters`. Succeed →
+50% and the secret is revealed.

## Golden Ingredients

Rare/uncommon letters in successful dishes mint golden stars. Spend:
`patience` (1⭐ → +15 s), `restock` (3⭐ → swap one ingredient),
`boost` (2⭐ → ×1.5 on the next dish). In Chaos Kitchen 1⭐ rings the 🔔 bell
(chaos event aimed at the leader).

## Kitchens (progression)

| Kitchen | Unlock XP | House rule |
|---|---|---|
| Street Kitchen | 0 | honest letters |
| Bakery | 2 000 | vowel market; sweet words thrive |
| Sushi Bar | 6 000 | short dishes score more |
| Space Kitchen | 14 000 | the void adds wild letters |
| Cyber Kitchen | 26 000 | glitch letters (rare ×heavy) |
| Ancient Kitchen | 44 000 | combo shield on failures |
| Midnight Diner | 70 000 | heat never cools |

## Multiplayer

* **QUICK COOK** — 2–8 chefs, identical tickets and trays; most points wins.
* **CHAOS KITCHEN** — different tickets; every round after the first opens with
  a deterministic chaos event: `STEAL_INGREDIENT`, `BURN_TIMER`, `SPICE_STORM`,
  `INGREDIENT_SWAP`, `DOUBLE_ORDER`. The 🔔 bell forces one immediately.

No pay-to-win: no purchasable power of any kind.

## Cheat sheet

* skip an impossible-feeling ticket (`SKIP`) — combo resets, fresh ticket.
* keep the combo, bank prep, then a big spicy dish at high heat.
* watch the flavor craving — it's worth ×1.5.
