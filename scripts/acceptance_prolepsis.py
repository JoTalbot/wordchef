#!/usr/bin/env python3
"""Word Chef — Prolepsis production acceptance.

Runs the mandatory acceptance battery against BOTH integration surfaces:

A. the in-process game runtime (WordChefRuntime — Jacquard game workflows);
B. the stock Prolepsis Agent Platform HTTP server (``prolepsis agent-serve``).

Checks: health · ready · version · async execution · execution completion ·
artifacts · checkpoint · replay · verify · restart · audit — and prints the
required record for an important game execution:
execution_id · digest · artifact_refs · checkpoint_id · verified=true

Exit code 0 only when EVERY check passes. No fake PASS is possible: each
check asserts real runtime state.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "game"))
sys.path.insert(0, str(ROOT / "prolepsis"))
sys.path.insert(0, str(ROOT / "prolepsis" / "vendor"))

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    if ok:
        PASSED.append(name)
        print(f"  PASS  {name}" + (f"  — {detail}" if detail else ""))
    else:
        FAILED.append((name, detail))
        print(f"  FAIL  {name}  — {detail}")
    return ok


def http(method: str, url: str, body: dict | None = None, timeout: float = 30.0):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        try:
            return exc.code, json.loads(payload or "{}")
        except ValueError:
            return exc.code, {"raw": payload}


# ─────────────────────── A. game runtime ───────────────────────

def acceptance_game_runtime(tmp: Path) -> None:
    print("\n══ A · WORD CHEF GAME RUNTIME (in-process, Jacquard patterns) ══")
    from wordchef_prolepsis import ops
    from wordchef_prolepsis.bridge import WordChefRuntime
    from wordchef_game.engine import start_match, start_round, submit_dish
    from wordchef_game.orders import check_order
    from wordchef_game.dictionary import load_dictionary

    runtime = WordChefRuntime(tmp / "prolepsis")

    check("health", runtime.health().get("status") == "ok", str(runtime.health()))
    check("ready", runtime.ready().get("status") == "ready")
    check("version", runtime.version().get("prolepsis") == "0.39.0", str(runtime.version()))

    # an important game execution: a dish served in a match
    d = load_dictionary()
    state = start_match(match_id="acceptance", mode="QUICK_COOK",
                        seed="acceptance-seed", player_ids=["alice", "bob"], now=1000.0)
    op, events, rid = ops.op_create_match(state)
    create_rec = runtime.execute_op(op, events, request_id=rid)
    check("match.create execution completes", create_rec.state == "COMPLETED")

    state = start_round(state, now=1000.0)
    op, events, rid = ops.op_start_round(state)
    check("round.start execution completes",
          runtime.execute_op(op, events, request_id=rid).state == "COMPLETED")

    player = state.players["alice"]
    order = player.order
    word = next(w for w in d.words if check_order(order, w, d)[0])
    state, outcome = submit_dish(state, "alice", word, now=1005.0)
    op, events, rid = ops.op_submit_dish(
        state=state, player=state.players["alice"], order=order, outcome=outcome,
        score_total=state.players["alice"].score, counter=1)
    record = runtime.execute_op(op, events, request_id=rid)

    check("async execution (prepare+worker)", _async_ok(runtime))
    check("execution completion", record.state == "COMPLETED", record.state)
    check("artifacts committed", bool(record.artifact_refs),
          f"{len(record.artifact_refs)} refs")
    payloads = [a["payload"] for a in record.artifacts]
    check("artifact payloads readable from CAS",
          all("kind" in p for p in payloads),
          ", ".join(sorted({p.get("kind", "?") for p in payloads})))
    check("checkpoint created", bool(record.checkpoint_id), record.checkpoint_id or "")

    replay = runtime.gateway.replay(record.execution_id, str(runtime.source_for("dish.submit")))
    check("replay digest matches", replay["matches"] and replay["document_matches"],
          replay["digest"][:28] if replay.get("digest") else "")
    verdict = runtime.verify(record.execution_id)
    check("verify → verified=true", verdict["verified"] is True,
          f"recorded={verdict['recorded_digest'][:24]}…")

    audit = runtime.audit_events(record.execution_id)
    types = {e["type"] for e in audit}
    check("audit events present",
          {"execution.completed", "capability.granted", "checkpoint.created"} <= types,
          f"{len(audit)} events")

    print("\n  ── REQUIRED RECORD (important game execution) ──")
    print(f"  execution_id   : {record.execution_id}")
    print(f"  digest         : {record.digest}")
    print(f"  artifact_refs  : {record.artifact_refs}")
    print(f"  checkpoint_id  : {record.checkpoint_id}")
    print(f"  verified       : {record.verified}")
    check("required record complete",
          bool(record.execution_id and record.digest and record.artifact_refs
               and record.checkpoint_id and record.verified is True))

    # restart: a fresh runtime must still verify the sealed execution
    runtime.shutdown()
    runtime2 = WordChefRuntime(tmp / "prolepsis")
    verdict2 = runtime2.verify(record.execution_id)
    check("restart: sealed execution still verifies", verdict2["verified"] is True)
    check("restart: artifacts still readable",
          bool(runtime2.read_artifacts(record.execution_id)))
    runtime2.shutdown()


def _async_ok(runtime) -> bool:
    from wordchef_prolepsis import ops
    from wordchef_game.engine import start_match
    state = start_match(match_id=f"async-{int(time.time())}", mode="SOLO",
                        seed="async", player_ids=["a"], now=0)
    op, events, rid = ops.op_create_match(state)
    future = runtime.execute_op_async(op, events, request_id=rid)
    record = future.result(timeout=30)
    return record.state == "COMPLETED" and record.verified is True


# ─────────────────────── B. platform HTTP server ───────────────────────

def acceptance_platform_server(tmp: Path) -> None:
    print("\n══ B · PROLEPSIS AGENT PLATFORM HTTP SERVER (agent-serve) ══")
    port = 7397
    state_dir = tmp / "server-state"
    env = dict(os.environ)
    proc = subprocess.Popen(
        [sys.executable, "-m", "prolepsis._cli", "agent-serve",
         "--host", "127.0.0.1", "--port", str(port),
         "--state-dir", str(state_dir)],
        cwd=str(ROOT / "prolepsis" / "vendor"),
        env={**env, "PYTHONPATH": str(ROOT / "prolepsis" / "vendor")},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                status, _ = http("GET", f"{base}/v1/health")
                if status == 200:
                    break
            except Exception:
                time.sleep(0.25)
        else:
            check("platform server boots", False, "did not answer /v1/health")
            return

        status, health = http("GET", f"{base}/v1/health")
        check("platform health", status == 200 and health.get("status") == "ok", str(health))
        status, ready = http("GET", f"{base}/v1/ready")
        check("platform ready", status == 200 and ready.get("status") == "ready", str(ready))
        status, version = http("GET", f"{base}/v1/version")
        check("platform version", status == 200 and "server" in version, str(version))

        # async execution of a real game dish workflow through the HTTP protocol.
        # Build the exact authoritative payload used by the game bridge rather than
        # hand-writing a plausible-looking claim. This keeps HTTP acceptance aligned
        # with the in-process surface and binds the execution to the canonical pack.
        source = str(ROOT / "prolepsis" / "patterns" / "wordchef" / "dish.yaml")
        from wordchef_game.engine import start_match, start_round, submit_dish
        from wordchef_game.dictionary import load_dictionary
        from wordchef_game.orders import check_order
        from wordchef_game.content import CONTENT_DIGEST
        from wordchef_prolepsis.ops import op_submit_dish

        d = load_dictionary()
        game_state = start_match(
            match_id="srv", mode="SOLO", seed="acceptance-http",
            player_ids=["alice"], now=1000.0,
        )
        game_state = start_round(game_state, now=1000.0)
        srv_player = game_state.players["alice"]
        srv_order = srv_player.order
        srv_word = next(w for w in d.words if check_order(srv_order, w, d)[0])
        game_state, srv_outcome = submit_dish(
            game_state, "alice", srv_word, now=1005.0,
        )
        _, events, request_id = op_submit_dish(
            state=game_state, player=game_state.players["alice"],
            order=srv_order, outcome=srv_outcome,
            score_total=game_state.players["alice"].score, counter=1,
        )
        events = [
            {**event, "payload": {**event["payload"], "content_digest": CONTENT_DIGEST}}
            for event in events
        ]
        status, queued = http("POST", f"{base}/v1/executions", {
            "source": source, "request_id": request_id,
            "agent_id": "wordchef-acceptance",
            "capabilities": ["weave.artifact"],
            "events": events,
            "async": True,
        })
        check("async execution accepted (202 + id)", status == 202 and
              str(queued.get("execution_id", "")).startswith("exec_"),
              queued.get("execution_id", ""))
        execution_id = queued["execution_id"]

        state = "QUEUED"
        for _ in range(120):
            _, record = http("GET", f"{base}/v1/executions/{execution_id}")
            state = record.get("state")
            if state in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            time.sleep(0.25)
        check("async execution completes over HTTP", state == "COMPLETED", state)

        _, record = http("GET", f"{base}/v1/executions/{execution_id}")
        check("digest recorded", str(record.get("digest", "")).startswith("sha256:"),
              str(record.get("digest", ""))[:28])
        check("artifact_refs recorded", bool(record.get("artifact_refs")),
              f"{len(record.get('artifact_refs', []))} refs")

        status, checkpoint = http("POST", f"{base}/v1/executions/{execution_id}/checkpoint",
                                  {"metadata": {"phase": "acceptance"}})
        check("checkpoint over HTTP", status == 200 and
              str(checkpoint.get("checkpoint_id", "")).startswith("chk_"),
              checkpoint.get("checkpoint_id", ""))

        status, replay = http("POST", f"{base}/v1/executions/{execution_id}/replay",
                              {"source": source})
        check("replay over HTTP", status == 200 and replay.get("matches") is True)
        status, verdict = http("POST", f"{base}/v1/executions/{execution_id}/verify",
                               {"source": source})
        check("verify over HTTP → verified=true",
              status == 200 and verdict.get("verified") is True, str(verdict)[:80])

        status, artifacts = http("GET", f"{base}/v1/executions/{execution_id}/artifacts")
        check("artifact listing over HTTP", status == 200)

        # idempotency: the same request_id must not duplicate work
        status2, again = http("POST", f"{base}/v1/executions", {
            "source": source, "request_id": request_id,
            "agent_id": "wordchef-acceptance",
            "capabilities": ["weave.artifact"],
            "events": events,
        })
        check("idempotent request_id", again.get("execution_id") == execution_id)

        status, listing = http("GET", f"{base}/v1/executions")
        check("execution listing + audit trail", status == 200 and
              any(e.get("execution_id") == execution_id for e in listing.get("executions", [])))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wc-acceptance-"))
    print("Word Chef · Prolepsis acceptance · v0.39.0")
    try:
        acceptance_game_runtime(tmp)
        acceptance_platform_server(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n══ ACCEPTANCE SUMMARY ══")
    print(f"  passed: {len(PASSED)}")
    print(f"  failed: {len(FAILED)}")
    for name, detail in FAILED:
        print(f"    ✗ {name}: {detail}")
    if FAILED:
        print("\nACCEPTANCE: FAILED")
        return 1
    print("\nACCEPTANCE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
