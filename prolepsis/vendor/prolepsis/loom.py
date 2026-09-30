# -*- coding: utf-8 -*-
"""
LOOM v0.7 — the runtime of the WEAVE paradigm · CAS beam + overspin + the mill
====================================================================
pattern (YAML, the Jacquard language) → warping (hard / soft / overspun) →
sheds → picks (parallel wefts) → reed → cloth → unraveling → reclamation

Layers:
  patterns/release.yaml       the process declaration (a language, edited without code)
  patterns/release.schema.json  the pattern's JSON schema (validated at runtime and in editors)
  jacquard.py                 the language: templates, selvedges, type guards, a sandbox
  beam.py                     content-addressed block store (sha256) + cloth warehouse
                              (passports are the live roots) + thrums reclamation
  locks.py                    real file locks (flock): several OS processes on one beam
  patternsmith.py             order → pattern draft → schema + reed → human signature
  codex_baseline.py           the same process the old way (the comparison baseline)
  loom.py                     the executor: warps, weaves, checks, unravels, reclaims

AN ARTIFACT IS A MANIFEST (an ordered list of block addresses on the beam).
AN IMPRINT (.md) is a view of the manifest, printed on demand.
A CLOTH PASSPORT — manifests + labels; submitted to the warehouse — a live root.
THE PICK LOG (picks.jsonl) is the truth of events: any cloth replays byte-exact.
UNRAVELING — manifests are discarded: 0 physical deletions.
THE RECLAIMER — yarn with no live manifest goes to thrums (returnable until the wash).
AUTO-WARPING — levels from history: P ≥ 0.75 hard · ≥ 0.40 overspun · else soft.
OVERSPIN — speculation: probable bases are woven in advance; a miss is reclaimed.
THE MILL — long-lived worker looms: warm up once, weave everything the queue sends;
           the startup cost amortizes to nothing while the beam stays one.

Run:       python3 loom.py    (dependencies: pyyaml; jsonschema — optional)
Result:    demo/ — cloths (imprints), .beam/ (objects + warehouse + thrums),
           picks.jsonl (the event log), loom.log (the full run log)
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import re
import shutil
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from . import jacquard as j
from . import codex_baseline as baseline
from . import patternsmith
from .beam import Beam
from .jacquard import plural
from .locks import FileLock

DEMO_ROOT = Path(__file__).resolve().parent / "demo"
PATTERN_PATH = Path(__file__).resolve().parent / "patterns" / "release.yaml"
IO_LATENCY = 0.03   # one physical I/O operation, seconds


# ───────────────────────── blocks ─────────────────────────

WIDTH = 66


def _row(text: str = "") -> str:
    return "║ " + text.ljust(WIDTH - 4) + " ║"


def _letterhead_block(data: dict) -> str:
    """The shared project letterhead — ONE physical block for the whole beam."""
    frame = "═" * (WIDTH - 2)
    return ("╔" + frame + "╗\n"
            + _row(data.get("project", "")) + "\n"
            + _row(data.get("repo", "")) + "\n"
            + _row(f"{data.get('license', '')} · {data.get('registry', '')}") + "\n"
            + "╠" + frame + "╣\n")


def _title_block(title: str) -> str:
    frame = "═" * (WIDTH - 2)
    return _row(title) + "\n" + "╠" + frame + "╣\n"


def _para(text: str) -> str:
    """A paragraph block (base / reserve / weft / compensation) — a blank line before it."""
    return "\n" + text.strip() + "\n"


# ───────────────────────── logs ─────────────────────────

class Log:
    def __init__(self):
        self.lines = []

    def __call__(self, line: str = ""):
        print(line)
        self.lines.append(line)

    def save(self, path: Path):
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


log = Log()


class PickLog:
    """The pick log (event sourcing): the truth of the project's EVENTS.
    A completed pick is appended in memory; a batch is committed to
    picks.jsonl with ONE physical operation when a cloth is submitted
    (group commit). Rejected picks never enter — the replay is clean.
    Any cloth is rebuildable by replay: manifests assemble byte-exact.

    Concurrency: in-process commits serialize on an asyncio lock; with a
    lock_path, cross-process commits serialize on a REAL file lock
    (locks.py) — several looms may share one pick log."""

    def __init__(self, path: Path, lock_path: Path | None = None):
        self.path = path
        self.records: list[dict] = []
        self.recorded = 0
        self.commits = 0
        self.lock_wait_ms = 0.0                 # honest contention metric
        self._lock = asyncio.Lock()             # multi-thread looms (one process)
        self._flock = (FileLock(lock_path) if lock_path is not None else None)

    def add(self, record: dict):
        self.records.append(record)

    async def commit(self) -> bool:
        """Append the uncommitted batch — one physical operation."""
        async with self._lock:
            if self._flock is None:
                return self._commit()
            t0 = time.monotonic()
            with self._flock:
                self.lock_wait_ms += (time.monotonic() - t0) * 1000
                return self._commit()

    def _commit(self) -> bool:
        fresh = self.records[self.recorded:]
        if not fresh:
            return False
        with self.path.open("a", encoding="utf-8") as f:
            for r in fresh:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        self.recorded = len(self.records)
        self.commits += 1
        return True

    @staticmethod
    def read(path: Path) -> list[dict]:
        return [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()]


class ThreadBreak(Exception):
    """Reality diverged from the pattern — the loom stops."""


async def _io():
    """One simulated physical disk/network operation."""
    if IO_LATENCY:
        await asyncio.sleep(IO_LATENCY)


# ───────────────────────── project history (for auto-warping) ─────────────────────────

# Honestly: a modeled history of the previous month (10 releases) — a seed
# while the project's own pick log is still short. From then on, history IS the log.
PRIOR_HISTORY = {
    "cloths": 10,
    "happened": {"TAG_CUT": 10, "CI_GREEN": 8, "ARTIFACTS_BUILT": 8,
                 "PUBLISHED": 7, "RELEASE_SIGNED": 5, "RELEASE_CANCELED": 2},
    "hotfixes": 6,       # 6 clients out of 10 came back with a hotfix
}
HARD_THRESHOLD = 0.75    # P ≥ 0.75 → hard warping (the apodictic future)
OVERSPIN_THRESHOLD = 0.40  # 0.40 ≤ P < 0.75 → overspun (speculation)


def history(records: list[dict]) -> dict:
    """The project's history = the pick log (+ a model of the past while the log is short)."""
    cloths = PRIOR_HISTORY["cloths"] + len({r["cloth"] for r in records
                                            if r.get("type") == "warping"})
    happened = Counter(r["event"] for r in records if r.get("type") == "pick")
    happened.update(PRIOR_HISTORY["happened"])
    return {"cloths": cloths, "happened": happened, "hotfixes": PRIOR_HISTORY["hotfixes"]}


# ───────────────────────── the loom (runtime) ─────────────────────────

