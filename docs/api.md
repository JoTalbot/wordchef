# API reference

Base URL: same origin as the game (default `http://localhost:8000`).
All bodies/responses are JSON. Errors: `{"detail": {"code", "message"}}`.

## Players

### `POST /api/players`
`{"name": "Alice"}` → `{"player_id": "p_…", "name": "Alice", "xp": 0, …}`

### `GET /api/players/{player_id}/progression`
→ `{"xp", "kitchen", "kitchen_name", "next_kitchen": {name, unlock_xp, remaining}|null}`

## Game info

### `GET /api/info`
Modes, kitchens, order kinds, themes.

### `GET /api/dictionary/check?word=bread`
→ `{"word": "bread", "is_word": true, "length": 5}`

## Matches

### `POST /api/matches`
```json
{"mode": "QUICK_COOK", "player_ids": ["p_…", "p_…"], "rounds": 3,
 "kitchen_id": "street", "seed": "optional-loom-seed"}
```
→ match view. Modes: `SOLO` | `QUICK_COOK` | `CHAOS_KITCHEN` (1–8 players,
1–12 rounds).

### `POST /api/matches/{match_id}/start`
Opens round 1 (deals orders, starts timers, seals `ROUND_START` + order
tickets on the beam).

### `GET /api/matches/{match_id}`
Match state + leaderboard + recent chaos.

### `GET /api/matches/{match_id}/players/{player_id}`
Chef view: score, combo, heat, spice, golden, prep, current order (tray,
constraints, deadline, streak), round counters.

### `POST /api/matches/{match_id}/intent`
**The only way to act.** Body: `{"player_id", "action", …}`:

| action | extra fields | effect |
|---|---|---|
| `SUBMIT_DISH` | `word`, `use_spice?` | serve a dish for the current order |
| `PREP` | `word` (2–3 letters) | bank prep points/tokens |
| `GOLDEN` | `effect`: `patience`/`restock`/`boost` | spend golden stars |
| `RING_BELL` | — | chaos event (Chaos Kitchen, costs 1⭐) |
| `SKIP` | — | fresh ticket, combo resets |

→ `{"accepted", "action", "reason", "player_id", "payload": {player,
leaderboard, round_complete, dish?, score_delta?, next_order?, chaos?,
execution: {execution_id, digest, verified, checkpoint_id}}}`.

`reason` values include `dish_served`, `streak_progress`, `prepped`,
`patience`, `restocked`, `boost_armed`, `skipped`, `not_in_dictionary`,
`not_formable_from_tray`, `word_already_served`, `order_expired`,
`needs_at_least_N_letters`, `needs_letter_x`, `not_on_the_…_menu`,
`not_enough_golden`, `unknown_action`.

### `POST /api/matches/{match_id}/verify`
Deterministic replay of `(seed + intent log)`:
→ `{"verdict": "verified"|"diverged", "state_match", "outcome_match",
"verify_execution": {…}}`

## Leaderboard & audit

* `GET /api/leaderboard?limit=50` — season standings (server-computed).
* `GET /api/audit/executions?match_id=…` — execution index: `execution_id`,
  `op`, `request_id`, `state`, `digest`, `checkpoint_id`, `verified`,
  `artifact_refs`.
* `GET /api/audit/executions/{execution_id}` — full envelope + CAS payloads.
* `GET /api/audit/events?execution_id=…` — audit events (`execution.*`,
  `capability.*`, `checkpoint.created`).

## Prolepsis platform

* `GET /api/prolepsis/health` · `GET /api/prolepsis/ready` ·
  `GET /api/prolepsis/version`

## WebSocket

`WS /api/ws/matches/{match_id}`

Server frames:
* `{"type":"SYNC","events":[…]}` — backlog on connect;
* `{"type":"EVENT","event":{…}}` — live events (`MATCH_CREATED`,
  `ROUND_STARTED`, `INTENT`, `ROUND_ENDED`, `MATCH_FINISHED`, …);
* `{"type":"PING"}` — keepalive every 20 s.
