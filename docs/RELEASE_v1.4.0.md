# Word Chef v1.4.0

## Production runtime hardening

- Docker frontend builds use the committed npm lockfile with `npm ci`.
- The backend container runs as a dedicated non-root user.
- Python runtime defaults disable bytecode writes and use unbuffered logs.
- `/healthz` reports the shipped version and process uptime.
- API responses are explicitly marked `no-store` to avoid accidental caching of game state and audit data.
- Existing browser security headers remain enabled.

## Verification

Frontend typecheck: PASS
Frontend production export: PASS
Backend/game test battery: PASS
Prolepsis acceptance: PASS

## Compatibility

The server-authoritative protocol and existing client flow are unchanged. No database migration is required.

## Deployment note

Multiplayer remains designed for a single-process deployment because the in-memory match/event bus is process-local.