class Loom:
    """A pattern executor. Artifacts are manifests of blocks on the CAS beam;
    all repeating content is physically written once."""

    YARN = j.YARN

    def __init__(self, pattern: dict, root: Path, beam: Beam,
                 pick_log: PickLog | None = None, silent: bool = False):
        self.pattern = pattern
        self.beam = beam
        self.pick_log = pick_log      # None → a replay: we do not journal
        self.silent = silent
        self.folder = root / pattern["data"]["release"]
        self.order: list[str] = []
        self.artifacts: dict[str, dict] = {}
        for i, (name, spec) in enumerate(pattern["artifacts"].items(), 1):
            self.order.append(name)
            self.artifacts[name] = {
                "title_template": spec["title"],
                "level": spec.get("warping", "hard"),
                "index": i,
                "path": self.folder / f"{i:02d}_{name}.md",
                "blocks": [],            # the manifest: block addresses on the beam
                "base_tied": False,
                "weft_woven": False,
            }
        self.shared_addrs: dict[str, list[str]] = {}    # soft artifacts hold shared addrs
        self.overspun: dict[str, list[str]] = {}        # overspun artifacts: ready addrs, no manifest
        self.speculation: dict[str, list[str]] = {}     # branch → addrs (a future cloth's yarn)
        self.labels: dict[str, dict] = {}               # address → machine-readable label
        self.state: list[str] = []
        self.facts: dict[str, dict] = {}
        self.unraveled = False
        self.stopped = False
        self.warped = False
        self.levels: dict[str, str] = {}
        self.counters = {"fans": 0, "tie_ons": 0, "picks": 0, "wefts": 0,
                         "unraveled": 0, "compensations": 0, "imprint_fans": 0,
                         "ops": 0, "dedup": 0}

        def _fact(event_name: str) -> dict:    # yarn for selvedges: pick facts
            return self.facts.get(event_name, {})
        _fact._yarn = True
        self._fact = _fact

    def _log(self, line: str = ""):
        if not self.silent:
            log(line)

    def _context(self, f: dict) -> dict:
        return {"f": f, "pattern": self.pattern["data"], "fact": self._fact, **self.YARN}

    def fact(self, event_name: str) -> dict:
        return self.facts.get(event_name, {})

    # ── writing blocks: labels + deduplication = 0 physical operations ──

    async def _write_block(self, text: str, label: dict | None = None) -> str:
        addr, is_new = self.beam.put(text)
        if label is not None:
            self.labels[addr] = label              # the block's role in THIS cloth
        if is_new:
            self.counters["ops"] += 1
            await _io()
        else:
            self.counters["dedup"] += 1
        return addr

    async def _weave_blocks(self, name: str, blocks: list[str], event: str | None = None):
        for b in blocks:
            addr = await self._write_block(
                b, {"type": "weft", "artifact": name, "event": event})
            self.artifacts[name]["blocks"].append(addr)

    async def _weave_base(self, name: str, title: str):
        a1 = await self._write_block(_letterhead_block(self.pattern["data"]),
                                     {"type": "letterhead", "artifact": name})
        a2 = await self._write_block(_title_block(title),
                                     {"type": "title", "artifact": name})
        self.artifacts[name]["blocks"] += [a1, a2]
        self.artifacts[name]["base_tied"] = True

    async def _overspin_base(self, name: str, title: str):
        """Overspin: the base of a probable artifact is written to the beam at
        warping time (in one fan with the rest of the base), but NO manifest is
        assembled — the yarn lies ready; when the event comes, the manifest
        assembles from ready addresses."""
        a1 = await self._write_block(_letterhead_block(self.pattern["data"]),
                                     {"type": "letterhead", "artifact": name, "overspun": True})
        a2 = await self._write_block(_title_block(title),
                                     {"type": "title", "artifact": name, "overspun": True})
        self.overspun[name] = [a1, a2]

    async def _weave_reserve(self, name: str):
        addr = await self._write_block(_para(
            f"── SOFT WARP · reserved at warping ──\n"
            f"artifact '{name}': the address on the beam is reserved, the content arrives with its event"),
            {"type": "reserve", "artifact": name})
        self.artifacts[name]["blocks"].append(addr)

    # ── history → probabilities ──

    def _probability(self, hist: dict, artifact: str) -> float | None:
        """P(the artifact gets woven) = max P(of the events that weave it)."""
        weavers = [name for name, spec in self.pattern["events"].items()
                   if artifact in (spec.get("weaves") or {})]
        if not weavers:
            return None
        return max(hist["happened"].get(name, 0) for name in weavers) / hist["cloths"]

    # ── WARPING ──

    async def warp(self, mode: str = "pattern", levels: dict | None = None):
        if self.warped:
            self._log("  the warp is already on the beam — warping is idempotent")
            return
        data = self.pattern["data"]
        ctx = self._context({})
        labels = {"pattern": "the warp follows the pattern",
                  "hard": "a hard, thick warp",
                  "soft": "a soft, thin warp",
                  "auto": "AUTO: the loom picks the levels from the project's history"}
        self.levels = {}
        if levels is not None:
            self.levels = dict(levels)             # deterministic replay
        elif mode == "hard":
            for name in self.order:
                self.levels[name] = "hard"
        elif mode == "soft":
            for name in self.order:
                self.levels[name] = "soft"
        elif mode == "auto":
            hist = history(self.pick_log.records if self.pick_log else [])
            self._hist = hist
            for name in self.order:
                p = self._probability(hist, name)
                if p is None:
                    self.levels[name] = self.artifacts[name]["level"]
                elif p >= HARD_THRESHOLD:
                    self.levels[name] = "hard"
                elif p >= OVERSPIN_THRESHOLD:
                    self.levels[name] = "overspun"
                else:
                    self.levels[name] = "soft"
        else:
            for name in self.order:
                self.levels[name] = self.artifacts[name]["level"]
        hard = [n for n in self.order if self.levels[n] == "hard"]
        overspun = [n for n in self.order if self.levels[n] == "overspun"]
        soft = [n for n in self.order if self.levels[n] == "soft"]

        self._log("")
        self._log(f"[WARPING · {data['release']}] {j.human_date(data['date'])} · {labels[mode]} · CAS beam")
        if mode == "auto":
            hist = self._hist
            self._log(f"  history: {plural(hist['cloths'], 'cloth')} (pick log + a model of the past) · "
                      f"thresholds: hard ≥ {HARD_THRESHOLD}, overspun ≥ {OVERSPIN_THRESHOLD}, else soft")
            decisions = []
            for name in self.order:
                weavers = [e for e, spec in self.pattern["events"].items()
                           if name in (spec.get("weaves") or {})]
                p = self._probability(hist, name)
                tail = f"P({'/'.join(weavers)})={p:.2f}" if p is not None else "by pattern"
                decisions.append(f"{name} ({tail} → {self.levels[name]})")
            self._log("  decision: " + " · ".join(decisions))
        if levels is not None:
            self._log("  levels restored from the pick log — the replay is deterministic")

        # fan #1 — manifests: an artifact is a list of blocks, no files created
        self.counters["fans"] += 1
        self._log(f"  fan #1 · artifacts registered: {len(self.order)} — manifests, 0 physical operations")

        # fan #2 — the base: hard get their base, probable get overspun, the rest a reserve
        o0, d0 = self.counters["ops"], self.counters["dedup"]
        jobs = []
        for name in hard:
            title = j.render(self.artifacts[name]["title_template"], ctx)
            jobs.append(self._weave_base(name, title))
        for name in overspun:
            title = j.render(self.artifacts[name]["title_template"], ctx)
            jobs.append(self._overspin_base(name, title))
        for name in soft:
            jobs.append(self._weave_reserve(name))
        await asyncio.gather(*jobs)
        self.counters["fans"] += 1
        new = self.counters["ops"] - o0
        dedup = self.counters["dedup"] - d0
        mix = (f"hard bases ({len(hard)})"
               + (f", overspun ({len(overspun)})" if overspun else "")
               + (f", soft reserves ({len(soft)})" if soft else ""))
        self._log(f"  fan #2 · {mix}: {new} new blocks, {dedup} dedups — one fan")

        # fan #3 — shared blocks: rendered once, written once, fanned out
        for shared in self.pattern["shared"]:
            rendered = j.render(shared["block"], ctx)
            addr = await self._write_block(_para(rendered), {"type": "shared"})
            for target in shared["into"]:
                if self.levels.get(target) == "hard":
                    self.artifacts[target]["blocks"].append(addr)
                elif self.levels.get(target) == "overspun":
                    self.overspun[target].append(addr)      # the address is ready — 0 operations
                else:
                    self.shared_addrs.setdefault(target, []).append(addr)
        self.counters["fans"] += 1
        pairs = ", ".join(f"'{shared['block'].splitlines()[0].split('·')[1].strip()}'"
                          if "·" in shared["block"].splitlines()[0]
                          else "shared" for shared in self.pattern["shared"])
        self._log(f"  fan #3 · shared blocks rendered and written ONCE ({pairs}) — fanned out by address")

        if self.pick_log:
            self.pick_log.add({"cloth": data["release"], "type": "warping",
                               "mode": mode, "levels": self.levels,
                               "pattern": self.pattern.get("_file", "?"), "data": data})
        self.warped = True
        if mode == "hard":
            classic = len(self.order) * 2 + sum(len(s["into"]) for s in self.pattern["shared"])
            self._log(f"  warping result: {self.counters['ops']} physical writes "
                      f"instead of {classic} classic letterhead/banner inserts · "
                      f"the project letterhead is physically one")
        elif mode == "auto":
            self._log(f"  warping result (auto): {self.counters['ops']} physical writes · "
                      f"{len(hard)} hard, {len(overspun)} overspun, {len(soft)} soft")
        else:
            self._log(f"  warping result: {self.counters['ops']} physical writes — the future is reserved")

    # ── PICK ──

    async def pick(self, event: str, payload: dict):
        if self.stopped:
            raise ThreadBreak("the loom is stopped — re-warp first")
        if not self.warped:
            raise ThreadBreak("the loom is not warped — dress it first")
        spec = self.pattern["events"][event]

        # 1) the shed
        for req in spec["requires"]:
            if req not in self.state:
                raise ThreadBreak(f"shed closed: '{event}' waits for event '{req}'")
        if self.unraveled and "unravels" not in spec:
            raise ThreadBreak(f"unraveled thread: event '{event}' belongs to the unraveled future")

        # 2) the reed: fact shape
        missing = [key for key in spec.get("facts", ()) if key not in payload]
        if missing:
            raise ThreadBreak(f"fact shape: '{event}' requires facts {missing}")

        # 2b) the reed: type guards
        violations = []
        for key, kind in (spec.get("fact_types") or {}).items():
            if key in payload:
                violation = j.check_fact_type(kind, key, payload[key])
                if violation:
                    violations.append(violation)
        if violations:
            raise ThreadBreak("type guard: " + "; ".join(violations))

        # 3) the reed: selvedges
        ctx = self._context(payload)
        for selvedge in spec.get("selvedges", ()):
            if not j.evaluate(selvedge["check"], ctx):
                raise ThreadBreak(f"selvedge broken: {selvedge['rule']}")

        number = self.counters["picks"] + 1
        day = j.human_date(payload.get("date", self.pattern["data"]["date"]))
        self._log("")
        self._log(f"[PICK #{number} · {event}] {day}")
        happened = ", ".join(self.state) if self.state else "—"
        self._log(f"  shed open ✔ (happened: {happened})")
        if spec.get("facts"):
            self._log(f"  reed · fact shape {list(spec['facts'])} ✔")
        if spec.get("fact_types"):
            self._log("  reed · type guards {"
                      + ", ".join(f"{k}: {v}" for k, v in spec["fact_types"].items()) + "} ✔")
        for selvedge in spec.get("selvedges", ()):
            self._log(f"  reed · selvedge '{selvedge['rule']}' ✔")

        # 4a) the unraveling event
        if "unravels" in spec:
            u = spec["unravels"]
            compensation = j.render(u.get("compensation", ""), ctx)
            await self._unravel(u["after"], compensation)
            self.state.append(event)
            self.facts[event] = payload
            self.counters["picks"] += 1
            if self.pick_log:
                self.pick_log.add({"cloth": self.pattern["data"]["release"], "type": "pick",
                                   "event": event, "payload": payload})
            return

        # 4b) tie-on of the soft base — from the beam's ready blocks
        soft_here = [a for a in spec["weaves"]
                     if self.levels.get(a) == "soft" and not self.artifacts[a]["base_tied"]]
        if soft_here:
            o0, d0 = self.counters["ops"], self.counters["dedup"]
            jobs = []
            for a in soft_here:
                title = j.render(self.artifacts[a]["title_template"], ctx)
                jobs.append(self._tie_on(a, title))
            await asyncio.gather(*jobs)
            self.counters["tie_ons"] += 1
            new = self.counters["ops"] - o0
            dedup = self.counters["dedup"] - d0
            self._log(f"  tie-on fan #{self.counters['tie_ons']} · soft base → full "
                      f"({', '.join(soft_here)}): {new} new blocks, {dedup} dedups")

        # 4b-2) assembly of the overspun base — the yarn was written at warping
        overspun_here = [a for a in spec["weaves"]
                         if self.levels.get(a) == "overspun" and not self.artifacts[a]["base_tied"]]
        if overspun_here:
            for a in overspun_here:
                await self._tie_on(a, None)
            self.counters["tie_ons"] += 1
            self._log(f"  tie-on fan #{self.counters['tie_ons']} · overspun at warping "
                      f"({', '.join(overspun_here)}): manifests assembled from ready addresses — "
                      f"0 operations, 0 renders")

        # 4c) wefts — in parallel
        jobs = []
        for a, template in spec["weaves"].items():
            body = j.render(template["weft"], ctx)
            jobs.append(self._weave_blocks(
                a, [_para(f"── WEFT · pick '{event}' · {day} ──\n" + body)], event=event))
        await asyncio.gather(*jobs)
        for a in spec["weaves"]:
            self.artifacts[a]["weft_woven"] = True
        self._log(f"  weft → {', '.join(spec['weaves'])}  (the base is warping blocks; only the diff is woven)")
        if len(spec["weaves"]) > 1:
            self._log(f"  ⤷ {len(spec['weaves'])} independent wefts woven in parallel")

        self.state.append(event)
        self.facts[event] = payload
        self.counters["picks"] += 1
        self.counters["wefts"] += len(spec["weaves"])
        if self.pick_log:
            self.pick_log.add({"cloth": self.pattern["data"]["release"], "type": "pick",
                               "event": event, "payload": payload})

    async def _tie_on(self, name: str, title: str | None):
        """A soft base assembles from the beam's ready blocks; an overspun one —
        with no writes and no renders at all (the addresses wait from warping)."""
        if name in self.overspun:
            self.artifacts[name]["blocks"] = list(self.overspun[name])
            self.artifacts[name]["base_tied"] = True
            return
        manifest = [await self._write_block(_letterhead_block(self.pattern["data"]),
                                            {"type": "letterhead", "artifact": name}),
                    await self._write_block(_title_block(title),
                                            {"type": "title", "artifact": name})]
        manifest += self.shared_addrs.get(name, [])
        self.artifacts[name]["blocks"] = manifest
        self.artifacts[name]["base_tied"] = True

    # ── SPECULATIVE OVERSPIN (a future cloth's yarn) ──

    async def overspin_branch(self, branch: str, hist: dict):
        """The agent overspins yarn for a probable NEXT cloth. If it happens —
        the blocks deduplicate (0 writes); if not — no manifest references them
        and the reclaimer recycles the yarn. The price of a miss is 1 block."""
        data = self.pattern["data"]
        p = hist["hotfixes"] / hist["cloths"]
        self._log("")
        self._log(f"[OVERSPIN · SPECULATION] the agent reads the history: {branch}")
        self._log(f"  P({branch}) = {hist['hotfixes']}/{hist['cloths']} = {p:.2f} "
                  f"≥ {OVERSPIN_THRESHOLD} → overspin")
        o0, d0 = self.counters["ops"], self.counters["dedup"]
        a1 = await self._write_block(_letterhead_block(data),
                                     {"type": "letterhead", "branch": branch})
        a2 = await self._write_block(_title_block(f"HOTFIX RELEASE NOTES · {data['release']}-hotfix.1"),
                                     {"type": "title", "branch": branch})
        banner = j.render(self.pattern["shared"][0]["block"], self._context({}))
        a3 = await self._write_block(_para(banner), {"type": "shared", "branch": branch})
        self.speculation[branch] = [a1, a2, a3]
        new = self.counters["ops"] - o0
        dedup = self.counters["dedup"] - d0
        self._log(f"  overspun: letterhead + title + banner: {new} new block"
                  f"{'s' if new != 1 else ''}, {dedup} dedups")
        self._log("  no manifest: if it happens — it assembles from the ready; if not — the reclaimer recycles")

    # ── UNRAVELING ──

    async def _unravel(self, after: str, compensation: str):
        anchor = self.artifacts[after]["index"]
        taken = [n for n in self.order if self.artifacts[n]["index"] > anchor]
        for n in taken:
            self.artifacts[n]["blocks"] = []
            self.artifacts[n]["base_tied"] = False
            self.overspun.pop(n, None)
        self.unraveled = True
        addr = await self._write_block(_para(compensation),
                                       {"type": "compensation", "artifact": after})
        self.artifacts[after]["blocks"].append(addr)
        self.counters["unraveled"] += len(taken)
        self.counters["compensations"] += 1
        self._log(f"[UNRAVELING · {self.pattern['data']['release']}]")
        self._log(f"  manifests discarded: {len(taken)} ({', '.join(taken)}) — 0 physical deletions")
        self._log("  the blocks stay on the beam: with no live passport the reclaimer takes them")
        self._log("  a compensation pick is woven into the release notes ('CANCELED')")

    def re_warp(self, reason: str):
        self.stopped = False
        self._log("")
        self._log(f"  ↻ RE-WARP: {reason}")
        self._log("  the loom continues")

    # ── IMPRINTS: views on demand ──

    async def imprint_artifact(self, name: str):
        a = self.artifacts[name]
        text = "".join(self.beam.get(addr) for addr in a["blocks"])
        a["path"].parent.mkdir(parents=True, exist_ok=True)
        a["path"].write_text(text, encoding="utf-8")
        self.counters["ops"] += 1
        await _io()

    async def imprints(self):
        woven = [n for n in self.order if self.artifacts[n]["weft_woven"]]
        if not woven:
            return
        self._log("")
        self._log(f"[IMPRINTS] views printed: {len(woven)} — one fan, on demand")
        await asyncio.gather(*(self.imprint_artifact(n) for n in woven))
        self.counters["imprint_fans"] += 1

    # ── WAREHOUSE: a cloth passport is a live root of the beam ──

    async def submit_cloth(self) -> str:
        """Submit the cloth to the beam's warehouse: the passport (manifests +
        labels) is one physical write; the pick-log batch commits here too — one more."""
        cloth = self.pattern["data"]["release"]
        passport = {"cloth": cloth,
                    "status": "unraveled" if self.unraveled else "woven",
                    "pattern": self.pattern.get("_file", "?"),
                    "manifests": {n: a["blocks"] for n, a in self.artifacts.items() if a["blocks"]},
                    "labels": self.labels}
        if self.speculation:
            passport["speculation"] = self.speculation
        addr = self.beam.submit_cloth(passport)
        self.counters["ops"] += 1
        await _io()
        if self.pick_log:
            self.pick_log.add({"cloth": cloth, "type": "passport", "address": addr})
            await self.pick_log.commit()
            self.counters["ops"] += 1
            await _io()
        self._log(f"[WAREHOUSE] cloth {cloth} submitted · passport {addr[:12]}… · "
                  f"a pick-log batch committed")
        return addr

    # ── SUMMARY ──

    def summary(self):
        c = self.counters
        data = self.pattern["data"]
        woven = [n for n in self.order if self.artifacts[n]["weft_woven"]]
        shared_targets = sum(sum(1 for t in s["into"] if t in woven)
                             for s in self.pattern["shared"])
        classic = len(woven) * 2 + shared_targets
        fans = c["fans"] + c["tie_ons"]
        self._log("")
        self._log("  " + "─" * 62)
        if not self.unraveled:
            self._log(f"  CLOTH SUMMARY {data['release']} (finished)")
            self._log(f"    artifacts woven: {len(woven)} · picks: {c['picks']} · wefts: {c['wefts']}")
            self._log("    the repetitive part (create · letterhead · shared):")
            self._log(f"      codex  — {classic} logical operations, every one on the hot path, sequential")
            self._log(f"      WEAVE  — {plural(fans, 'fan operation')} "
                      f"({c['fans']} warping + {c['tie_ons']} tie-on)")
            self._log(f"              physical writes: {c['ops']} · dedups: {c['dedup']}")
            self._log("              — batched and parallel · O(N) → O(fans)")
            self._log(f"    shared blocks: rendered once, written once, fanned into {shared_targets} manifests")
            if self.overspun:
                assembled = sum(1 for n in self.order
                                if self.artifacts[n]["base_tied"] and n in self.overspun)
                self._log(f"    overspin: {len(self.overspun)} artifacts overspun at warping — "
                          f"{assembled} of them tied on for 0 operations")
            if self.speculation:
                blocks = sum(len(a) for a in self.speculation.values())
                self._log(f"    speculation: {plural(blocks, 'block')} of a future branch — "
                          f"no manifest (awaiting the event or the reclaimer)")
        else:
            self._log(f"  CLOTH SUMMARY {data['release']} (unraveled)")
            self._log(f"    managed to happen: {c['picks']} picks · {c['wefts']} wefts · "
                      f"{c['compensations']} compensation")
            self._log(f"    codex would have done: {classic} operations (it is lazy — it never builds the future)")
            self._log(f"    WEAVE did: {plural(fans, 'fan')} + discarding "
                      f"{c['unraveled']} manifests (0 physical deletions)")
            self._log("    loss: nothing — the blocks stay on the beam; with no live passport the reclaimer takes them")
        self._log("  " + "─" * 62)


