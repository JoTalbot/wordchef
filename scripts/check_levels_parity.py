#!/usr/bin/env python3
"""Cross-language parity check: Python level engine vs the TypeScript mirror.

The repo ships the level generator twice on purpose — `game/wordchef_game/
levels.py` (server/authoritative side) and `frontend/lib/levels.ts` (client
side). They must agree byte-for-byte, or a player's board would differ from
the one the server scores.

This script compiles the TS mirror with the frontend's own TypeScript and
compares its output to the Python engine for every level in
`tests/fixtures_levels_golden.json`.

    python3 scripts/check_levels_parity.py            # all fixture levels
    python3 scripts/check_levels_parity.py 1 2 3 42   # explicit levels

Exit code 0 = the two implementations agree.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
FIXTURE = ROOT / "tests" / "fixtures_levels_golden.json"
sys.path.insert(0, str(ROOT / "game"))

from wordchef_game.levels import generate_level  # noqa: E402

NODE_DRIVER = """
const levels = require(process.argv[2]);
const keys = process.argv.slice(3);
const out = {};
for (const k of keys) out[k] = levels.generateLevel(Number(k));
process.stdout.write(JSON.stringify(out));
"""


def compile_mirror(out_dir: Path) -> Path:
    tsc = FRONTEND / "node_modules" / ".bin" / "tsc"
    if not tsc.exists():
        sys.exit("frontend deps missing — run `npm install` in frontend/ first")
    subprocess.run(
        [str(tsc), str(FRONTEND / "lib" / "levels.ts"),
         "--outDir", str(out_dir), "--module", "commonjs",
         "--target", "es2020", "--skipLibCheck", "--strict"],
        check=True, cwd=FRONTEND,
    )
    return out_dir / "levels.js"


def ts_levels(keys: list[str]) -> dict:
    with tempfile.TemporaryDirectory(prefix="wc-parity-") as tmp:
        driver = Path(tmp) / "driver.js"
        driver.write_text(NODE_DRIVER, encoding="utf-8")
        levels_js = compile_mirror(Path(tmp))
        proc = subprocess.run(
            ["node", str(driver), str(levels_js), *keys],
            check=True, capture_output=True, text=True,
        )
        return json.loads(proc.stdout)


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    if argv:
        keys = argv
    else:
        keys = sorted(json.loads(FIXTURE.read_text(encoding="utf-8")), key=int)

    ts = ts_levels(keys)
    failures = []
    for k in keys:
        py = generate_level(int(k)).to_dict()
        if py != ts[k]:
            failures.append(k)

    if failures:
        print(f"✗ parity broken for levels: {', '.join(failures)}")
        k = failures[0]
        py, tsd = generate_level(int(k)).to_dict(), ts[k]
        for field in sorted(set(py) | set(tsd)):
            if py.get(field) != tsd.get(field):
                print(f"  level {k} · {field}:")
                print(f"    py: {py.get(field)}")
                print(f"    ts: {tsd.get(field)}")
        return 1

    print(f"✓ Python ↔ TypeScript parity confirmed for {len(keys)} levels "
          f"({', '.join(keys[:6])}{' …' if len(keys) > 6 else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
