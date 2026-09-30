# -*- coding: utf-8 -*-
"""
BEAM v0.6.1 — content-addressed warp storage + cloth warehouse + thrums reclamation
====================================================================================
The beam every loom winds its warp onto. Each cloth block (letterhead,
title, shared warp block, weft, compensation) is written ONCE and is
addressed by the sha256 of its content. Rewriting known content is a
complete no-op (deduplication):

  · the project letterhead — one physical block for every artifact,
    every cloth and every process of the project;
  · a document title — one block for all cloths (release notes carry the
    version in the title, so theirs is unique per cloth);
  · shared warp blocks — one block per content (version banner,
    contributors list);
  · wefts are unique (they carry dates and event facts).

An artifact is a MANIFEST (an ordered list of block addresses).
A cloth PASSPORT = {cloth, status, manifests, labels}; a submitted cloth
is a live root of the beam — the reclaimer leans on its manifests.

An IMPRINT (.md) is a materialized view of a manifest, printed on demand;
it is never the truth. The truth is the beam.

UNRAVELING = discarding manifests (0 physical deletions).

RECLAIMER (thrums reclamation): blocks that no live passport references
(remains of unraveled cloths, failed speculation, spent reserves) are
moved to thrums/ in one batch — returnable until the "wash".

MULTI-PROCESS (v0.6.1): `locked=True` guards put/submit_cloth with a real
file lock (locks.py, flock) — several OS processes may weave one beam at
once. The simulated I/O latency lives in the loom, outside the lock.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .locks import FileLock


class Beam:
    def __init__(self, root: Path, locked: bool = False):
        self.root = root
        self.objects = root / "objects"
        self.warehouse = root / "warehouse"     # passports of live cloths
        self.thrums = root / "thrums"           # yarn with no live manifest
        self.objects.mkdir(parents=True, exist_ok=True)
        self.warehouse.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(root / ".beam.lock") if locked else None
        self.writes = 0            # physical block writes performed
        self.dedup = 0             # rewrites NOT performed
        self.submitted = 0
        self.reclaimed_objects = 0
        self.reclaimed_bytes = 0

    @staticmethod
    def address(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _canon(obj: dict) -> str:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1)

    def put(self, text: str) -> tuple[str, bool]:
        """Write a block; return (address, was_new). A repeat is 0 operations.
        In a multi-process loom the check-then-write pair is guarded by a real
        file lock: two writers cannot both count "new", a reader never sees a
        half-written block."""
        if self.lock is not None:
            with self.lock:
                return self._put(text)
        return self._put(text)

    def _put(self, text: str) -> tuple[str, bool]:
        addr = self.address(text)
        path = self.objects / addr[:2] / addr
        if path.exists():
            self.dedup += 1
            return addr, False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.writes += 1
        return addr, True

    def get(self, addr: str) -> str:
        return (self.objects / addr[:2] / addr).read_text(encoding="utf-8")

    # ── warehouse: passports are the live roots of the beam ──

    def submit_cloth(self, passport: dict) -> str:
        """Submit a finished cloth to the warehouse: its passport (manifests +
        labels) becomes an addressed object of the beam. Idempotent; guarded
        by the beam lock in multi-process looms."""
        text = self._canon(passport)
        addr = self.address(text)
        path = self.warehouse / addr[:2] / addr
        if self.lock is not None:
            with self.lock:
                self._write_passport(path, text)
        else:
            self._write_passport(path, text)
        return addr

    def _write_passport(self, path: Path, text: str):
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            self.submitted += 1

    def passports(self) -> list[dict]:
        """Live roots: the passports of cloths in the warehouse."""
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(self.warehouse.rglob("*")) if p.is_file()]

    # ── reclaimer: recycle yarn with no live manifest ──

    def reclaim(self) -> dict:
        """Blocks referenced by no manifest of any live passport (remains of
        unraveled cloths, failed speculation, spent reserves) are moved to
        thrums in one batch. Physical moves happen off the hot path; until
        the wash, thrums are returnable. (Single-orchestrator operation:
        run it when no looms are weaving.)"""
        live: set[str] = set()
        referenced_by: dict[str, list[str]] = {}
        for passport in self.passports():
            who = passport.get("cloth", "?")
            for name, addrs in passport.get("manifests", {}).items():
                for a in addrs:
                    live.add(a)
                    referenced_by.setdefault(a, []).append(f"{who}/{name}")
        moved, moved_bytes = 0, 0
        if self.objects.exists():
            for path in sorted(self.objects.rglob("*")):
                if path.is_file() and path.name not in live:
                    dest = self.thrums / path.parent.name / path.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    moved_bytes += path.stat().st_size
                    path.rename(dest)
                    moved += 1
        self.reclaimed_objects += moved
        self.reclaimed_bytes += moved_bytes
        return {"reclaimed_objects": moved, "reclaimed_bytes": moved_bytes,
                "live_blocks": len(live), "references": referenced_by}

    # ── stats ──

    def stats(self) -> dict:
        files = [f for f in self.objects.rglob("*") if f.is_file()]
        thrums = ([f for f in self.thrums.rglob("*") if f.is_file()]
                  if self.thrums.exists() else [])
        return {"objects": len(files),
                "bytes": sum(f.stat().st_size for f in files),
                "writes": self.writes,
                "dedup": self.dedup,
                "passports": self.submitted,
                "reclaimed": self.reclaimed_objects,
                "reclaimed_bytes": self.reclaimed_bytes,
                "in_thrums": len(thrums)}