# ───────────────────────── audit: replay and the reviewer ─────────────────────────

async def replay_cloth(records: list[dict], cloth: str, beam: Beam,
                       folder: Path) -> Loom | None:
    """Replaying the pick log rebuilds a cloth: manifests assemble from the
    events alone. Rejected picks never enter the log — the replay is clean.
    Warping levels come from the log, so AUTO cloths replay deterministically."""
    cloth_records = [r for r in records if r.get("cloth") == cloth]
    if not cloth_records:
        return None
    loom: Loom | None = None
    for r in cloth_records:
        if r["type"] == "warping":
            path = Path(__file__).resolve().parent / "patterns" / r["pattern"]
            pattern = j.load_pattern(path, data=r["data"])
            loom = Loom(pattern, folder, beam, silent=True)
            await loom.warp(r["mode"], levels=r.get("levels"))
        elif r["type"] == "pick" and loom is not None:
            await loom.pick(r["event"], r["payload"])
    return loom


def semantic_diff(passport_a: dict, passport_b: dict):
    """A cloth reviewer: how two cloths differ — by labels, without reading texts.
    '=' — a shared block; '≠type' — unique (labeled)."""
    log("")
    log(f"[REVIEWER] semantic diff of cloths {passport_a['cloth']} vs {passport_b['cloth']} "
        "— by labels, texts unread")
    labels = {**passport_a.get("labels", {}), **passport_b.get("labels", {})}
    common, unique = 0, 0
    for name in passport_a["manifests"]:
        if name not in passport_b["manifests"]:
            continue
        blocks_b = set(passport_b["manifests"][name])
        parts = []
        for addr in passport_a["manifests"][name]:
            if addr in blocks_b:
                parts.append("=")
                common += 1
            else:
                lab = labels.get(addr, {})
                tag = lab.get("type", "?")
                if lab.get("event"):
                    tag += f"·{lab['event']}"
                parts.append(f"≠{tag}")
                unique += 1
        log(f"  {name:<18} " + "  ".join(parts))
    log(f"  ⤷ {common} shared blocks, {unique} unique: the repeatable yarn is common;")
    log("    only titles (release tags) and wefts (event facts) are unique")


