# Anti-cheat

## Threat model

An attacker controls **everything the client sends**: HTTP bodies, WebSocket
frames, timestamps, and their own device clock. Goals they might pursue:
inflate score, fake combo/heat, mint golden stars, freeze/extend timers,
inject leaderboard rows, replay another player's dish as their own, or forge
a finished match result.

## Principles

1. **The server is authoritative.** The client sends `intent + word`. Score,
   combo, heat, spice, golden, prep, timers, rewards, inventory and
   leaderboard positions are computed server-side from the match seed and the
   server clock. Client-supplied `score`/`combo`/… fields are ignored
   (test: `test_extra_intent_fields_ignored`).
2. **Claims are recomputed, not trusted.** Each dish becomes a Prolepsis
   execution whose facts include the claimed result. The weaver handler
   recomputes the dish with the same pure scorer and compares *every* field;
   also `score_delta` must equal `computed × boost × double` exactly.
   Mismatch ⇒ `constraint_broken` ⇒ execution `FAILED` ⇒ **state does not
   advance** (the service raises `verification_failed`).
3. **Structural invariants fail closed at the reed.** Jacquard selvedges:
   `score_delta ≥ 0`, `combo_after ≥ 0`, `heat_after ≥ 0`, `golden_delta ≥ 0`,
   `valid ≤ 1`, `word_len ≥ 2`, `position ≥ 1`, `entries ≥ 1`…
4. **Digests bind input → result.** `sha256` canonical digests cover the
   canonical state derived from the event log; `verify` requires
   `recorded == recomputed`. Tampering with the persisted envelope (runtime
   log or digest) flips verification to `false` (two dedicated tests).
5. **Idempotency.** Request ids are deterministic
   (`dish.submit:{match}:{player}:{seq}`); the Agent Gateway dedupes, so a
   network retry can never double-apply a score.
6. **Server clock only.** Timers use the server's `now`; a client cannot
   backdate `elapsed` (test: `test_server_clock_decides_timers`). Expired
   orders are auto-replaced so nobody is softlocked.
7. **Capabilities.** The engine holds `weave.artifact` only; a request with
   any other capability fails with `capability_denied`.
8. **Match replay is the final judge.** `POST /api/matches/{id}/verify`
   re-executes the entire `(seed + intent log)` timeline and compares full
   canonical states — a disputed match has a byte-exact answer.

## Evidence per important action

Persisted in `data/prolepsis/executions/` + `data/wordchef.db`:

`execution_id · request_id · op · input events · canonical result · digest ·
timestamp · player · match · artifact_refs (CAS) · checkpoint_id · verified ·
audit events (capability decisions, lifecycle)`.

## What is explicitly NOT trusted

score · combo · heat · timer · result · reward · inventory · leaderboard
position · kitchen unlocks · anything else derived from gameplay.

## Test coverage

`tests/anti_cheat/test_anti_cheat.py` (13 tests) + tamper tests in
`tests/prolepsis/test_integration.py` + `verification_failed` path in the
service. All green in CI via `./scripts/run_tests.sh`.
