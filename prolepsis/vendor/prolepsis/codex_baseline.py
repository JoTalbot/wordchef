# -*- coding: utf-8 -*-
"""
CODEX BASELINE — the same release process, written the old way
================================================================
The same process as patterns/release.yaml, but as classic sequential code:
every stage creates its own file, inserts the letterhead, rewrites the
version banner and the contributors block (every single time!), and writes
the body.

Purpose — an honest baseline for comparison:
  · authoring metrics: how much code an agent must write the old way;
  · execution metrics: how many operations and how much time it takes.

Yarn helpers (human_date, fmt_num) are imported from jacquard — utilities
exist in both worlds; we compare the process code, not the libraries.

Run:  python3 codex_baseline.py   (writes documents into baseline-output/)
"""

from __future__ import annotations

import time
from pathlib import Path

from .jacquard import human_date, fmt_num, total

IO_LATENCY = 0.03   # one I/O operation, seconds (same as the loom)

PROJECT = "WEAVE"
REPO = "github.com/JoTalbot/prolepsis"
LICENSE = "MIT"
REGISTRY = "ghcr.io/jotalbot/prolepsis"
MAINTAINER = "JoTalbot"
WIDTH = 66


def _row(text: str = "") -> str:
    return "║ " + text.ljust(WIDTH - 4) + " ║"


def _letterhead(title: str) -> str:
    frame = "═" * (WIDTH - 2)
    return "\n".join([
        "╔" + frame + "╗", _row(PROJECT), _row(REPO), _row(f"{LICENSE} · {REGISTRY}"),
        "╠" + frame + "╣", _row(title), "╠" + frame + "╣",
    ])


def _pause():
    if IO_LATENCY:
        time.sleep(IO_LATENCY)


# ── classic primitives: every call is a separate operation ──

def _new_document(folder: Path, filename: str, title: str, ops: dict) -> Path:
    path = folder / filename
    path.touch()
    ops["files"] += 1
    _pause()
    with path.open("a", encoding="utf-8") as f:
        f.write(_letterhead(title) + "\n\n")
    ops["letterheads"] += 1
    _pause()
    return path


def _shared_block(path: Path, block: str, ops: dict):
    """The classic world rewrites the banner and the contributors list in
    every target document — every time, from scratch."""
    with path.open("a", encoding="utf-8") as f:
        f.write(block.rstrip() + "\n\n")
    ops["shared"] += 1
    _pause()


def _body(path: Path, text: str, ops: dict):
    with path.open("a", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n\n")
    ops["bodies"] += 1
    _pause()


# ── BASELINE: the sequential release process ──

def run_release(data: dict, folder: Path, published: bool = True) -> dict:
    """The classic stage-by-stage process. Returns the operation counter."""
    ops = {"files": 0, "letterheads": 0, "shared": 0, "bodies": 0}
    folder.mkdir(parents=True, exist_ok=True)
    rel = data["release"]
    date = data["date"]

    banner = (f"── WARP · version banner ──\n"
              f"{PROJECT} {rel} · {human_date(date)} · {LICENSE}")          # recomputed EVERY time
    contrib = ("── WARP · contributors ──\n" + "\n".join(
        f"  {i}. {c['name']} — {c['commits']} commits"
        for i, c in enumerate(data["contributors"], 1)))

    # STAGE 1: TAG_CUT → release notes (banner 1st time, contributors 1st time)
    p = _new_document(folder, "01_ReleaseNotes.md", f"RELEASE NOTES · {rel}", ops)
    _shared_block(p, banner, ops)
    _shared_block(p, contrib, ops)
    _body(p, f"Cut by {MAINTAINER} on {human_date(date)}\n"
             f"Previous release: {data['previous']} · resolves {data['issue']}", ops)

    if not published:
        _body(p, "── RELEASE CANCELED ──", ops)
        ops["total"] = sum(ops[k] for k in ("files", "letterheads", "shared", "bodies"))
        return ops

    # STAGE 2: CI_GREEN → test report + build manifest
    p = _new_document(folder, "02_TestReport.md", "CI TEST REPORT", ops)
    _body(p, "Suite: full matrix\nTests: 612 passed · 0 failed\nGate: green", ops)
    p = _new_document(folder, "03_BuildManifest.md", "BUILD MANIFEST", ops)
    dist = "\n".join(f"  {i}. {d['file']} · {fmt_num(d['size'])} KB"
                     for i, d in enumerate(data["dist"], 1))
    _body(p, f"Built from {REPO} at tag {rel}\n{dist}", ops)

    # STAGE 3: ARTIFACTS_BUILT → checksums + container notes
    p = _new_document(folder, "04_Checksums.md", "DISTRIBUTION CHECKSUMS", ops)
    sums = "\n".join(f"{d['file']} · {fmt_num(d['size'])} KB · sealed" for d in data["dist"])
    _body(p, f"{sums}\nTotal: {fmt_num(total(data['dist'], 'size'))} KB", ops)
    p = _new_document(folder, "05_DockerNotes.md", "CONTAINER IMAGE NOTES", ops)
    _body(p, f"Image: {REGISTRY}:{rel}\nBase: python:3.13-slim · layers: 7", ops)

    # STAGE 4: PUBLISHED → announcement (banner 2nd time, contributors 2nd time)
    p = _new_document(folder, "06_Announcement.md", "RELEASE ANNOUNCEMENT", ops)
    _shared_block(p, banner, ops)
    _shared_block(p, contrib, ops)
    _body(p, f"{PROJECT} {rel} is out — {REGISTRY}:{rel}", ops)

    # STAGE 5: RELEASE_SIGNED → sign-off (banner 3rd time)
    p = _new_document(folder, "07_SignOff.md", "RELEASE SIGN-OFF", ops)
    _shared_block(p, banner, ops)
    _body(p, f"Signed off by {MAINTAINER} on {human_date(date)}\n"
             f"Release {rel} is complete and reproducible", ops)

    ops["total"] = sum(ops[k] for k in ("files", "letterheads", "shared", "bodies"))
    return ops


if __name__ == "__main__":
    DATA = {
        "release": "v1.4.2",
        "previous": "v1.4.1",
        "date": "2026-09-27",
        "issue": "#42 — login timeout on session refresh",
        "contributors": [
            {"name": "JoTalbot", "commits": 214},
            {"name": "Iryna Tkachuk", "commits": 96},
        ],
        "dist": [
            {"file": "weave-1.4.2.tar.gz", "size": 148},
            {"file": "weave-1.4.2-py3-none-any.whl", "size": 152},
        ],
    }
    result = run_release(DATA, Path(__file__).resolve().parent / "baseline-output")
    print("codex baseline finished:", result)
