# Интеграция с Prolepsis

> 🇬🇧 English original: [`../prolepsis-integration.md`](../prolepsis-integration.md)

Рантайм: **Prolepsis v0.39.0** (парадигма WEAVE, Agent Platform v1,
JACQUARD v0.4 patterns), вендорен в `prolepsis/vendor/` и закреплён по версии.
Два задокументированных микропатча (`vendor/PATCHES.md`) возвращают поведение,
которое обещает документация апстрима (бросаемый `IRError`, интроспекция
ошибок).

## Почему Prolepsis — настоящий слой исполнения

Каждая операция, значимая для состояния игры, проходит через
`WordChefRuntime.execute_op` → `AgentGateway.execute` (тот же кодовый путь,
что используют SDK/CLI/HTTP-сервер самой платформы):

1. **execution id** — конверт `exec_…`, идемпотентный по `request_id`;
2. **политика capability** — движку выдан только `weave.artifact`; всё
   остальное падает закрыто (`capability_denied`);
3. **факты-события (заявки)** — входы/выходы движка путешествуют как
   типизированные факты (`number`/`string`), валидируемые reed'ом;
4. **селвиджи** — декларативные инварианты (`score_delta ≥ 0`, `valid ≤ 1`,
   `position ≥ 1`…), проверяемые **до** того, как что-либо соткётся; сломанный
   селвидж убивает исполнение;
5. **веверы** (`handlers.py`) — для `DishReceipt` вевер **пересчитывает**
   `scoring.score_dish` из заявленных входов и сравнивает каждое поле (включая
   поправки на буст/дабл). Одно расхождение ⇒ `IRError("constraint_broken", …)`
   ⇒ узел failed ⇒ исполнение FAILED ⇒ игра отказывается двигаться вперёд. Это
   анти-чит *внутри* рантайма;
6. **артефакты** — квитанции (отрендеренные утки́ Jacquard + структурные
   нагрузки) коммитятся в `PersistentArtifactStore` (CAS, `sha256:…`), читаемы
   после рестарта;
7. **чекпойнты** — каждое важное исполнение чекпойнтится (`chk_…`, несёт
   дайджест);
8. **replay / verify** — `gateway.replay` заново сводит канонический лог;
   `gateway.verify` требует `recomputed digest == recorded digest`;
9. **аудит-события** — `execution.validating/ready/running/completed`,
   `capability.requested/granted/denied`, `checkpoint.created`;
10. **асинхронные исполнения** — генерация заказов, закрытие билетов и записи
    в таблицу лидеров идут через `prepare`/`execute_queued` на пуле воркеров
    (а штатный HTTP-сервер доказывает путь очереди 202 в приёмке).

## Jacquard-паттерны (YAML)

`prolepsis/patterns/wordchef/`:

* `dish.yaml` — `DISH_SUBMIT` (+`SCORE_AWARD`, `PREP_SERVICE`) — горячий путь
* `order.yaml` — `ORDER_GENERATE` (+`ORDER_COMPLETE`)
* `round.yaml` — `ROUND_START`/`ROUND_END`
* `match.yaml` — `MATCH_CREATE`/`MATCH_FINISH` + распускатель `MATCH_VOIDED`
* `bonus.yaml` — `BONUS_APPLY`, `CHAOS_EVENT`
* `leaderboard.yaml` — `LEADERBOARD_UPDATE`/`LEADERBOARD_COMMIT`
* `verify.yaml` — `MATCH_VERIFIED`

Каждый паттерн компилируется в канонический IR при загрузке (адрес документа
`sha256:…`); тот же документ используется для реплея, поэтому любое изменение
исходника немедленно видно как `document_matches: false`.

## Детерминизм

```
те же входные события + тот же паттерн → тот же канонический результат → тот же дайджест
```

Доказано в `tests/prolepsis/test_integration.py::TestDeterminism` (два прогона,
разные execution id, идентичные дайджесты и идентичные ссылки на артефакты).

## Приёмка

`python3 scripts/acceptance_prolepsis.py` — 29 проверок, обе поверхности:

* игровой рантайм в процессе (health/ready/version, async, артефакты, чтение
  CAS, чекпойнт, реплей, верификация, рестарт, аудит) и обязательная запись
  (`execution_id`, `digest`, `artifact_refs`, `checkpoint_id`, `verified=true`);
* штатный `prolepsis agent-serve` по HTTP (health/ready/version, асинхронное
  исполнение 202, завершение, дайджест, артефакты, чекпойнт, реплей,
  верификация, идемпотентность, листинг/аудит).

Скрипт завершается ненулевым кодом, если не прошла **любая** проверка — никаких
фальшивых PASS.

## Известные ограничения (честно)

* `IRError` в штатной v0.39.0 — это dataclass, а не исключение; вендоренный
  патч делает отказы обработчиков полноценными (см. `PATCHES.md`).
* Шаблоны уровней узлов живут в адаптерном бандле (вопрос исходников); мост
  загружает их один раз при старте, а вевер ищет по id узла. Имена артефактов
  глобально уникальны среди игровых паттернов, чтобы этот поиск был полным.
* Порядок событий внутри одного исполнения — это порядок входов; порядок между
  исполнениями задаёт игра (лог интентов матча), проверяется реплеем матча.
