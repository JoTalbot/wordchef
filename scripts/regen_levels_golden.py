#!/usr/bin/env python3
"""Regenerate tests/fixtures_levels_golden.json from the Python level engine.

The fixture is the contract between `game/wordchef_game/levels.py` and its
1:1 TypeScript mirror `frontend/lib/levels.ts`. Regenerate it **only** when
content changes on purpose (new dishes, new words, changed pools) — never to
make a failing parity test pass:

    python3 scripts/regen_levels_golden.py            # sanity-check only
    python3 scripts/regen_levels_golden.py --write    # rewrite the fixture

After a rewrite, always run the cross-language parity check:

    python3 scripts/check_levels_parity.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "game"))

from wordchef_game.levels import generate_level  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures_levels_golden.json"
# Extra levels lock the tail of the campaign (60+ levels apart, all kitchens).
EXTRA_KEYS = (500, 999, 1000)


def build() -> dict:
    keys = {int(k) for k in json.loads(FIXTURE.read_text(encoding="utf-8"))}
    keys |= set(EXTRA_KEYS)
    return {str(k): generate_level(k).to_dict() for k in sorted(keys)}


def render(data: dict) -> str:
    """Same formatting the fixture has always used: indent=1, sorted keys."""
    return json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="rewrite the fixture")
    args = ap.parse_args()

    current = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fresh = build()

    diffs = [k for k in sorted(fresh, key=int)
             if current.get(k) != fresh[k]]
    if not diffs:
        print(f"fixture is up to date ({len(fresh)} levels)")
        return 0

    print(f"levels differing from the fixture: {', '.join(diffs)}")
    for k in diffs:
        old = current.get(k, {})
        print(f"  level {k}: dish {old.get('dish')} → {fresh[k]['dish']}"
              if old.get("dish") != fresh[k]["dish"] else
              f"  level {k}: board/bonus changed")

    if not args.write:
        print("\nrunning in check mode — pass --write to rewrite the fixture")
        return 1

    FIXTURE.write_text(render(fresh), encoding="utf-8")
    print(f"\nwrote {FIXTURE.relative_to(ROOT)} ({len(fresh)} levels)")
    print("now run: python3 scripts/check_levels_parity.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