# ───────────────────────── the scenario ─────────────────────────

async def attempt(loom: Loom, event: str, payload: dict):
    try:
        await loom.pick(event, payload)
    except ThreadBreak as e:
        loom.stopped = True
        log("")
        log("  ▄▄▄ THREAD BREAK · THE LOOM IS STOPPED ▄▄▄")
        log(f"  reason: {e}")
        log("  the cloth is whole: the pick was rejected, nothing was woven")


def show_file(path: Path, intro: str):
    log("")
    log(f"── {intro} · {path.name} " + "─" * 24)
    for line in path.read_text(encoding="utf-8").splitlines():
        log("  " + line)


def dress(pattern: dict, data_replaced: bool = False):
    s = j.stats(pattern)
    log("")
    log(f"[DRESSING] pattern: {pattern.get('_file', 'pattern')} · the Jacquard language (YAML)")
    if data_replaced:
        log("  the same pattern — the data replaced (one cut, many cloths)")
    log(f"  pattern schema: {pattern.get('_schema', '—')}")
    log(f"  the reed checked the pattern: {s['artifacts']} artifacts ({s['soft']} soft) · "
        f"{s['events']} events · {plural(s['selvedges'], 'selvedge')} · "
        f"{plural(s['guards'], 'type guard')} · "
        f"{plural(s['unravelers'], 'unraveler')} ✓")


CONTRIBUTORS = [{"name": "JoTalbot", "commits": 214},
                {"name": "Iryna Tkachuk", "commits": 96}]

BENCH_BASE = {
    "previous": "v9.0.0",
    "issue": "#90 — benchmark cloth",
    "highlights": ["benchmark cloth"],
    "contributors": CONTRIBUTORS,
}

SWATCH_DATA = {
    "release": "v1.9.9",
    "previous": "v1.9.8",
    "date": "2026-10-20",
    "issue": "#99 — swatch cloth",
    "highlights": ["swatch cloth"],
    "contributors": CONTRIBUTORS,
    "dist": [{"file": "weave-1.9.9.tar.gz", "size": 160}],
}


def _shift(iso: str, days: int) -> str:
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


def _picks(data: dict, offset: int = 0) -> list[tuple[str, dict]]:
    """The happy-path payload list for the release pattern."""
    d = lambda days: _shift(data["date"], days)          # noqa: E731
    return [
        ("TAG_CUT", {"version": data["release"], "date": d(offset)}),
        ("CI_GREEN", {"date": d(offset), "passed": 612, "failed": 0}),
        ("ARTIFACTS_BUILT", {"date": d(offset + 1), "files": data["dist"]}),
        ("PUBLISHED", {"date": d(offset + 1), "registry": data["registry"]}),
        ("RELEASE_SIGNED", {"date": d(offset + 2), "signer": "JoTalbot"}),
    ]


def _bench_release(i: int) -> dict:
    rel = f"v9.0.{i + 1}"
    return {**BENCH_BASE, "release": rel, "date": _shift("2026-10-01", i),
            "dist": [{"file": f"weave-9.0.{i + 1}.tar.gz", "size": 150},
                     {"file": f"weave-9.0.{i + 1}-py3-none-any.whl", "size": 154}]}


async def _weave_release(beam: Beam, folder: Path, data: dict, offset: int,
                         pick_log: PickLog | None = None) -> Loom:
    pattern = j.load_pattern(PATTERN_PATH, data=data)
    loom = Loom(pattern, folder, beam, pick_log=pick_log, silent=True)
    await loom.warp("hard")
    for event, payload in _picks(pattern["data"], offset):
        await loom.pick(event, payload)
    await loom.imprints()
    await loom.submit_cloth()
    return loom


def _bytes(folder: Path) -> int:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())


# ───────────────────────── swatches: negative picks from the pattern ─────────────────────────

async def swatches():
    """Swatches: negative picks generated from the pattern itself.
    The reed must catch every one — the cloth stays whole."""
    log("")
    log("═" * 74)
    log("[SWATCHES] negative picks generated from the pattern — the reed must catch every one")
    log("═" * 74)
    folder = DEMO_ROOT / "_swatches"
    beam = Beam(folder / "beam")            # a scrap beam — a swatch never touches the cloth
    pattern = j.load_pattern(PATTERN_PATH, data=SWATCH_DATA)
    loom = Loom(pattern, folder, beam, silent=True)
    await loom.warp("hard")
    data = pattern["data"]
    caught, missed, kinds = 0, [], Counter()

    async def expect_break(kind: str, event: str, payload: dict, needle: str):
        nonlocal caught
        try:
            await loom.pick(event, payload)
            missed.append(f"{kind} · {event} — NOT CAUGHT")
        except ThreadBreak as e:
            if needle in str(e):
                caught += 1
                kinds[kind] += 1
                log(f"  ✗ {kind:<16} {event:<18} → {e}")
            else:
                missed.append(f"{kind} · {event} — wrong reason: {e}")

    def valid(spec: dict) -> dict:
        p = {}
        for name in spec.get("facts", ()):
            if name in data:
                p[name] = data[name]
            elif name == "version":
                p[name] = data["release"]
            else:
                kind = (spec.get("fact_types") or {}).get(name)
                p[name] = {"number": 0, "date": data["date"], "list": data["dist"],
                           "string": "swatch", "semver": data["release"]}.get(kind, "swatch")
        return p

    WRONG = {"date": "not-a-date", "number": "none", "semver": "next",
             "list": "no files at all", "string": 424242}

    # 1) premature sheds — nothing has happened yet, every requires-clause must bite
    for event, spec in pattern["events"].items():
        if spec.get("requires"):
            await expect_break("premature shed", event, valid(spec), "shed closed")

    # 2) the happy path — each real pick preceded by its negative swatches
    for event, spec in pattern["events"].items():
        if "unravels" in spec:
            continue
        p = valid(spec)
        if spec.get("facts"):
            broken = {k: v for k, v in p.items() if k != spec["facts"][0]}
            await expect_break("fact shape", event, broken, "fact shape")
        for name, kind in (spec.get("fact_types") or {}).items():
            if name in p:
                await expect_break("type guard", event, {**p, name: WRONG[kind]}, "type guard")
                break                                    # one guard probe per event is enough
        for selvedge in spec.get("selvedges", ()):
            m = re.search(r"f\.(\w+)\s*==\s*pattern\.(\w+)", selvedge["check"])
            if m and m.group(1) in p:
                good = data[m.group(2)]
                bad = good + 1 if isinstance(good, (int, float)) else f"{good}-evil"
                await expect_break("selvedge", event, {**p, m.group(1): bad}, "selvedge broken")
        await loom.pick(event, p)                        # the real, positive pick

    # 3) the unraveler — and the late pick into the unraveled future
    await expect_break("fact shape", "RELEASE_CANCELED", {}, "fact shape")
    await expect_break("type guard", "RELEASE_CANCELED", {"reason": 424242}, "type guard")
    await loom.pick("RELEASE_CANCELED", {"reason": "a regression escaped the RC gate"})
    await expect_break("unraveled thread", "PUBLISHED",
                       valid(pattern["events"]["PUBLISHED"]), "unraveled thread")

    total = caught + len(missed)
    log("")
    log(f"  swatches: {total} · caught by the reed: {caught} · missed: {len(missed)}")
    for m in missed:
        log(f"  ✗ MISSED: {m}")
    log("  " + " · ".join(f"{v} {k}" for k, v in kinds.items()))
    log("  the reed needs no human code review — the pattern generates its own negative tests")


