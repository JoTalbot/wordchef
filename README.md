# 🍳 WORD CHEF

**Cook words. Serve dishes. Ride the heat.**

Word Chef is an original multiplayer/web word-cooking game where players are
chefs who turn letters into dishes under pressure. Every submission is scored
by a deterministic, server-authoritative engine and **sealed as a Prolepsis
execution** — canonical digest, content-addressed artifacts, checkpoints,
replay and cryptographic verification. Cheating is not “discouraged”; it is
arithmetically impossible to make the loom weave a lie.

> Letters are ingredients. Words are dishes. Orders come from customers with
> cravings and secrets. Combo and Heat multiply everything — and so does the
> risk of the Spice.

---

## ✨ What makes it different

| Mechanic | What it does |
|---|---|
| **Flavor Profiles** | Every word deterministically maps to a flavor (sweet / savory / umami / tangy / rich / classic). Customers *crave* a flavor — matching it ×1.5, clashing ×0.85. |
| **Mise en place (prep)** | Bank 2–3 letter words as *prep*: small points, combo never breaks, and each prep token adds +10% to the next dish. |
| **Secret Menu** | ~25% of orders carry a hidden objective (“5+ letters”, “two vowels”…). Crack it silently for +50%. |
| **Spice** | Arm a ×2 multiplier — but the next flop burns your combo *and* your heat. |
| **Golden Ingredients** | Rare letters (J/Q/X/Z) mint golden stars: buy +15 s of patience, a tray restock, or a ×1.5 boost. |
| **Heat & Combo** | Streaks raise Heat (bigger rewards, harder tickets) and Combo (up to ×4). |
| **Kitchens** | 7 progression stages (Street → Bakery → Sushi Bar → Space → Cyber → Ancient → Midnight Diner), each rewriting the house rules: vowel markets, glitch letters, combo shields. |
| **Chaos Kitchen** | Multiplayer sabotage: steal ingredients, burn timers, spice storms, tray swaps, double orders — fully deterministic, never pay-to-win. |
| **Overcook timer** | Fast dishes are *al dente* (×1.2); late ones overcook (×0.9). The server clock is the only clock. |

Modes: **SOLO** · **QUICK COOK** (2–8 chefs, identical tickets) ·
**CHAOS KITCHEN** (different tickets + chaos events + the 🔔 bell).

---

## 🏗 Architecture

```
┌────────────────────┐   intents (WS/REST)   ┌───────────────────────────────┐
│  Next.js frontend  │ ────────────────────► │  FastAPI backend (authoritative)│
│  (TS, mobile-first)│ ◄──────────────────── │  · match orchestration          │
└────────────────────┘   events / views      │  · SQLite persistence           │
                                            │  · WebSocket live feed          │
                                            └──────────────┬────────────────┘
                                                           │ every meaningful op
                                                           ▼
                                            ┌───────────────────────────────┐
                                            │ Prolepsis Agent Platform v1   │
                                            │ (v0.39.0 · WEAVE / JACQUARD)  │
                                            │  · async executions           │
                                            │  · Jacquard patterns (YAML)   │
                                            │  · weaver handlers recompute  │
                                            │  · CAS artifacts · checkpoints│
                                            │  · replay · verify · audit    │
                                            └───────────────────────────────┘
```

* `game/wordchef_game` — pure deterministic game core (RNG, dictionary,
  orders, scoring, kitchens, chaos, engine). Zero IO. Same seed ⇒ same match.
* `prolepsis/wordchef_prolepsis` — the execution bridge: 10 game operations →
  Prolepsis executions, CAS read-back, verification, audit.
* `prolepsis/patterns/wordchef/` — **Jacquard v0.4 YAML patterns** (dish,
  order, round, match, bonus/chaos, leaderboard, verification).
* `prolepsis/vendor/` — Prolepsis v0.39.0 (pinned, 2 documented micro-patches,
  see `prolepsis/vendor/PATCHES.md`).
* `backend/` — FastAPI + SQLite + WebSocket.
* `frontend/` — Next.js 14 static export (TypeScript).
* `tests/` — 98 tests: unit · game-rule · API · prolepsis · determinism ·
  multiplayer · anti-cheat · persistence/restart · frontend smoke.

### Data flow of one dish

