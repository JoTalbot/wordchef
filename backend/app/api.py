"""Word Chef HTTP API — REST + WebSocket. Server-authoritative."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.service import GameError, GameService

router = APIRouter(prefix="/api")


def service(request: Request) -> GameService:
    return request.app.state.service


def _err(exc: GameError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)})


# ── models ──────────────────────────────────────────────────

class RegisterBody(BaseModel):
    name: str = Field(default="Chef", max_length=32)


class CreateMatchBody(BaseModel):
    mode: str = "SOLO"
    player_ids: list[str] = Field(default_factory=list)
    kitchen_id: str = "street"
    rounds: int = 3
    seed: str = ""


class IntentBody(BaseModel):
    player_id: str
    action: str
    word: str = ""
    use_spice: bool = False
    effect: str = ""


# ── players ─────────────────────────────────────────────────

@router.post("/players")
def register(body: RegisterBody, request: Request):
    try:
        return service(request).register_player(body.name)
    except GameError as exc:
        raise _err(exc)


@router.get("/players/{player_id}/progression")
def progression(player_id: str, request: Request):
    try:
        return service(request).progression(player_id)
    except GameError as exc:
        raise _err(exc)


# ── static game info ────────────────────────────────────────

@router.get("/info")
def info(request: Request):
    from wordchef_game.kitchens import KITCHENS
    from wordchef_game.orders import KINDS, THEMES
    return {
        "game": "Word Chef",
        "modes": ["SOLO", "QUICK_COOK", "CHAOS_KITCHEN"],
        "kitchens": [{"kitchen_id": k.kitchen_id, "name": k.name,
                      "tagline": k.tagline, "unlock_xp": k.unlock_xp,
                      "rule": k.rule, "theme": k.theme} for k in KITCHENS],
        "order_kinds": list(KINDS),
        "themes": {t: len(ws) for t, ws in THEMES.items()},
    }


@router.get("/dictionary/check")
def check_word(word: str, request: Request):
    from wordchef_game.dictionary import load_dictionary
    dictionary = load_dictionary()
    return {"word": word.lower(), "is_word": dictionary.is_word(word),
            "length": len(word)}


# ── matches ─────────────────────────────────────────────────

@router.post("/matches")
def create_match(body: CreateMatchBody, request: Request):
    try:
        names = {}
        for pid in body.player_ids:
            player = service(request).store.get_player(pid)
            if player:
                names[pid] = player["name"]
        return service(request).create_match(
            mode=body.mode, player_ids=body.player_ids,
            kitchen_id=body.kitchen_id, rounds=body.rounds,
            seed=body.seed, display_names=names)
    except GameError as exc:
        raise _err(exc)


@router.get("/matches/{match_id}")
def match_state(match_id: str, request: Request):
    try:
        return service(request).match_view(match_id)
    except GameError as exc:
        raise _err(exc)


@router.get("/matches/{match_id}/players/{player_id}")
def player_view(match_id: str, player_id: str, request: Request):
    try:
        return service(request).player_view(match_id, player_id)
    except GameError as exc:
        raise _err(exc)


@router.post("/matches/{match_id}/start")
def start_match(match_id: str, request: Request):
    try:
        return service(request).start_match(match_id)
    except GameError as exc:
        raise _err(exc)


@router.post("/matches/{match_id}/intent")
def intent(match_id: str, body: IntentBody, request: Request):
    try:
        payload = {"action": body.action}
        if body.word:
            payload["word"] = body.word
        if body.use_spice:
            payload["use_spice"] = True
        if body.effect:
            payload["effect"] = body.effect
        return service(request).submit_intent(match_id, body.player_id, payload)
    except GameError as exc:
        raise _err(exc)


@router.post("/matches/{match_id}/verify")
def verify(match_id: str, request: Request):
    try:
        return service(request).verify_match(match_id)
    except GameError as exc:
        raise _err(exc)


# ── leaderboard & audit ─────────────────────────────────────


class LevelCompleteBody(BaseModel):
    level_no: int
    words: list[str] = []
    bonus: list[str] = []
    player_id: str | None = None


@router.get("/levels/{level_no}")
def get_level(level_no: int, request: Request):
    if level_no < 1:
        raise HTTPException(400, "level_no must be >= 1")
    return service(request).get_level(level_no)


@router.post("/levels/complete")
def complete_level(body: LevelCompleteBody, request: Request):
    try:
        return service(request).complete_level(
            body.player_id, body.level_no, body.words, body.bonus)
    except GameError as exc:
        raise _err(exc)


@router.get("/leaderboard")
def leaderboard(request: Request, limit: int = 50):
    return {"season": "season-1", "entries": service(request).leaderboard(limit)}


@router.get("/audit/executions")
def audit_executions(request: Request, match_id: str = ""):
    return {"executions": service(request).audit_executions(match_id or None)}


@router.get("/audit/executions/{execution_id}")
def audit_detail(execution_id: str, request: Request):
    try:
        return service(request).audit_execution_detail(execution_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail={"code": "unknown_execution",
                                                    "message": str(exc)})


@router.get("/audit/events")
def audit_events(request: Request, execution_id: str = ""):
    return {"events": service(request).runtime.audit_events(execution_id or None)}


# ── prolepsis platform ──────────────────────────────────────

@router.get("/prolepsis/health")
def prolepsis_health(request: Request):
    return service(request).prolepsis_health()


@router.get("/prolepsis/ready")
def prolepsis_ready(request: Request):
    return service(request).prolepsis_ready()


@router.get("/prolepsis/version")
def prolepsis_version(request: Request):
    return service(request).prolepsis_version()


# ── WebSocket: live match feed ──────────────────────────────

@router.websocket("/ws/matches/{match_id}")
async def match_feed(websocket: WebSocket, match_id: str):
    await websocket.accept()
    svc: GameService = websocket.app.state.service
    try:
        svc.match_view(match_id)
    except GameError as exc:
        await websocket.send_text(json.dumps({"type": "ERROR", "code": exc.code}))
        await websocket.close()
        return
    item = svc.subscribe(match_id)
    try:
        await websocket.send_text(json.dumps(
            {"type": "SYNC", "events": svc.events_since(match_id, 0)}))
        while True:
            try:
                message = await asyncio.wait_for(item[1].get(), timeout=20.0)
                await websocket.send_text(json.dumps({"type": "EVENT", "event": message}))
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "PING"}))
    except WebSocketDisconnect:
        pass
    finally:
        svc.unsubscribe(match_id, item)
