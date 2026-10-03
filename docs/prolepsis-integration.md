# Prolepsis integration

> 🇷🇺 Русская версия: [`docs/ru/prolepsis-integration.md`](ru/prolepsis-integration.md)

Runtime: **Prolepsis v0.39.0** (WEAVE paradigm, Agent Platform v1,
JACQUARD v0.4 patterns), vendored at `prolepsis/vendor/` and pinned by
version. Two documented micro-patches (`vendor/PATCHES.md`) restore behavior
the upstream docs promise (raisable `IRError`, failure introspection).

## Why Prolepsis is the real execution layer

Every operation that matters for game state passes through
`WordChefRuntime.execute_op` → `AgentGateway.execute` (the same code path the
platform's own SDK/CLI/HTTP server use):

1. **execution id** — `exec_…` envelope, idempotent by `request_id`;
2. **capability policy** — the engine is granted only `weave.artifact`;
   anything else fails closed (`capability_denied`);
3. **event facts (claims)** — the engine's inputs/outputs travel as typed
   facts (`number`/`string`) validated by the reed;
4. **selvedges** — declarative invariants (`score_delta ≥ 0`, `valid ≤ 1`,
   `position ≥ 1`…) checked **before** anything is woven; a broken selvedge
   kills the execution;
5. **weaver handlers** (`handlers.py`) — for `DishReceipt` the weaver
   **recomputes** `scoring.score_dish` from the claimed inputs and compares
   every field (including boost/double adjustments). A single mismatch ⇒
   `IRError("constraint_broken", …)` ⇒ node failed ⇒ execution FAILED ⇒
   the game refuses to advance. This is anti-cheat *inside* the runtime;
6. **artifacts** — receipts (rendered Jacquard wefts + structured payloads)
   committed to a `PersistentArtifactStore` (CAS, `sha256:…`), readable after
   restart;
7. **checkpoints** — every important execution is checkpointed
   (`chk_…`, carries the digest);
8. **replay / verify** — `gateway.replay` re-reduces the canonical log;
   `gateway.verify` requires `recomputed digest == recorded digest`;
9. **audit events** — `execution.validating/ready/running/completed`,
   `capability.requested/granted/denied`, `checkpoint.created`;
10. **async executions** — order generation, ticket closing and leaderboard
    writes run through `prepare`/`execute_queued` on a worker pool (and the
    stock HTTP server proves the 202-queue path in acceptance).

## Jacquard patterns (YAML)

`prolepsis/patterns/wordchef/`:

* `dish.yaml` — `DISH_SUBMIT` (+`SCORE_AWARD`, `PREP_SERVICE`) — the hot path
* `order.yaml` — `ORDER_GENERATE` (+`ORDER_COMPLETE`)
* `round.yaml` — `ROUND_START`/`ROUND_END`
* `match.yaml` — `MATCH_CREATE`/`MATCH_FINISH` + `MATCH_VOIDED` unraveler
* `bonus.yaml` — `BONUS_APPLY`, `CHAOS_EVENT`
* `leaderboard.yaml` — `LEADERBOARD_UPDATE`/`LEADERBOARD_COMMIT`
* `verify.yaml` — `MATCH_VERIFIED`

Each pattern is compiled to Canonical IR at load time (document address
`sha256:…`); the same document is used for replay, so any source change is
immediately visible as `document_matches: false`.

## Determinism

```
same input events + same pattern → same canonical result → same digest
```

Proven in `tests/prolepsis/test_integration.py::TestDeterminism` (two runs,
different execution ids, identical digests and identical artifact refs).

## Acceptance

`python3 scripts/acceptance_prolepsis.py` — 29 checks, both surfaces:

* in-process game runtime (health/ready/version, async, artifacts, CAS
  read-back, checkpoint, replay, verify, restart, audit) and the required
  record (`execution_id`, `digest`, `artifact_refs`, `checkpoint_id`,
  `verified=true`);
* stock `prolepsis agent-serve` over HTTP (health/ready/version, 202 async
  execution, completion, digest, artifacts, checkpoint, replay, verify,
  idempotency, listing/audit).

The script exits non-zero unless **every** check passes — no fake PASS.

## Known limits (honest)

* `IRError` in stock v0.39.0 is a dataclass, not an exception — the vendored
  patch makes handler refusals first-class (see `PATCHES.md`).
* Node-level templates live in the adapter bundle (a source concern); the
  bridge loads them once at startup and the weaver looks them up by node id.
  Artifact names are globally unique across the game patterns to keep that
  lookup total.
* Event ordering within one execution is the input order; cross-execution
  ordering is the game's (the match intent log), verified by match replay.
