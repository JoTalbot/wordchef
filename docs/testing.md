# Testing

Run everything:

```bash
./scripts/run_tests.sh
# = pytest (98 tests) + Prolepsis acceptance (29 checks) + frontend build check
```

## Map

| Suite | Files | Covers |
|---|---|---|
| unit | `tests/unit/test_core.py` | RNG determinism, dictionary, trays, flavors, kitchens, order generation (solvability!), secret objectives |
| game rules | `tests/unit/test_game_rules.py` | scoring multipliers, combo/heat/spice/prep/golden, streaks, skips, expiry, round flow, chaos kitchen, bell, full-match determinism |
| prolepsis | `tests/prolepsis/test_integration.py` | all 7 patterns compile; 10 op families execute; artifacts readable; checkpoints; capability deny; async path; audit trail; **same input → same digest**; replay/verify; tampered log & tampered digest detected |
| anti-cheat | `tests/anti_cheat/test_anti_cheat.py` | inflated score/combo/boost claims refused by the loom; negative scores & zero positions break selvedges; intent field injection ignored; server clock; golden cannot be minted; prep capped |
| API | `tests/api/test_api.py` | full REST surface, lifecycle, errors (400/404/409), leaderboard, audit endpoints, platform endpoints, verify endpoint |
| multiplayer | `tests/multiplayer/test_multiplayer.py` | Quick Cook identical tickets & equal scoring, Chaos Kitchen chaos + bell, WebSocket SYNC/EVENT frames, no monetization hooks |
| persistence | `tests/persistence/test_persistence.py` | store round-trips; **service restart resumes play**; **runtime restart still verifies sealed executions**; checkpoint records |
| frontend | `tests/frontend/test_smoke.py` | export exists, game shell present, protocol markers in the bundle, size budget, no external CDN, mobile viewport |

## Determinism gate

The critical contract is asserted directly:

```
same input → same execution → same canonical result → same digest
```

(`TestDeterminism::test_same_input_same_digest` — two runs, distinct
execution ids, identical digests and identical CAS refs.)

## Acceptance & smoke

* `python3 scripts/acceptance_prolepsis.py` — production acceptance of the
  Prolepsis layer (29 checks, both in-process and `agent-serve` HTTP).
* `python3 scripts/smoke_test.py http://localhost:8000` — end-to-end against
  a live server: shell → match → dish → anti-cheat → completion → replay
  verdict → leaderboard → audit → platform endpoints.

## Adding tests

Game rules go in `tests/unit/` against the pure engine; anything touching
executions goes in `tests/prolepsis/`; anything about cheating goes in
`tests/anti_cheat/`. Use `tests/helpers.py::find_word` for order-solving
words — it scans the full dictionary.
