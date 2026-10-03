# Справочник API

> 🇬🇧 English original: [`../api.md`](../api.md)

Базовый URL — тот же origin, что у игры (по умолчанию `http://localhost:8000`).
Все тела и ответы — JSON. Ошибки: `{"detail": {"code", "message"}}`.

## Игроки

### `POST /api/players`
`{"name": "Alice"}` → `{"player_id": "p_…", "name": "Alice", "xp": 0, …}`

### `GET /api/players/{player_id}/progression`
→ `{"xp", "kitchen", "kitchen_name", "next_kitchen": {name, unlock_xp, remaining}|null}`

## Информация об игре

### `GET /api/info`
Режимы, кухни, виды заказов, темы.

### `GET /api/dictionary/check?word=bread`
→ `{"word": "bread", "is_word": true, "length": 5}`

## Матчи

### `POST /api/matches`
```json
{"mode": "QUICK_COOK", "player_ids": ["p_…", "p_…"], "rounds": 3,
 "kitchen_id": "street", "seed": "optional-loom-seed"}
```
→ вид матча. Режимы: `SOLO` | `QUICK_COOK` | `CHAOS_KITCHEN` (1–8 игроков,
1–12 раундов).

### `POST /api/matches/{match_id}/start`
Открывает раунд 1 (раздаёт заказы, запускает таймеры, запечатывает
`ROUND_START` + билеты заказов на луче).

### `GET /api/matches/{match_id}`
Состояние матча + таблица лидеров + недавний хаос.

### `GET /api/matches/{match_id}/players/{player_id}`
Вид шефа: счёт, комбо, жар, приправа, золото, заготовки, текущий заказ (поднос,
ограничения, дедлайн, серия), счётчики раунда.

### `POST /api/matches/{match_id}/intent`
**Единственный способ действовать.** Тело: `{"player_id", "action", …}`:

| action | доп. поля | эффект |
|---|---|---|
| `SUBMIT_DISH` | `word`, `use_spice?` | подать блюдо под текущий заказ |
| `PREP` | `word` (2–3 буквы) | отложить очки/токены заготовки |
| `GOLDEN` | `effect`: `patience`/`restock`/`boost` | потратить золотые звёзды |
| `RING_BELL` | — | хаос-событие (Хаос-кухня, стоит 1⭐) |
| `SKIP` | — | свежий билет, комбо сбрасывается |

→ `{"accepted", "action", "reason", "player_id", "payload": {player,
leaderboard, round_complete, dish?, score_delta?, next_order?, chaos?,
execution: {execution_id, digest, verified, checkpoint_id}}}`.

Значения `reason` включают `dish_served`, `streak_progress`, `prepped`,
`patience`, `restocked`, `boost_armed`, `skipped`, `not_in_dictionary`,
`not_formable_from_tray`, `word_already_served`, `order_expired`,
`needs_at_least_N_letters`, `needs_letter_x`, `not_on_the_…_menu`,
`not_enough_golden`, `unknown_action`.

> Коды `reason` в протоколе остаются английскими — это машинные коды. На русский
> их переводит только слой отображения во фронтенде (`ruReason()`).

### `POST /api/matches/{match_id}/verify`
Детерминированный реплей `(seed + лог интентов)`:
→ `{"verdict": "verified"|"diverged", "state_match", "outcome_match",
"verify_execution": {…}}`

## Таблица лидеров и аудит

* `GET /api/leaderboard?limit=50` — сезонные позиции (считает сервер).
* `GET /api/audit/executions?match_id=…` — индекс исполнений: `execution_id`,
  `op`, `request_id`, `state`, `digest`, `checkpoint_id`, `verified`,
  `artifact_refs`.
* `GET /api/audit/executions/{execution_id}` — полный конверт + полезные
  нагрузки CAS.
* `GET /api/audit/events?execution_id=…` — аудит-события (`execution.*`,
  `capability.*`, `checkpoint.created`).

## Платформа Prolepsis

* `GET /api/prolepsis/health` · `GET /api/prolepsis/ready` ·
  `GET /api/prolepsis/version`

## WebSocket

`WS /api/ws/matches/{match_id}`

Кадры сервера:
* `{"type":"SYNC","events":[…]}` — накопленный бэклог при подключении;
* `{"type":"EVENT","event":{…}}` — живые события (`MATCH_CREATED`,
  `ROUND_STARTED`, `INTENT`, `ROUND_ENDED`, `MATCH_FINISHED`, …);
* `{"type":"PING"}` — keepalive каждые 20 с.