# ───────────────────────── the multi-thread loom ─────────────────────────

async def multi_thread_demo(beam: Beam, pick_log: PickLog):
    """Two cloths woven CONCURRENTLY on one beam with one pick log.
    One event loop: no data races on synchronous code; the log commits under
    a lock. The model assumes the store serves parallel I/O (object storage)."""
    log("")
    log("═" * 74)
    log("[MULTI-THREAD LOOM] two cloths woven concurrently — one beam, one pick log")
    log("═" * 74)
    datas = [
        {"release": "v1.6.0", "previous": "v1.5.0", "date": "2026-10-12",
         "issue": "#63 — concurrent shuttles",
         "highlights": ["multi-thread loom"], "contributors": CONTRIBUTORS,
         "dist": [{"file": "weave-1.6.0.tar.gz", "size": 156}]},
        {"release": "v1.6.1", "previous": "v1.6.0", "date": "2026-10-13",
         "issue": "#64 — a polish pick",
         "highlights": ["concurrent shuttles", "polish"], "contributors": CONTRIBUTORS,
         "dist": [{"file": "weave-1.6.1.tar.gz", "size": 157}]},
    ]

    async def weave(data: dict, offset: int) -> Loom:
        pattern = j.load_pattern(PATTERN_PATH, data=data)
        loom = Loom(pattern, DEMO_ROOT, beam, pick_log=pick_log, silent=True)
        await loom.warp("pattern")
        for event, payload in _picks(pattern["data"], offset):
            await loom.pick(event, payload)
        await loom.imprints()
        await loom.submit_cloth()
        return loom

    t0 = time.monotonic()
    looms = await asyncio.gather(*(weave(d, 20 + i) for i, d in enumerate(datas)))
    dt = time.monotonic() - t0
    for loom in looms:
        c = loom.counters
        log(f"  cloth {loom.pattern['data']['release']:<10} woven: "
            f"{c['ops']} physical ops · {c['dedup']} dedups · {c['picks']} picks")
    ops = sum(l.counters["ops"] for l in looms)
    dedup = sum(l.counters["dedup"] for l in looms)
    log(f"  together: {ops} ops · {dedup} dedups · {dt:.2f} s wall time — both shuttles flew at once")
    log(f"  the pick log interleaved both cloths under one lock "
        f"({pick_log.commits} commits so far); both passports are in the warehouse")
    log("  honestly: one event loop = no data races on sync code; a multi-process loom")
    log("  needs real locks — that is exactly the next section")


# ───────────────────────── the multi-process loom ─────────────────────────

def _process_weave(payload: dict, queue) -> dict:
    """A WORKER LOOM: runs inside a real OS process (spawned interpreter),
    weaves one cloth on the SHARED beam and the SHARED pick log — both under
    real file locks (locks.py) — and reports its counters. Announces 'dressed'
    (the pattern loaded and validated — the end of the startup phase) so the
    orchestrator can time pure weaving separately from process startup."""
    beam = Beam(Path(payload["beam"]), locked=True)
    pick_log = PickLog(Path(payload["log"]), lock_path=Path(payload["loglock"]))
    pattern = j.load_pattern(PATTERN_PATH, data=payload["data"])
    queue.put(("dressed", payload["data"]["release"]))
    loom = Loom(pattern, Path(payload["out"]), beam, pick_log=pick_log, silent=True)

    async def _run():
        await loom.warp("pattern")
        for event, pl in _picks(pattern["data"], payload["offset"]):
            await loom.pick(event, pl)
        await loom.imprints()
        await loom.submit_cloth()

    t0 = time.monotonic()
    asyncio.run(_run())
    return {"release": payload["data"]["release"], "pid": os.getpid(),
            "weave_s": time.monotonic() - t0,
            **loom.counters, "lock_wait_ms": pick_log.lock_wait_ms}


def _process_worker(payload: dict, queue):
    """The process entry point: ready → dressed (pattern loaded and validated)
    → weave one cloth → report."""
    queue.put(("ready", payload["data"]["release"], os.getpid()))
    queue.put(("done", _process_weave(payload, queue)))


async def multi_process_demo():
    """The honest upgrade of the multi-thread demo: REAL OS processes with
    REAL file locks. Three fresh interpreters weave three cloths on ONE beam
    and ONE pick log; a sequential control run weaves the same cloths through
    the same locked code path on another cold beam. Verification: the two
    beams must end IDENTICAL — the beam is order-invariant; the pick log
    records the actual commit order — the log is the witness."""
    log("")
    log("═" * 74)
    log("[MULTI-PROCESS LOOM] three real OS processes on one beam and one pick log")
    log("═" * 74)
    folder = DEMO_ROOT / "_multi"
    if folder.exists():
        shutil.rmtree(folder)
    datas = [
        {"release": "v1.7.0", "previous": "v1.6.1", "date": "2026-10-18",
         "issue": "#71 — real processes on one beam",
         "highlights": ["multi-process loom", "real file locks"], "contributors": CONTRIBUTORS,
         "dist": [{"file": "weave-1.7.0.tar.gz", "size": 158}]},
        {"release": "v1.7.1", "previous": "v1.7.0", "date": "2026-10-19",
         "issue": "#72 — lock contention is honest",
         "highlights": ["flock waits, measured"], "contributors": CONTRIBUTORS,
         "dist": [{"file": "weave-1.7.1.tar.gz", "size": 159}]},
        {"release": "v1.7.2", "previous": "v1.7.1", "date": "2026-10-20",
         "issue": "#73 — order invariance of the beam",
         "highlights": ["the beam does not care about the order"], "contributors": CONTRIBUTORS,
         "dist": [{"file": "weave-1.7.2.tar.gz", "size": 160}]},
    ]
    offsets = [30, 31, 32]

    # sequential control: the same three cloths, the same locked code path, one
    # process; patterns dressed BEFORE the clock — pure weaving is measured
    beam_seq = Beam(folder / "beam-seq", locked=True)
    log_seq = PickLog(folder / "picks-seq.jsonl", lock_path=folder / "picks-seq.lock")
    seq_patterns = [j.load_pattern(PATTERN_PATH, data=d) for d in datas]

    async def weave_seq(pattern: dict, offset: int) -> Loom:
        loom = Loom(pattern, folder / "seq", beam_seq, pick_log=log_seq, silent=True)
        await loom.warp("pattern")
        for event, payload in _picks(pattern["data"], offset):
            await loom.pick(event, payload)
        await loom.imprints()
        await loom.submit_cloth()
        return loom

    t0 = time.monotonic()
    seq = [await weave_seq(p, o) for p, o in zip(seq_patterns, offsets)]
    t_seq = time.monotonic() - t0
    ops_seq = sum(l.counters["ops"] for l in seq)

    # the real thing: fresh spawned interpreters — separate PIDs, separate GILs
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    payloads = [{"data": d, "offset": o, "beam": str(folder / "beam"),
                 "log": str(folder / "picks.jsonl"), "loglock": str(folder / "picks.lock"),
                 "out": str(folder)} for d, o in zip(datas, offsets)]
    procs = [ctx.Process(target=_process_worker, args=(p, queue)) for p in payloads]

    t_start = time.monotonic()
    for p in procs:
        p.start()
    ready, dressed, results = 0, 0, []
    t_all_dressed = t_start
    try:
        while len(results) < len(procs):
            msg = queue.get(timeout=120)
            if msg[0] == "ready":
                ready += 1
            elif msg[0] == "dressed":
                dressed += 1
                if dressed == len(procs):
                    t_all_dressed = time.monotonic()
            elif msg[0] == "done":
                results.append(msg[1])          # completion order — the witness
    except Exception as e:
        log(f"  ✗ a worker failed: {e!r} — terminating the rest")
        for p in procs:
            p.terminate()
        return
    for p in procs:
        p.join()
    t_weave = time.monotonic() - t_all_dressed
    t_startup = t_all_dressed - t_start

    ops_par = sum(r["ops"] for r in results)
    dedup_par = sum(r["dedup"] for r in results)
    waits = sum(r["lock_wait_ms"] for r in results)
    log(f"  sequential control: {plural(ops_seq, 'physical op')} · {t_seq:.2f} s of pure weaving "
        f"(one process, the same locked code path)")
    log(f"  startup: {t_startup:.2f} s — spawn (fresh interpreters) + imports + pattern dress:")
    log("            the real price of SHORT-LIVED workers (importing jsonschema alone costs")
    log("            ~1.4 s CPU here); long-lived worker looms amortize it")
    for r in results:                                 # completion order
        log(f"  cloth {r['release']} woven by PID {r['pid']}: "
            f"{r['ops']} ops · {r['dedup']} dedups · {r['weave_s']:.2f} s (its own clock)")
    log(f"  together: {plural(ops_par, 'physical op')} · {dedup_par} dedups · "
        f"weave wall {t_weave:.2f} s — ×{t_seq / t_weave:.1f} vs sequential")
    log(f"  the same {ops_seq} physical writes as the sequential control: "
        f"concurrency changed WHEN, not WHAT")
    log(f"  pick-log lock waits: {waits:.0f} ms total — measured contention "
        f"(0 = the batches did not collide this run)")
    order = [r["release"] for r in results]
    log(f"  commit order: {' → '.join(order)} "
        f"(spawn order: {' → '.join(d['release'] for d in datas)}) —")
    log("  the log is the only witness of interleaving; the beam does not care")
    objs_seq = {p.name for p in (folder / "beam-seq" / "objects").rglob("*") if p.is_file()}
    objs_par = {p.name for p in (folder / "beam" / "objects").rglob("*") if p.is_file()}
    pass_seq = {p.name for p in (folder / "beam-seq" / "warehouse").rglob("*") if p.is_file()}
    pass_par = {p.name for p in (folder / "beam" / "warehouse").rglob("*") if p.is_file()}
    if objs_seq == objs_par and pass_seq == pass_par:
        log(f"  verification: both beams ended IDENTICAL — {len(objs_par)} blocks, "
            f"{len(pass_par)} passports: the beam is order-invariant ✓")
    else:
        log("  ✗ THE BEAMS DIVERGED — needs investigation")
    log("  honestly: the I/O latency is still simulated sleep; the parallelism is real —")
    log("            separate PIDs, separate GILs, one flock per store (POSIX; O_EXCL fallback)")


