# Deployment

> 🇷🇺 Русская версия: [`docs/ru/deployment.md`](ru/deployment.md)

## Docker (recommended)

```bash
docker compose up --build
# game:      http://localhost:8000
# platform:  http://localhost:7397/v1/health  (optional prolepsis agent-serve)
```

`docker-compose.yml` runs:

* **wordchef** — FastAPI (API + WebSocket + static frontend) on `:8000`,
  volume `wordchef-data:/app/data` (SQLite + Prolepsis executions + CAS);
* **prolepsis** *(profile `platform`)* — stock `agent-serve` on `:7397` for
  ops visibility (health/ready/version/SSE/metrics), same data volume.

## Manual

```bash
pip install fastapi "uvicorn[standard]" pydantic
(cd frontend && npm install && npm run build)
PYTHONPATH=backend:game:prolepsis:prolepsis/vendor \
  python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Operational notes

* **State**: everything durable lives under `data/` (env `WC_DATA_DIR`,
  `WC_DB_PATH`). Back up that directory; restores are tested by the restart
  suite.
* **Restarts**: safe at any moment — match snapshots + intent logs +
  execution envelopes resume exactly (`tests/persistence`).
* **TLS**: terminate at a reverse proxy (Caddy/nginx). Set
  `PROLEPSIS_AUTH_TOKEN` when exposing `agent-serve` beyond localhost.
* **Seasons**: set `WC_SEASON` to roll the leaderboard.
* **Scaling**: one writer process per data directory (SQLite + JSON stores
  are file-locked). For multi-host, swap the stores for Postgres and a shared
  CAS — the bridge API is already storage-agnostic at that boundary.
* **Observability**: `/api/audit/executions`, `/api/audit/events`,
  `agent-serve` `/metrics` (Prometheus text), match replay for disputes.

## Health checks

* game: `GET /healthz`
* prolepsis: `GET /api/prolepsis/health` · `ready` · `version`
* platform server: `GET :7397/v1/health` · `ready` · `version`
