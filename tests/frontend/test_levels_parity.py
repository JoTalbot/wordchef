"""Cross-language parity: the TypeScript level mirror must match the Python engine.

`tests/unit/test_levels.py` proves the Python generator is stable against the
golden fixture; this test proves the *other* implementation agrees with it by
compiling `frontend/lib/levels.ts` with the repo's own TypeScript and diffing
the output. Skipped (not failed) when node or frontend deps are unavailable —
`scripts/check_levels_parity.py` is the same check as a manual entry point.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
TSC = FRONTEND / "node_modules" / ".bin" / "tsc"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not TSC.exists(),
    reason="node + frontend deps are required for the TS mirror check",
)


class TestLevelParity:
    def test_ts_mirror_matches_python_engine(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_levels_parity.py")],
            capture_output=True, text=True, cwd=ROOT,
        )
        assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    def test_parity_script_reports_specific_levels(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_levels_parity.py"), "42"],
            capture_output=True, text=True, cwd=ROOT,
        )
        assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
        assert "42" in proc.stdout