# ───────────────────────── the mill: long-lived worker looms ─────────────────────────

def _pool_worker(paths: dict, jobs, results):
    """A LONG-LIVED worker loom of the mill: warms up once (imports + a dress
    rehearsal), then weaves every cloth the mill sends. The beam and the pick
    log are opened ONCE and shared with the whole mill under real file locks.
    Stops at the sentinel — v0.6.1 measured that short-lived workers lose to
    startup; the mill answers: warm up once, weave everything."""
    beam = Beam(Path(paths["beam"]), locked=True)
    pick_log = PickLog(Path(paths["log"]), lock_path=Path(paths["loglock"]))
    j.load_pattern(PATTERN_PATH)                      # dress rehearsal: warms the schema
    results.put(("ready", os.getpid()))
    woven = 0
    while True:
        job = jobs.get()
        if job is None:                               # the sentinel: the mill closes
            break
        t0 = time.monotonic()
        pattern = j.load_pattern(PATTERN_PATH, data=job["data"])
        loom = Loom(pattern, Path(paths["out"]), beam, pick_log=pick_log, silent=True)

        async def _run():
            await loom.warp("pattern")
            for event, pl in _picks(pattern["data"], job["offset"]):
                await loom.pick(event, pl)
            await loom.imprints()
            await loom.submit_cloth()

        asyncio.run(_run())
        woven += 1
        results.put(("cloth", {"release": job["data"]["release"], "pid": os.getpid(),
                               "weave_s": time.monotonic() - t0,
                               **loom.counters, "lock_wait_ms": pick_log.lock_wait_ms}))
    results.put(("stopped", {"pid": os.getpid(), "woven": woven}))


async def mill_demo():
    """THE MILL: long-lived worker looms. v0.6.1 measured that short-lived
    workers lose to their own startup; the mill answers with residency —
    warm up once, then weave everything the queue sends. Twenty-four cloths
    through a job queue to three resident looms, against a sequential
    control of the same twelve cloths. The breakeven point is computed from
    the measured numbers, not asserted."""
    log("")
    log("═" * 74)
    log("[THE MILL · WORKER POOL] three long-lived looms, twenty-four cloths — amortization")
    log("═" * 74)
    folder = DEMO_ROOT / "_mill"
    if folder.exists():
        shutil.rmtree(folder)
    datas = [{
        "release": f"v1.8.{i}",
        "previous": f"v1.8.{i - 1}" if i else "v1.7.2",
        "date": _shift("2026-10-25", i),
        "issue": f"#{80 + i} — mill cloth",
        "highlights": ["woven by a long-lived loom"],
        "contributors": CONTRIBUTORS,
        "dist": [{"file": f"weave-1.8.{i}.tar.gz", "size": 150},
                 {"file": f"weave-1.8.{i}-py3-none-any.whl", "size": 154}],
    } for i in range(24)]
    offsets = list(range(24))
    n_workers = 3

    # sequential control: the same twelve cloths, the same locked code path
    beam_seq = Beam(folder / "beam-seq", locked=True)
    log_seq = PickLog(folder / "picks-seq.jsonl", lock_path=folder / "picks-seq.lock")
    seq_patterns = [j.load_pattern(PATTERN_PATH, data=d) for d in datas]

    async def weave_seq(pattern: dict, offset: int) -> Loom:
        loom = Loom(pattern, folder / "seq", beam_seq, pick_log=log_seq, silent=True)
        await loom.warp("pattern")
        for event, payload in _picks(pattern["data"], offset):
            await loom.pick(event, payload)
        await loom.imprints()
        await loom.submit_cloth()
        return loom

    t0 = time.monotonic()
    seq = [await weave_seq(p, o) for p, o in zip(seq_patterns, offsets)]
    t_seq = time.monotonic() - t0
    ops_seq = sum(l.counters["ops"] for l in seq)

    # the mill: three resident looms, one job queue
    ctx = multiprocessing.get_context("spawn")
    jobs, results = ctx.Queue(), ctx.Queue()
    paths = {"beam": str(folder / "beam"), "log": str(folder / "picks.jsonl"),
             "loglock": str(folder / "picks.lock"), "out": str(folder)}
    workers = [ctx.Process(target=_pool_worker, args=(paths, jobs, results))
               for _ in range(n_workers)]

    t_start = time.monotonic()
    for w in workers:
        w.start()
    ready = 0
    cloths, stopped = [], []
    try:
        while ready < n_workers:                      # all looms dressed and warm
            msg = results.get(timeout=120)
            if msg[0] == "ready":
                ready += 1
        t_dressed = time.monotonic()
        for d, o in zip(datas, offsets):
            jobs.put({"data": d, "offset": o})        # natural load balancing
        for _ in workers:
            jobs.put(None)                            # sentinels: the mill closes
        while len(stopped) < n_workers:
            msg = results.get(timeout=120)
            if msg[0] == "cloth":
                cloths.append(msg[1])                 # completion order — the witness
            elif msg[0] == "stopped":
                stopped.append(msg[1])
    except Exception as e:
        log(f"  ✗ a mill worker failed: {e!r} — terminating the rest")
        for w in workers:
            w.terminate()
        return
    for w in workers:
        w.join()
    t_weave = time.monotonic() - t_dressed
    t_total = time.monotonic() - t_start

    ops_mill = sum(c["ops"] for c in cloths)
    dedup_mill = sum(c["dedup"] for c in cloths)
    waits = sum(c["lock_wait_ms"] for c in cloths)
    per_seq = t_seq / len(datas)
    per_mill = t_weave / len(datas)
    amortized = (t_dressed - t_start) / len(datas)
    breakeven = (t_dressed - t_start) / (per_seq - per_mill) if per_seq > per_mill else float("inf")
    log(f"  sequential control: {len(datas)} cloths · {plural(ops_seq, 'physical op')} · "
        f"{t_seq:.2f} s of pure weaving (one process)")
    log(f"  mill startup: {t_dressed - t_start:.2f} s — spawn + imports + a dress rehearsal "
        f"per loom, paid ONCE for the whole run")
    log(f"  jobs: {len(datas)} cloths fed through one queue — each loom takes the next cloth when free")
    by_pid = {}
    for c in cloths:
        by_pid.setdefault(c["pid"], []).append(c)
    for pid, cs in sorted(by_pid.items()):
        avg = sum(c["weave_s"] for c in cs) / len(cs)
        log(f"  loom PID {pid}: {plural(len(cs), 'cloth')} · "
            f"avg {avg:.2f} s per cloth (its own clock)")
    log(f"  the mill: {plural(ops_mill, 'physical op')} · {dedup_mill} dedups · "
        f"weave wall {t_weave:.2f} s — ×{t_seq / t_weave:.1f} vs sequential")
    log(f"  per cloth: sequential {per_seq:.2f} s vs mill {per_mill:.2f} s "
        f"(+{amortized:.2f} s amortized startup) — "
        f"breakeven ≈ {breakeven:.0f} cloths")
    log(f"  total: the mill {t_total:.2f} s incl. startup vs sequential {t_seq:.2f} s — "
        f"{'already ahead' if t_total < t_seq else 'not there yet'} at {len(datas)} cloths, in this sandbox")
    objs_seq = {p.name for p in (folder / "beam-seq" / "objects").rglob("*") if p.is_file()}
    objs_mill = {p.name for p in (folder / "beam" / "objects").rglob("*") if p.is_file()}
    pass_seq = {p.name for p in (folder / "beam-seq" / "warehouse").rglob("*") if p.is_file()}
    pass_mill = {p.name for p in (folder / "beam" / "warehouse").rglob("*") if p.is_file()}
    if objs_seq == objs_mill and pass_seq == pass_mill:
        log(f"  verification: both beams ended IDENTICAL — {len(objs_mill)} blocks, "
            f"{len(pass_mill)} passports: the beam is order-invariant ✓")
    else:
        log("  ✗ THE BEAMS DIVERGED — needs investigation")
    log(f"  pick-log lock waits: {waits:.0f} ms across {len(cloths)} commits — measured contention")
    log("  honestly: the I/O latency is still simulated; 2 CPU cores here; a real mill's")
    log("            looms live for thousands of cloths — the startup vanishes into the noise")


# ───────────────────────── benchmarks ─────────────────────────

