# Architecture

> 🇷🇺 Русская версия: [`docs/ru/architecture.md`](ru/architecture.md)

## Components

| Layer | Path | Responsibility |
|---|---|---|
| Game core | `game/wordchef_game/` | Pure deterministic rules: RNG (SplitMix64), dictionary, trays, orders, scoring, kitchens, chaos, match state machine. No IO, no clock reads except injected `now`. |
| Execution bridge | `prolepsis/wordchef_prolepsis/` | `WordChefRuntime`: Agent Gateway + weaver handlers + CAS + ops mapping. Every meaningful game op becomes an execution. |
| Jacquard patterns | `prolepsis/patterns/wordchef/` | Declarative workflows (dish, order, round, match, bonus, leaderboard, verify) with selvedges (guards) and weft templates (receipts). |
| Backend | `backend/app/` | FastAPI: REST + WebSocket + static frontend. Match orchestration, SQLite persistence, event bus. |
| Frontend | `frontend/` | Next.js 14 static export, TypeScript, mobile-first. Sends intents, renders state. |

## Determinism contract

```
match = f(seed, sequence of intents, timestamps supplied by the SERVER clock)
```

* RNG streams are derived: `sha256(master_seed ‖ context)` → SplitMix64.
* Scoring is one pure function (`scoring.score_dish`).
* A finished match replays byte-exactly from `(seed, intent log)` —
  `POST /api/matches/{id}/verify` re-runs it and compares.

## Execution flow (dish example)

1. `POST /api/matches/{id}/intent {action: SUBMIT_DISH, word}`
2. `engine.apply_intent` — validates against dictionary + tray + order,
   computes `DishResult` (pure).
3. `ops.op_submit_dish` builds `DISH_SUBMIT` + `SCORE_AWARD` facts (claims).
4. `WordChefRuntime.execute_op` → `AgentGateway.execute` (Prolepsis):
   * selvedges check invariants (`score_delta ≥ 0`, `valid ≤ 1` …);
   * `wordchef` weaver **recomputes** the dish from the claimed inputs and
     compares every field — mismatch ⇒ `IRError(constraint_broken)` ⇒ node
     `failed` ⇒ execution `FAILED`;
   * on success the weft (receipt) is rendered and committed to the CAS;
   * canonical digest + artifact refs + checkpoint are recorded.
5. If the execution is not `COMPLETED`, the service raises — **the game state
   does not advance** (the intent remains in the log for audit).
6. State snapshot persisted to SQLite; event broadcast on the WS feed.

## Persistence

| Store | Contents | Location |
|---|---|---|
| SQLite | players, matches + state snapshots, intent log, execution index, leaderboard | `data/wordchef.db` |
| Prolepsis JSON store | execution envelopes (events, digests, checkpoints, runtime log) | `data/prolepsis/executions/` |
| Persistent CAS | artifact payloads (content-addressed, `sha256:…`) | `data/prolepsis/cas/` |

All three survive restart; `tests/persistence/` proves a blackout mid-match
loses nothing and play continues.

## Concurrency

Per-match `RLock` serializes intent processing; Prolepsis async executions use
a worker pool (`prepare`/`execute_queued`); WebSocket feeds are poll-free
(asyncio queues with keepalive pings).
