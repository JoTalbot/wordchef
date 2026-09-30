"""Word Chef — FastAPI application factory.

Serves the API, the WebSocket feed and the built frontend (Next.js static
export) from one origin. Run with:

    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.config import DB_PATH, FRONTEND_DIR, PROLEPSIS_ROOT
from app.service import GameService
from app.store import Store
from wordchef_prolepsis.bridge import WordChefRuntime


def create_app() -> FastAPI:
    app = FastAPI(title="Word Chef API", version="1.0.0",
                  description="Server-authoritative multiplayer word-cooking")
    store = Store(DB_PATH)
    runtime = WordChefRuntime(PROLEPSIS_ROOT)
    app.state.service = GameService(store, runtime)
    app.include_router(router)

    @app.get("/healthz")
    def healthz():
        return JSONResponse({"status": "ok", "game": "wordchef"})

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