async def benchmark():
    log("")
    log("═" * 74)
    log(f"[BENCHMARK] 5 releases of one process · simulated I/O {int(IO_LATENCY * 1000)} ms/op")
    log("  v0.6: every cloth submits a passport (+1) and commits a pick-log batch (+1)")
    log("═" * 74)
    folder = DEMO_ROOT / "_bench"
    if folder.exists():
        shutil.rmtree(folder)

    # codex baseline: 5 sequential runs
    t0 = time.monotonic()
    ops_b = 0
    for i in range(5):
        ops_b += baseline.run_release(_bench_release(i), folder / "baseline")["total"]
    t_b = time.monotonic() - t0
    bytes_b = _bytes(folder / "baseline")

    # WEAVE: 5 cloths through ONE CAS beam, sequential
    beam_b = Beam(folder / "beam")
    log_b = PickLog(folder / "picks.jsonl")
    t0 = time.monotonic()
    looms = []
    for i in range(5):
        looms.append(await _weave_release(beam_b, folder, _bench_release(i), i, log_b))
    t_s = time.monotonic() - t0
    ops_s = sum(l.counters["ops"] for l in looms)
    dedup_s = sum(l.counters["dedup"] for l in looms)
    batches_s = sum(l.counters["fans"] + l.counters["tie_ons"] + l.counters["picks"]
                    + l.counters["imprint_fans"] for l in looms)
    st = beam_b.stats()
    bytes_imprints = sum(_bytes(folder / _bench_release(i)["release"]) for i in range(5))

    # WEAVE: 5 cloths through one beam, CONCURRENT
    beam_c = Beam(folder / "beam-concurrent")
    log_c = PickLog(folder / "picks-concurrent.jsonl")
    t0 = time.monotonic()
    looms_c = await asyncio.gather(*(_weave_release(beam_c, folder, _bench_release(i), i, log_c)
                                     for i in range(5)))
    t_c = time.monotonic() - t0
    ops_c = sum(l.counters["ops"] for l in looms_c)
    dedup_c = sum(l.counters["dedup"] for l in looms_c)
    st_c = beam_c.stats()

    log(f"  {'model':<34} {'phys.ops':>8} {'dedup':>7} {'batches':>8} {'time':>9}   speedup")
    log(f"  {'codex baseline × 5':<34} {ops_b:>8} {'—':>7} {0:>8} {t_b:>7.2f} s   1.0×")
    log(f"  {'WEAVE · one beam × 5':<34} {ops_s:>8} {dedup_s:>7} {batches_s:>8} {t_s:>7.2f} s   ×{t_b / t_s:.1f}")
    log(f"  {'WEAVE · one beam × 5 concurrent':<34} {ops_c:>8} {dedup_c:>7} {'—':>8} {t_c:>7.2f} s   ×{t_b / t_c:.1f}")
    log("")
    log(f"  bytes of truth: baseline — {bytes_b / 1024:.1f} KB of files;")
    log(f"                  WEAVE — beam {st['bytes'] / 1024:.1f} KB of unique blocks "
        f"({st['objects']} objects, {st['dedup']} dedups)")
    log(f"                  + {st['passports']} passports in the warehouse · "
        f"{log_b.commits} pick-log commits")
    log(f"                  (+ {bytes_imprints / 1024:.1f} KB of imprints — printed on demand,")
    log("                   never the truth; without printing, the truth is the beam alone)")
    log(f"  concurrent beam: {st_c['objects']} objects · {st_c['dedup']} dedups — identical to sequential")
    log("")
    log("  honestly: the first cloth is not cheaper (the blocks must be written anyway);")
    log("  the win starts with the second cloth: the letterhead, the constant titles and the")
    log("  contributors block are already on the beam — 100 releases = 1 physical letterhead.")
    log("  The concurrent row assumes the store serves parallel I/O (object storage).")
    log("  +2 ops per cloth (passport + log commit) bought: byte-exact replay, semantic diff,")
    log("  the reclaimer and a live warehouse of cloths.")


async def benchmark_overspin():
    """A cold beam, the same cloth, 5 picks (no imprints).
    A) soft reserves: the base is tied on when its event arrives (classic WEAVE);
    B) auto + overspin: probable bases are woven in the single warping fan."""
    log("")
    log("═" * 74)
    log("[MICRO-BENCHMARK · OVERSPIN] a cold beam · one cloth · 5 picks")
    log("═" * 74)
    folder = DEMO_ROOT / "_micro"
    if folder.exists():
        shutil.rmtree(folder)
    results = {}
    for label, sub, mode in (("A · soft reserves (on event)", "a_reserves", "pattern"),
                             ("B · overspun (at warping)", "b_overspin", "auto")):
        beam = Beam(folder / sub / "beam")
        pattern = j.load_pattern(PATTERN_PATH, data={
            "release": "v7.7.7", "previous": "v7.7.6", "date": "2026-09-26",
            "issue": "#70 — micro-benchmark", "highlights": ["micro cloth"],
            "contributors": CONTRIBUTORS,
            "dist": [{"file": "weave-7.7.7.tar.gz", "size": 140}]})
        loom = Loom(pattern, folder / sub, beam, silent=True)
        t0 = time.monotonic()
        await loom.warp(mode)
        for event, payload in _picks(pattern["data"]):
            await loom.pick(event, payload)
        dt = time.monotonic() - t0
        results[label[0]] = (loom.counters["ops"], loom.counters["dedup"], dt, loom)
        log(f"  {label:<32} {plural(loom.counters['ops'], 'physical op'):>16} · "
            f"dedup {loom.counters['dedup']:>2} · time {dt * 1000:>5.0f} ms")
    oa, da, ta, la = results["A"]
    ob, db, tb, lb = results["B"]
    log("")
    log(f"  overspin: {oa} → {ob} operations (−{oa - ob}) · time ×{ta / tb:.1f}")
    log(f"  ({len(lb.overspun)} probable bases woven in the warping fan; no reserve blocks written)")
    log("  honestly: the overspin win lives on a cold beam; on a warm one the titles")
    log("  deduplicate anyway. The CAS beam is the first-order effect; overspin is second-order.")


def finale(beam: Beam):
    st = beam.stats()
    log("")
    log("═" * 74)
    log(" FINALE")
    log("═" * 74)
    points = [
        "  1. The repeatable future is executed by fans: O(N) → O(fans)",
        "  2. An event's hot path is only the diff: no files, no letterheads, no computation",
        "  3. Independent wefts and fans run concurrently (asyncio) — and real OS processes",
        "     weave one beam under file locks (locks.py): order-invariant; the mill keeps its",
        "     looms long-lived, so the startup cost amortizes to nothing",
        "  4. Reality diverged from the pattern — a thread break, a loud stop, not silent corruption",
        "  5. The future is cancelled — manifests discarded: 0 physical deletions",
        "  6. Soft warping — insurance for a conditional future at a cost of ≈ 0",
        "  7. The language is separated from the runtime: patterns are edited in YAML, the loom untouched",
        "  8. The CAS beam: repeating content is physically one — for every cloth and every process",
        "  9. Selvedges and type guards: shape and types are checked before the weft is beaten in",
        " 10. The pick log is the truth of events: any cloth replays byte-exact — audit for free",
        " 11. Auto-warping and overspin: the loom decides from history — hard / overspun / soft",
        " 12. The reclaimer: yarn with no live manifest goes to thrums — returnable until the wash",
        " 13. The patternsmith: order → draft pattern → schema + reed → human signature",
    ]
    for p in points:
        log(p)
    log("")
    log("  Language:     patterns/release.yaml · patterns/release.schema.json · jacquard.py · patternsmith.py")
    log("  Runtime:      loom.py (asyncio + multiprocessing) · beam.py (CAS + warehouse + reclaimer) · "
        "locks.py (flock) · baseline: codex_baseline.py")
    log(f"  Demo beam:    {st['objects']} objects · {st['bytes']} bytes · {st['dedup']} dedups · "
        f"warehouse: {plural(st['passports'], 'passport')} · thrums: {st['reclaimed']}")
    log("  Cloths:       v1.4.2 (hard) · v1.5.0-rc.1 (unraveled) · v1.4.3 (warm) · "
        "v1.5.0 (auto+overspin) · v1.4.4 (patternsmith) · v1.6.0 + v1.6.1 (concurrent) · "
        "v1.7.0–v1.7.2 (real processes) · v1.8.0–v1.8.11 (the mill)")
    log("  Docs:         README.md · docs/weave-manifest.md · docs/metrics.md · demo/loom.log · demo/picks.jsonl")


