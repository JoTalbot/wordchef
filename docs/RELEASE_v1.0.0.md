# Word Chef — Release Summary

## VERSION
`v1.0.0` · 2026-09-30 · first public release

## COMMIT
`7ac92e3cd543e64afea9bd309c479dc1d29e6606` → https://github.com/JoTalbot/wordchef
(tag `v1.0.0`)

## FEATURES
* Original word-cooking gameplay: orders → letter trays → dishes → score,
  with **Combo ×4**, **Heat**, risky **Spice ×2**, **Golden Ingredients**,
  **Mise en place** (prep), **Secret Menu**, **Flavor Profiles**, overcook
  timers and 7 progression **Kitchens** (Street → Bakery → Sushi → Space →
  Cyber → Ancient → Midnight Diner).
* Multiplayer: **Quick Cook** (2–8 chefs, identical tickets) and
  **Chaos Kitchen** (steal/burn/spice storm/tray swap/double order + the 🔔
  bell). Zero pay-to-win.
* Server-authoritative anti-cheat with deterministic match replay.
* AI-shaped dynamics kept honest: adaptive difficulty (heat/skill-driven),
  deterministic chaos director, themed NPC customers — no LLM in the scoring
  path (see KNOWN LIMITATIONS).
* Mobile-first Next.js UI (letter tap-to-cook, dish pop animation, live
  standings, chaos feed).

## ARCHITECTURE
`Next.js 14 (TS, static export)` ⇄ `FastAPI + WebSocket + SQLite` ⇄
`Prolepsis v0.39.0 (Agent Platform v1, JACQUARD v0.4 patterns)`.
Game core is a pure deterministic package (`game/wordchef_game`).
All 10 game operations run as Prolepsis executions (patterns in
`prolepsis/patterns/wordchef/*.yaml`); weaver handlers recompute every dish
claim; CAS artifacts + checkpoints + digests + audit events persist in
`data/`. ~6 000 lines of Python/TS/YAML.

## TEST RESULTS
| Gate | Result | Detail |
|---|---|---|
| BUILD | **PASS** | Next.js production build + TypeScript check |
| UNIT TESTS | **PASS** | 43/43 (RNG, dictionary, letters, flavors, kitchens, orders, scoring) |
| GAMEPLAY TESTS | **PASS** | combo/heat/spice/prep/golden/streak/secret/chaos/progression + full-match determinism |
| INTEGRATION TESTS | **PASS** | Prolepsis 15/15 · API 12/12 |
| MULTIPLAYER | **PASS** | 5/5 (Quick Cook parity, Chaos Kitchen, WebSocket feed) |
| ANTI-CHEAT | **PASS** | 11/11 + 2 tamper-detection tests |
| PERSISTENCE / RESTART / CHECKPOINT | **PASS** | 6/6 (store, service restart, runtime restart, checkpoints) |
| FRONTEND SMOKE | **PASS** | 6/6 (export, protocol markers, size, no CDN, viewport) |
| **pytest total** | **PASS** | **98/98** |

## PROLEPSIS EXECUTIONS
Acceptance battery `scripts/acceptance_prolepsis.py`: **29/29 PASS** —
health · ready · version · async execution (prepare+worker **and** HTTP 202
via stock `agent-serve`) · completion · artifacts (CAS readable) ·
checkpoint · replay · verify · restart · audit · idempotency.

Required record for an important game execution (dish service):

```
execution_id   : exec_af190daabc87d44552f616cc
digest         : sha256:ee233a35c3e489e7aa6bcfe9a97997b65d9c23507a542e0daaf4ce6b2735b4e3
artifact_refs  : warp_1 · dish_receipt · score_sheet   (sha256 CAS refs)
checkpoint_id  : chk_1_30c749aa
verified       : True
```

Ops on the beam: `match.create · round.start · order.generate ·
dish.submit(+score.award) · prep.service · bonus.apply · chaos.event ·
round.end · result.commit · leaderboard.update · leaderboard.commit ·
match.verify`.

## VERIFICATION RESULTS
* `same input → same execution → same canonical result → same digest` — proven
  (two runs, distinct execution ids, identical digests + CAS refs).
* Replay of sealed executions: `matches=true`, `document_matches=true`.
* Tampered runtime log ⇒ `verified=false`; tampered digest ⇒ `verified=false`.
* Full-match replay (`POST /api/matches/{id}/verify`) on the live smoke match:
  `verdict=verified`, `state_match=true`.
* Forged score/combo/boost claims ⇒ execution `FAILED (constraint_broken)` ⇒
  game state does not advance.

## KNOWN LIMITATIONS
1. Prolepsis v0.39.0 `IRError` is a dataclass (not raisable) and
   `ExecutionRecord` lacks `reason/message` — fixed by 2 documented
   micro-patches in the vendored copy (`prolepsis/vendor/PATCHES.md`);
   upstream could adopt them.
2. Multiplayer is single-process (per-match locks + SQLite). Multi-host needs
   the documented store swap (Postgres + shared CAS).
3. “AI” today = deterministic chaos director + adaptive difficulty + themed
   NPC orders. An LLM order-writer can be added behind the same validated
   seam (never in the scoring path) — intentionally deferred.
4. Browser E2E (real taps) is covered by build smoke + API/WS suites, not a
   headless-browser suite.
5. Docker images are provided but the registry publish step is left to CI.

## DEPLOYMENT STATUS
`docker compose up --build` (multi-stage image builds the frontend) or
manual `uvicorn app.main:app`. Data volume `data/` (SQLite + Prolepsis
executions + CAS) is restart-safe (tested). Live instance running on
`http://localhost:8000` — smoke 14/14 PASS against it.

## RELEASE STATUS
**RELEASE READY** — all mandatory gates PASS:

```
BUILD PASS · UNIT PASS · INTEGRATION PASS · GAMEPLAY PASS · MULTIPLAYER PASS
ANTI-CHEAT PASS · PROLEPSIS PASS · REPLAY PASS · VERIFY PASS · CHECKPOINT PASS
RESTART PASS · SMOKE PASS · DOCUMENTATION PASS
```
