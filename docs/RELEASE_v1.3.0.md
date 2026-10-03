# Word Chef v1.3.0

## Production hardening

- API request boundaries are validated before game logic runs.
- Player names are normalized and blank names are rejected.
- Match rounds, player count, seed, intent fields and dictionary queries have explicit limits.
- Leaderboard queries are bounded to a safe maximum.
- HTTP responses include baseline browser security headers.
- `/healthz` reports the shipped application version.

## Verification

- Frontend TypeScript: PASS
- Frontend production export: PASS
- Bundle verification: PASS
- Backend/game test battery: PASS
- Prolepsis acceptance: PASS

## Compatibility

This release preserves the server-authoritative game protocol and existing client flow. No database migration is required.

## Known deployment constraint

Multiplayer remains designed for a single-process deployment because the in-memory match/event bus is process-local.