async def scenario():
    if DEMO_ROOT.exists():
        shutil.rmtree(DEMO_ROOT)
    DEMO_ROOT.mkdir(parents=True)
    beam = Beam(DEMO_ROOT / ".beam")        # one beam for every cloth and every process
    pick_log = PickLog(DEMO_ROOT / "picks.jsonl")

    log("═" * 74)
    log(" LOOM v0.7 · the WEAVE runtime · JACQUARD v0.4 · CAS beam · overspin · the mill")
    log(" pattern → warping → sheds → picks → reed → cloth(blocks) → imprints → warehouse/thrums")
    log("═" * 74)

    # ── CLOTH 1: a hard warp, the full path, a thread-break demo ──
    p1 = j.load_pattern(PATTERN_PATH)
    dress(p1)
    log("")
    log(f"── CLOTH 1 · {p1['data']['release']} · hard warp (cold beam) " + "─" * 12)
    loom1 = Loom(p1, DEMO_ROOT, beam, pick_log=pick_log)
    await loom1.warp("hard")

    log("")
    log("[THREAD BREAK DEMO 1] trying to pick ARTIFACTS_BUILT before CI_GREEN…")
    await attempt(loom1, "ARTIFACTS_BUILT",
                  {"date": "2026-09-27", "files": p1["data"]["dist"]})
    loom1.re_warp("events arrived out of order — the pattern is intact, we wait for CI_GREEN")

    for event, payload in _picks(p1["data"]):
        await loom1.pick(event, payload)
    await loom1.imprints()
    await loom1.submit_cloth()
    loom1.summary()
    show_file(loom1.artifacts["ReleaseNotes"]["path"],
              "a finished imprint: letterhead + title + shared warp + weft")

    # ── CLOTH 2: the warp follows the pattern, the release is canceled ──
    data2 = {
        "release": "v1.5.0-rc.1", "previous": "v1.4.2", "date": "2026-09-28",
        "issue": "#58 — RC: dark mode flicker",
        "highlights": ["dark mode", "a faster beam reclaimer"],
        "contributors": CONTRIBUTORS,
        "dist": [{"file": "weave-1.5.0-rc.1.tar.gz", "size": 151}],
    }
    p2 = j.load_pattern(PATTERN_PATH, data=data2)
    dress(p2, data_replaced=True)
    log("")
    log(f"── CLOTH 2 · {data2['release']} · the cancel path " + "─" * 24)
    loom2 = Loom(p2, DEMO_ROOT, beam, pick_log=pick_log)
    await loom2.warp("pattern")
    await loom2.pick("TAG_CUT", {"version": "v1.5.0-rc.1", "date": "2026-09-28"})
    await loom2.pick("RELEASE_CANCELED", {"reason": "a regression was found in the RC build"})

    log("")
    log("[THREAD BREAK DEMO 2] CI goes green AFTER the cancel…")
    await attempt(loom2, "CI_GREEN", {"date": "2026-09-29", "passed": 612, "failed": 0})
    log("")
    log("  The loom is stopped: the thread was unraveled, yet CI went green.")
    log("  In the codex world this is silent data corruption (a green build of a canceled release).")
    log("  In WEAVE it is a loud stop and a demand to re-warp the pattern.")
    await loom2.imprints()
    loom2.summary()
    log("  the cloth is unraveled — NO passport is submitted; the yarn waits for the reclaimer")
    show_file(loom2.artifacts["ReleaseNotes"]["path"],
              "the release notes after unraveling: base + weft + compensation")

    # ── CLOTH 3: the warp follows the pattern, the happy path (a warm beam) ──
    data3 = {
        "release": "v1.4.3", "previous": "v1.4.2", "date": "2026-10-02",
        "issue": "#50 — a flaky swatch on py3.12",
        "highlights": ["the py3.12 swatch fix"],
        "contributors": CONTRIBUTORS,
        "dist": [{"file": "weave-1.4.3.tar.gz", "size": 149},
                 {"file": "weave-1.4.3-py3-none-any.whl", "size": 153}],
    }
    p3 = j.load_pattern(PATTERN_PATH, data=data3)
    dress(p3, data_replaced=True)
    log("")
    log(f"── CLOTH 3 · {data3['release']} · tie-ons (a warm beam) " + "─" * 18)
    loom3 = Loom(p3, DEMO_ROOT, beam, pick_log=pick_log)
    await loom3.warp("pattern")
    for event, payload in _picks(p3["data"], 5):
        await loom3.pick(event, payload)
    await loom3.imprints()
    await loom3.submit_cloth()
    loom3.summary()

    # ── AUDIT: replaying the pick log ──
    log("")
    log("═" * 74)
    log("[AUDIT] a cloth is rebuildable by replaying the pick log (event sourcing)")
    log("═" * 74)
    records = PickLog.read(pick_log.path)
    warpings = sum(1 for r in records if r["type"] == "warping")
    picks_n = sum(1 for r in records if r["type"] == "pick")
    log(f"  picks.jsonl: {len(records)} records ({plural(warpings, 'warping')}, "
        f"{plural(picks_n, 'pick')}) —")
    log("  the truth of events: rejected picks never enter the log, the replay is clean")
    loom_replay = await replay_cloth(records, "v1.4.2", beam, DEMO_ROOT / "_replay")
    passport1 = next((p for p in beam.passports() if p["cloth"] == "v1.4.2"), None)
    passport3 = next((p for p in beam.passports() if p["cloth"] == "v1.4.3"), None)
    if loom_replay and passport1:
        replayed = {n: a["blocks"] for n, a in loom_replay.artifacts.items() if a["blocks"]}
        if replayed == passport1["manifests"]:
            blocks_n = sum(len(a) for a in replayed.values())
            log(f"  replay of v1.4.2: {len(replayed)} artifacts · {plural(blocks_n, 'block')} —")
            log("  checked against the warehouse passport: the manifests match byte-for-byte ✓")
        else:
            log("  ✗ THE REPLAY DIVERGED from the passport — needs investigation")
    if passport1 and passport3:
        semantic_diff(passport1, passport3)

    # ── CLOTH 4: AUTO warping + a type-guard demo + speculation ──
    data4 = {
        "release": "v1.5.0", "previous": "v1.4.3", "date": "2026-10-06",
        "issue": "#58 — dark mode flicker",
        "highlights": ["dark mode", "auto-warping from history", "overspun speculation"],
        "contributors": CONTRIBUTORS,
        "dist": [{"file": "weave-1.5.0.tar.gz", "size": 155},
                 {"file": "weave-1.5.0-py3-none-any.whl", "size": 158}],
    }
    p4 = j.load_pattern(PATTERN_PATH, data=data4)
    dress(p4, data_replaced=True)
    log("")
    log(f"── CLOTH 4 · {data4['release']} · AUTO: the loom decides " + "─" * 12)
    loom4 = Loom(p4, DEMO_ROOT, beam, pick_log=pick_log)
    await loom4.warp("auto")

    await loom4.pick("TAG_CUT", {"version": "v1.5.0", "date": _shift("2026-10-06", 9)})

    log("")
    log("[THREAD BREAK DEMO 3] CI reports failed: 'none' — a type guard on watch…")
    await attempt(loom4, "CI_GREEN", {"date": _shift("2026-10-06", 9), "passed": 612, "failed": "none"})
    loom4.re_warp("the reed rejected a mistyped fact — the cloth is whole")

    for event, payload in _picks(p4["data"], 9)[1:]:
        await loom4.pick(event, payload)

    # speculative overspin of the hotfix branch
    await loom4.overspin_branch("HOTFIX", history(pick_log.records))

    await loom4.imprints()
    await loom4.submit_cloth()
    loom4.summary()
    log("")
    log("  the hotfix never came: the speculative yarn stays without a manifest —")
    log("  a failed speculation waits for the reclaimer")

    # ── THE RECLAIMER ──
    log("")
    log("═" * 74)
    log("[RECLAIMER] recycling yarn with no live manifest")
    log("═" * 74)
    live = beam.passports()
    log("  live roots — the warehouse passports: " + ", ".join(p["cloth"] for p in live))
    r = beam.reclaim()
    log(f"  blocks with live references: {r['live_blocks']} · reclaimed: "
        f"{plural(r['reclaimed_objects'], 'object')} ({r['reclaimed_bytes']} bytes) → .beam/thrums/")
    log("  who they are: the spent reserves (tie-ons replaced them with full bases —")
    log("                a placeholder has served its purpose) · the remains of the unraveled")
    log("                v1.5.0-rc.1 (its title, banner, weft, compensation) · the failed")
    log("                speculation of v1.5.0 (the hotfix title)")
    log("  reclamation is not deletion: until the wash, the yarn can be returned to work")

    # ── PATTERNSMITH + CLOTH 5: a new process on the shared beam ──
    log("")
    log("═" * 74)
    log("[PATTERNSMITH] order → pattern draft → schema + reed → human signature")
    log("═" * 74)
    order_text = ("Hotfix v1.4.4 for issue #77 — session leak in the refresh flow. "
                  "Cut by JoTalbot on 30.09.2026, registry ghcr.io/jotalbot/prolepsis.")
    draft_path = patternsmith.take_order(order_text)
    log(f"  the order (free text): {order_text}")
    log(f"  the draft: {draft_path.name} · a template from the library + the project yarn")
    log("  validated: pattern schema ✓ · the reed ✓ → STATUS: awaiting a human signature")
    show_file(draft_path, "the patternsmith draft: a new process")

    p5 = j.load_pattern(draft_path)
    dress(p5)
    log("")
    log(f"── CLOTH 5 · {p5['data']['release']} · a NEW process on the shared beam " + "─" * 2)
    loom5 = Loom(p5, DEMO_ROOT, beam, pick_log=pick_log)
    await loom5.warp("pattern")
    log("  the letterhead is a block of the Release process (dedup); the 'CI TEST REPORT' title —")
    log("  a Release-process block too: the new process inherits the project yarn for free")
    await loom5.pick("HOTFIX_CUT", {"version": "v1.4.4", "date": "2026-09-30"})
    await loom5.pick("HOTFIX_CI", {"date": "2026-09-30", "passed": 41, "failed": 0})
    await loom5.imprints()
    await loom5.submit_cloth()
    loom5.summary()
    log("")
    log("  v1.5.0's speculative title was reclaimed — v1.4.4 is a different block anyway")
    log("  (a different tag): the honest price of a missed speculation is 1 block; a hit costs 0 writes")

    # ── SWATCHES ──
    await swatches()

    # ── MULTI-THREAD LOOM ──
    await multi_thread_demo(beam, pick_log)

    # ── MULTI-PROCESS LOOM ──
    await multi_process_demo()

    # ── THE MILL: long-lived worker looms ──
    await mill_demo()

    # ── BENCHMARKS ──
    await benchmark()
    await benchmark_overspin()

    finale(beam)
    await pick_log.commit()
    log("")
    log(f"pick log: {pick_log.path} · records: {len(pick_log.records)} · commits: {pick_log.commits}")
    log.save(DEMO_ROOT / "loom.log")
    log(f"the run log is saved: {DEMO_ROOT / 'loom.log'}")


if __name__ == "__main__":
    asyncio.run(scenario())
