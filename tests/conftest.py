"""Shared fixtures: import paths, temp stores, running services."""
from __future__ import annotations

import sys
import shutil
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (str(ROOT / "backend"), str(ROOT / "game"),
              str(ROOT / "prolepsis"), str(ROOT / "prolepsis" / "vendor")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

from wordchef_prolepsis.bridge import WordChefRuntime  # noqa: E402


@pytest.fixture()
def tmp_root():
    path = Path(tempfile.mkdtemp(prefix="wc-test-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def runtime(tmp_root):
    rt = WordChefRuntime(tmp_root / "prolepsis")
    yield rt
    rt.shutdown()


@pytest.fixture()
def service(tmp_root):
    from app.service import GameService
    from app.store import Store
    store = Store(tmp_root / "test.db")
    rt = WordChefRuntime(tmp_root / "prolepsis")
    svc = GameService(store, rt)
    yield svc
    rt.shutdown()
    store.close()


@pytest.fixture()
def client(tmp_root, monkeypatch):
    from fastapi.testclient import TestClient
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", tmp_root / "api.db")
    monkeypatch.setattr(config, "PROLEPSIS_ROOT", tmp_root / "prolepsis")
    monkeypatch.setattr(config, "FRONTEND_DIR", ROOT / "frontend" / "out")
    import importlib
    import app.main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.create_app()) as test_client:
        yield test_client