```
client intent {word}  →  engine.score_dish (pure)  →  Prolepsis execution
   →  Jacquard selvedges check invariants (score ≥ 0, combo ≥ 0 …)
   →  weaver RECOMPUTES the dish and compares every claimed field
   →  refused? → execution FAILED → game state does not advance
   →  ok? → DishReceipt + ScoreSheet woven into CAS → digest → checkpoint
   →  state persisted → WebSocket broadcast
```

---

## 🚀 Run locally

```bash
# 1 · backend deps
pip install fastapi "uvicorn[standard]" pydantic

# 2 · frontend (already built in this repo; rebuild after UI changes)
cd frontend && npm install && npm run build && cd ..

# 3 · start the kitchen (serves API + game on one port)
PYTHONPATH=backend:game:prolepsis:prolepsis/vendor \
  python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# open http://localhost:8000
```

Or with Docker: `docker compose up --build` → http://localhost:8000

### Tests & acceptance

```bash
./scripts/run_tests.sh            # full battery (98 tests + acceptance + build)
python3 scripts/acceptance_prolepsis.py   # Prolepsis acceptance only
python3 scripts/smoke_test.py http://localhost:8000   # against a live server
```

---

## 🔌 Prolepsis integration

Prolepsis v0.39.0 (Agent Platform v1) is the **execution/runtime layer** —
not a decoration:

| Game operation | Jacquard pattern | Event(s) | Artifact(s) |
|---|---|---|---|
| 1 · match creation | `match.yaml` | `MATCH_CREATE` | `MatchCloth` |
| 2 · round start | `round.yaml` | `ROUND_START` | `RoundTicket` |
| 3 · order generation | `order.yaml` | `ORDER_GENERATE` | `OrderTicket` |
| 4 · player answer | `dish.yaml` | `DISH_SUBMIT` | `DishReceipt` |
| 5 · score award | `dish.yaml` | `SCORE_AWARD` | `ScoreSheet` |
| 6 · bonuses / chaos | `bonus.yaml` | `BONUS_APPLY`, `CHAOS_EVENT` | `BonusCoupon`, `ChaosNotice` |
| 7 · round end | `round.yaml` | `ROUND_END` | `RoundSummary` |
| 8 · result commit | `match.yaml` | `MATCH_FINISH` | `MatchSignOff` |
| 9 · leaderboard | `leaderboard.yaml` | `LEADERBOARD_UPDATE/COMMIT` | `LeaderboardSheet`, `LeaderboardSeal` |
| 10 · replay/verify | `verify.yaml` | `MATCH_VERIFIED` | `VerificationReport` |

(4+5 are one atomic cloth: a dish and its score never separate.)

Runtime guarantees exercised by `scripts/acceptance_prolepsis.py` (29 checks):

* `health` · `ready` · `version` (in-process **and** over HTTP `agent-serve`)
* async executions (`prepare` → worker → `COMPLETED`, HTTP 202 + `execution_id`)
* artifacts (CAS refs + readable payloads) · checkpoints · replay · verify
  (`recorded digest == recomputed digest`), restart survival, audit events,
  request-id idempotency.

Every important execution yields: `execution_id · digest · artifact_refs ·
checkpoint_id · verified=true`.

---

## 🌐 API (summary)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/players` | register a chef |
| GET | `/api/players/{id}/progression` | XP, kitchen, next unlock |
| GET | `/api/info` | modes, kitchens, themes |
| POST | `/api/matches` | create match (SOLO/QUICK_COOK/CHAOS_KITCHEN) |
| POST | `/api/matches/{id}/start` | begin round 1 |
| GET | `/api/matches/{id}` | match state + leaderboard |
| GET | `/api/matches/{id}/players/{pid}` | player view (order, tray, timers) |
| POST | `/api/matches/{id}/intent` | **the only way to act** (SUBMIT_DISH, PREP, GOLDEN, RING_BELL, SKIP) |
| POST | `/api/matches/{id}/verify` | deterministic replay + verdict |
| GET | `/api/leaderboard` | season standings |
| GET | `/api/audit/executions[?match_id=]` | execution trail (digests, checkpoints) |
| GET | `/api/audit/executions/{id}` | envelope + CAS artifacts |
| GET | `/api/audit/events` | audit events (capability, lifecycle) |
| GET | `/api/prolepsis/health\|ready\|version` | platform status |
| WS | `/api/ws/matches/{id}` | live event feed (SYNC + EVENT frames) |

