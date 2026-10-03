# Развёртывание

> 🇬🇧 English original: [`../deployment.md`](../deployment.md)

## Docker (рекомендуется)

```bash
docker compose up --build
# игра:      http://localhost:8000
# платформа: http://localhost:7397/v1/health  (опциональный prolepsis agent-serve)
```

`docker-compose.yml` поднимает:

* **wordchef** — FastAPI (API + WebSocket + статический фронтенд) на `:8000`,
  том `wordchef-data:/app/data` (SQLite + Prolepsis-исполнения + CAS);
* **prolepsis** *(профиль `platform`)* — штатный `agent-serve` на `:7397` для
  операционной видимости (health/ready/version/SSE/metrics), тот же том данных.

## Вручную

```bash
pip install fastapi "uvicorn[standard]" pydantic
(cd frontend && npm install && npm run build)
PYTHONPATH=backend:game:prolepsis:prolepsis/vendor \
  python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Операционные заметки

* **Состояние**: всё долговечное живёт в `data/` (переменные `WC_DATA_DIR`,
  `WC_DB_PATH`). Делайте бэкап этого каталога; восстановление проверено набором
  тестов рестарта.
* **Рестарты**: безопасны в любой момент — снимки матчей + логи интентов +
  конверты исполнений продолжаются ровно с того же места
  (`tests/persistence`).
* **TLS**: терминируйте на обратном прокси (Caddy/nginx). Установите
  `PROLEPSIS_AUTH_TOKEN`, если выставляете `agent-serve` за пределы localhost.
* **Сезоны**: переменная `WC_SEASON` перезапускает таблицу лидеров.
* **Масштабирование**: один процесс-писатель на каталог данных (SQLite и
  JSON-хранилища лочатся по файлам). Для нескольких хостов замените хранилища на
  Postgres и общий CAS — API моста на этой границе уже не зависит от хранилища.
* **Наблюдаемость**: `/api/audit/executions`, `/api/audit/events`,
  `agent-serve` `/metrics` (текстовый Prometheus), реплей матча для спорных
  ситуаций.

## Проверки здоровья

* игра: `GET /healthz`
* prolepsis: `GET /api/prolepsis/health` · `ready` · `version`
* сервер платформы: `GET :7397/v1/health` · `ready` · `version`
