#!/usr/bin/env python3
"""Word Chef — final smoke test against a RUNNING server.

Usage: python3 scripts/smoke_test.py [http://localhost:8000]

Exercises the shipped product end-to-end over HTTP:
frontend shell · players · match · dish · anti-cheat rejection ·
full match to completion · replay verification · leaderboard ·
prolepsis platform endpoints · audit trail.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"


def req(path: str, data: dict | None = None):
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(BASE + path, data=body,
                                     method="POST" if body is not None else "GET")
    if body:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, json.loads(response.read().decode() or "{}")


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def ok(name: str, cond: bool, detail: str = ""):
        checks.append((name, bool(cond), detail))
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"  — {detail}" if detail else ""))

    print(f"Word Chef smoke test → {BASE}")

    status, _ = req("/healthz")
    ok("healthz", status == 200)

    import urllib.request as u
    html = u.urlopen(BASE + "/", timeout=30).read().decode()
    ok("frontend shell serves WORD CHEF", "WORD CHEF" in html and "_next" in html)

    _, info = req("/api/info")
    ok("game info", info.get("game") == "Word Chef" and len(info.get("kitchens", [])) >= 7)

    _, alice = req("/api/players", {"name": "Smokey"})
    _, bob = req("/api/players", {"name": "Barbecue"})
    ok("players register", alice["player_id"].startswith("p_"))

    _, match = req("/api/matches", {
        "mode": "QUICK_COOK",
        "player_ids": [alice["player_id"], bob["player_id"]],
        "rounds": 1, "seed": "smoke-final"})
    mid = match["match_id"]
    req(f"/api/matches/{mid}/start", {})

    _, view = req(f"/api/matches/{mid}/players/{alice['player_id']}")
    ok("order dealt", view.get("order") is not None,
       f"{view['order']['kind']} / {view['order']['tray']}")

    sys.path.insert(0, "game")
    from wordchef_game.orders import Order, check_order          # noqa: E402
    from wordchef_game.dictionary import load_dictionary        # noqa: E402
    d = load_dictionary()
    order = Order.from_dict(view["order"])
    word = next((w for w in d.words if check_order(order, w, d)[0]), None)
    ok("order solvable", word is not None, word or "")

    _, res = req(f"/api/matches/{mid}/intent", {
        "player_id": alice["player_id"], "action": "SUBMIT_DISH", "word": word})
    ok("dish served", res["reason"] in ("dish_served", "streak_progress"),
       f"+{res['payload'].get('score_delta')}")
    ok("dish sealed + verified", bool(res["payload"].get("execution")) and
       res["payload"]["execution"]["verified"] is True,
       res["payload"]["execution"]["digest"][:28])

    _, res = req(f"/api/matches/{mid}/intent", {
        "player_id": bob["player_id"], "action": "SUBMIT_DISH", "word": "zzzzz"})
    ok("anti-cheat: bad word rejected", res["reason"] == "not_in_dictionary")

    finished = False
    for _ in range(30):
        _, state = req(f"/api/matches/{mid}")
        if state["finished"]:
            finished = True
            break
        for p in (alice, bob):
            _, v = req(f"/api/matches/{mid}/players/{p['player_id']}")
            if not v.get("order"):
                continue
            o = Order.from_dict(v["order"])
            w = next((x for x in d.words if check_order(o, x, d)[0]), None)
            req(f"/api/matches/{mid}/intent", {
                "player_id": p["player_id"],
                "action": "SUBMIT_DISH" if w else "SKIP", "word": w or ""})
    ok("match plays to completion", finished)

    _, verdict = req(f"/api/matches/{mid}/verify", {})
    ok("replay verification", verdict["verdict"] == "verified",
       verdict["verify_execution"]["digest"][:28])

    _, board = req("/api/leaderboard")
    ok("leaderboard updated", bool(board["entries"]) and board["entries"][0]["score"] > 0)

    _, execs = req(f"/api/audit/executions?match_id={mid}")
    rows = execs["executions"]
    ok("audit trail", len(rows) >= 5 and all(r["digest"] for r in rows),
       f"{len(rows)} executions")

    _, health = req("/api/prolepsis/health")
    _, ready = req("/api/prolepsis/ready")
    _, version = req("/api/prolepsis/version")
    ok("prolepsis platform endpoints",
       health["status"] == "ok" and ready["status"] == "ready"
       and version["prolepsis"] == "0.39.0", version.get("server", ""))

    failed = [c for c in checks if not c[1]]
    print(f"\nSMOKE: {len(checks) - len(failed)}/{len(checks)} passed")
    if failed:
        print("SMOKE TEST: FAILED")
        return 1
    print("SMOKE TEST: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
