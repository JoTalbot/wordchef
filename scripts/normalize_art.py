#!/usr/bin/env python3
"""Normalise `frontend/public/img` to the repo's asset conventions.

The UI never displays a dish larger than 140 px, yet early art shipped at
1024² — which blew the 4 MB bundle budget enforced by
`tests/frontend/test_smoke.py`. This script is the mechanical fix, and the
tool to reach for whenever a new batch of art lands oversized:

    python3 scripts/normalize_art.py            # report only
    python3 scripts/normalize_art.py --write    # rewrite files in place

Targets (see docs/art-pipeline.md):

    dish_* / guest_*   512²  q80   ≤ 60 KB   (displayed at 46–140 px)
    kitchen_*          512²  q80   ≤ 60 KB
    ach_*              256²  q85   ≤ 40 KB
    ui_*               256²  q85   ≤ 40 KB
    everything else    no resize, re-encode only if over 48 KB

Quality is stepped down (never below q70) until a file fits its byte budget,
so a busy banner cannot blow the budget at a fixed quality. **Idempotent:**
a file is only rewritten when the fresh encode is at least 3 % smaller than
what is already on disk — re-running never churns the whole set, and files
sitting at the quality floor are left alone instead of being re-encoded
forever.
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "frontend" / "public" / "img"

QUALITY_FLOOR = 70
MIN_GAIN = 0.97  # rewrite only if the new encode is ≥3 % smaller

# prefix -> (max side, starting quality, byte budget)
RULES: list[tuple[str, int, int, int]] = [
    ("dish_", 512, 80, 60 * 1024),
    ("guest_", 512, 80, 60 * 1024),
    ("kitchen_", 512, 80, 60 * 1024),
    ("ach_", 256, 85, 40 * 1024),
    ("ui_", 256, 85, 40 * 1024),
]

# everything else (banners, badges…): no resize, re-encode only if over budget
DEFAULT: tuple[int, int, int] = (0, 82, 48 * 1024)


def target_for(path: Path) -> tuple[int, int, int]:
    for prefix, size, quality, budget in RULES:
        if path.name.startswith(prefix):
            return size, quality, budget
    return DEFAULT


def encode_under_budget(im: Image.Image, quality: int, budget: int) -> tuple[bytes, int]:
    """Encode to JPEG in memory, stepping quality down until it fits `budget`."""
    q = quality
    while True:
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() <= budget or q <= QUALITY_FLOOR:
            return buf.getvalue(), q
        q = max(QUALITY_FLOOR, q - 4)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    before = after = 0
    written = skipped = 0

    for p in sorted(IMG.glob("*.jpg")):
        size, quality, budget = target_for(p)
        orig_bytes = p.stat().st_size
        with Image.open(p) as im:
            w, h = im.size
            needs_work = (size and (w > size or h > size)) or orig_bytes > budget
            if not needs_work:
                skipped += 1
                continue
            out = im.convert("RGB")
            if size:
                out.thumbnail((size, size), Image.LANCZOS)
            data, used_q = encode_under_budget(out, quality, budget)

        # idempotency guard: no churn when the file is already at its best
        if len(data) >= orig_bytes * MIN_GAIN:
            skipped += 1
            continue

        before += orig_bytes
        written += 1
        note = "" if len(data) <= budget else " (бюджет не достижим — пол качества)"
        if args.write:
            p.write_bytes(data)
            after += p.stat().st_size
            print(f"{p.name:26s} {w}×{h} {orig_bytes // 1024:>4}K → "
                  f"{out.size[0]}×{out.size[1]} {p.stat().st_size // 1024:>3}K q{used_q}{note}")
        else:
            print(f"{p.name:26s} {w}×{h} {orig_bytes // 1024:>4}K → "
                  f"{out.size[0]}×{out.size[1]} {len(data) // 1024:>3}K q{used_q}"
                  f" (нужна запись){note}")

    print(f"\nк обработке: {written}, уже в норме: {skipped}")
    if args.write and written:
        print(f"итого: {before // 1024} КБ → {after // 1024} КБ "
              f"(экономия {(before - after) // 1024} КБ)")
        print("дальше: cd frontend && npm run build && pytest tests/frontend")
    elif not args.write and written:
        print("это отчёт — запусти с --write, чтобы применить")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
