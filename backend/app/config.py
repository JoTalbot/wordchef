"""Word Chef backend — configuration and paths."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # repo root
DATA_DIR = Path(os.environ.get("WC_DATA_DIR", ROOT / "data"))
FRONTEND_DIR = ROOT / "frontend" / "out"        # Next.js static export
DB_PATH = Path(os.environ.get("WC_DB_PATH", DATA_DIR / "wordchef.db"))
PROLEPSIS_ROOT = DATA_DIR / "prolepsis"
SEASON = os.environ.get("WC_SEASON", "season-1")

DATA_DIR.mkdir(parents=True, exist_ok=True)

for extra in (str(ROOT / "game"), str(ROOT / "prolepsis")):
    import sys
    if extra not in sys.path:
        sys.path.insert(0, extra)
