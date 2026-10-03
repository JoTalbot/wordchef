#!/usr/bin/env python3
"""Migrate `frontend/public/img` from JPEG to WebP, rewriting every reference.

WebP carries the same visible quality at roughly half the bytes, and the
Android build ships the whole `frontend/out` tree inside the APK, so the win
is worth a repo-wide mechanical pass. Run once (or whenever new JPEG art has
landed):

    python3 scripts/to_webp.py            # dry run: what would change
    python3 scripts/to_webp.py --write    # convert, rewrite refs, delete .jpg

What it touches:

* `frontend/public/img/*.jpg`   → `*.webp` (quality searched to fit the budget)
* references in `frontend/**/*.{ts,tsx,css}` — including template-literal
  paths such as `` `img/kitchen_${id}.jpg` ``, which a naive string replace
  would miss;
* afterwards it re-scans the code for `img/…` references and fails loudly if
  anything points at a file that does not exist.

`icon.png` (the Next.js app icon route) is intentionally left alone.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "frontend" / "public" / "img"
CODE_GLOBS = ("frontend/**/*.tsx", "frontend/**/*.ts", "frontend/**/*.css")
DOCS = (ROOT / "docs", ROOT / "README.md")

REF_RE = re.compile(r"img/[A-Za-z0-9_${}.\-]*?\.jpg")
QUALITY_FLOOR = 70
REF_START = "img/"


def encode(im: Image.Image, budget: int, start_q: int = 82) -> tuple[bytes, int]:
    q = start_q
    while True:
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=q, method=6)
        if buf.tell() <= budget or q <= QUALITY_FLOOR:
            return buf.getvalue(), q
        q = max(QUALITY_FLOOR, q - 5)


def code_files() -> list[Path]:
    out: list[Path] = []
    for pattern in CODE_GLOBS:
        out += [p for p in ROOT.glob(pattern) if "node_modules" not in p.parts
                and "out" not in p.parts and ".next" not in p.parts]
    return sorted(set(out))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    jpgs = sorted(IMG.glob("*.jpg"))
    if not jpgs:
        print("JPEG-файлов нет — миграция уже выполнена")
        return 0

    before = after = 0
    for p in jpgs:
        before += p.stat().st_size
        with Image.open(p) as im:
            out = im.convert("RGB")
            # aim for ~60 % of the JPEG we are replacing, capped by budget
            cap = 60 * 1024 if max(im.size) > 384 else 40 * 1024
            budget = min(cap, max(int(p.stat().st_size * 0.6), 12 * 1024))
            data, q = encode(out, budget)
        after += len(data)
        dst = p.with_suffix(".webp")
        print(f"{p.name:26s} {p.stat().st_size // 1024:>4}K → "
              f"{dst.name:26s} {len(data) // 1024:>3}K q{q}"
              + ("" if len(data) <= budget else "  (бюджет не достижим)"))
        if args.write:
            dst.write_bytes(data)
            p.unlink()

    print(f"\nитого: {before // 1024} КБ → {after // 1024} КБ "
          f"(−{100 - after * 100 // before} %)")

    # ── rewrite references ────────────────────────────────────────────────
    changed_files = 0
    for f in code_files():
        src = f.read_text(encoding="utf-8")
        new = REF_RE.sub(lambda m: m.group(0)[:-4] + ".webp", src)
        if new != src:
            changed_files += 1
            print(f"ссылки обновлены: {f.relative_to(ROOT)}")
            if args.write:
                f.write_text(new, encoding="utf-8")

    # ── verify: every literal reference resolves ─────────────────────────
    missing: list[str] = []
    for f in code_files():
        src = f.read_text(encoding="utf-8")
        for ref in re.findall(r"img/[A-Za-z0-9_.\-]+\.[a-z]{3,4}", src):
            if "${" in ref:
                continue
            if not (ROOT / "frontend" / "public" / ref).is_file():
                missing.append(f"{f.relative_to(ROOT)} → {ref}")
    if missing:
        print("\n✗ висячие ссылки:")
        for m in missing:
            print("   ", m)
        return 1

    dynamic = sorted({r for f in code_files()
                      for r in re.findall(r"img/[A-Za-z0-9_${}.\-]*?\.\w{3,4}", f.read_text(encoding="utf-8"))
                      if "${" in r})
    if dynamic:
        print("динамические ссылки (проверь вручную):", ", ".join(dynamic))

    if not args.write:
        print("\nэто прогон без записи — запусти с --write")
    else:
        print(f"\nготово: {len(jpgs)} файлов сконвертировано, "
              f"ссылки в {changed_files} файлах")
        print("дальше: python3 scripts/normalize_art.py && "
              "cd frontend && npm run build && pytest tests/frontend")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