Full reference: [`docs/api.md`](docs/api.md).

---

## 🎲 Game rules (short)

* A **round** closes when every chef has served 3 dishes (or skipped along).
* A **dish** must be a real dictionary word spelled from the *server-dealt*
  tray and must satisfy the current **order** (length, letter, theme, streak,
  speed).
* Score = `quality(word) × combo × heat × spice × kitchen × flavor × speed ×
  prep × secret` — computed once, server-side, in
  `game/wordchef_game/scoring.py`, and **recomputed inside Prolepsis**.
* Failures reset Combo (Ancient Kitchen shields 1 level) and cool Heat
  (Midnight Diner never cools).
* Full rules: [`docs/game-rules.md`](docs/game-rules.md).

---

## 🧪 Testing

```
tests/
├── unit/            # RNG, dictionary, letters, flavors, kitchens, orders, scoring
│                    # + full game-rule suite (combo/heat/spice/prep/golden/chaos)
├── prolepsis/       # patterns compile, 10 ops, artifacts, checkpoints,
│                    # idempotency, capabilities, async, replay, verify,
│                    # tamper detection (same input → same digest)
├── anti_cheat/      # forged scores/combo/boosts refused by the loom;
│                    # server clock, no state injection, prep caps
├── api/             # REST surface, lifecycle, audit, platform endpoints
├── multiplayer/     # Quick Cook parity, Chaos Kitchen, WebSocket feed
├── persistence/     # store round-trips, service restart, runtime restart
└── frontend/        # build smoke: export, protocol markers, size, no CDN
```

Critical determinism check (green):

```
same input → same execution → same canonical result → same digest
```

---

## 🔒 Security & anti-cheat

* **Server-authoritative.** Clients send `intent + word`. Score, combo, heat,
  timers, rewards, inventory and leaderboard positions are computed
  server-side. Client-supplied `score`/`combo`/`golden` fields are ignored.
* **The loom refuses lies.** Dish claims are *recomputed* by the weaver
  handler; any mismatch ⇒ `constraint_broken` ⇒ execution `FAILED` ⇒ the game
  state does not advance. Test-proofed in `tests/anti_cheat/`.
* **Selvedges** (Jacquard guards) enforce structural invariants
  (`score ≥ 0`, `combo ≥ 0`, `position ≥ 1`, `valid ∈ {0,1}` …) before any
  artifact is woven.
* **Canonical digests** bind (input → result); replay/verify detect any
  envelope tampering (`tests/prolepsis/test_integration.py::…tampered…`).
* **Idempotent request ids** — retries never double-apply.
* **Capability policy** — the game engine holds only `weave.artifact`;
  everything else fails closed.
* **Secrets**: no secrets in IR or artifacts; matchmaking is by match id;
  the API never trusts a client clock. Deploy behind TLS (see
  [`docs/deployment.md`](docs/deployment.md)).

---

## 📦 Deployment

* `docker compose up --build` — backend (API + static frontend) on `:8000`.
* State lives in `data/` (SQLite + Prolepsis executions + CAS) — mount a
  volume; the service survives restarts (tested).
* Optional: run `python3 -m prolepsis._cli agent-serve --port 7397` as a
  standalone platform endpoint for ops/audit (health/ready/version/SSE).
* Details: [`docs/deployment.md`](docs/deployment.md).

---

## 📚 Documentation

* [`docs/architecture.md`](docs/architecture.md) — components & data flow
* [`docs/game-rules.md`](docs/game-rules.md) — full rules & mechanics
* [`docs/api.md`](docs/api.md) — REST/WS reference
* [`docs/prolepsis-integration.md`](docs/prolepsis-integration.md) — patterns,
  weavers, acceptance
* [`docs/anti-cheat.md`](docs/anti-cheat.md) — threat model & enforcement
* [`docs/testing.md`](docs/testing.md) — test map & how to run
* [`docs/deployment.md`](docs/deployment.md) — production notes

---

## 📄 License

Game code: MIT (see `LICENSE`). Vendored Prolepsis v0.39.0: MIT,
© JoTalbot — see `prolepsis/vendor/PROLEPSIS-LICENSE` and
`prolepsis/vendor/PATCHES.md` for the two documented micro-fixes.
