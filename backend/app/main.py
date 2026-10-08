"""Word Chef — FastAPI application factory.

Serves the API, the WebSocket feed and the built frontend (Next.js static
export) from one origin. Run with:

    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.config import DB_PATH, FRONTEND_DIR, PROLEPSIS_ROOT
from app.service import GameService
from app.store import Store
from wordchef_prolepsis.bridge import WordChefRuntime


def create_app() -> FastAPI:
    app = FastAPI(title="Word Chef API", version="1.17.0",
                  description="Server-authoritative multiplayer word-cooking")
    store = Store(DB_PATH)
    runtime = WordChefRuntime(PROLEPSIS_ROOT)
    app.state.service = GameService(store, runtime)
    app.state.started_at = time.time()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Request-ID", request.headers.get("X-Request-ID", ""))
        return response

    app.include_router(router)

    @app.get("/healthz")
    def healthz():
        return JSONResponse({"status": "ok", "game": "wordchef", "version": "1.17.0", "uptime_s": round(max(0.0, time.time() - app.state.started_at), 3)})

    if FRONTEND_DIR.is_dir():
        assets = FRONTEND_DIR / "_next"
        if assets.is_dir():
            app.mount("/_next", StaticFiles(directory=str(assets)), name="next-assets")

        @app.get("/")
        def index():
            return FileResponse(str(FRONTEND_DIR / "index.html"))

        @app.get("/{path:path}")
        def spa(path: str):
            candidate = FRONTEND_DIR / path
            if candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(FRONTEND_DIR / "index.html"))
    else:
        @app.get("/")
        def index_missing():
            return JSONResponse({
                "game": "Word Chef",
                "note": "frontend not built yet — run `npm run build` in frontend/",
            })

    return app


app = create_app()
