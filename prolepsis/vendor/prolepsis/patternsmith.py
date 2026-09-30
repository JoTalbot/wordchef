# -*- coding: utf-8 -*-
"""
PATTERNSMITH v0.2 — drafts patterns from orders
=================================================
An order (what the project needs) → a pattern draft (YAML, the Jacquad
language) → validation against the pattern schema and the reed → a human
signature → only then does a loom dress it.

v0.2 understands orders written in a deliberately tiny grammar (regexes):
kind, version, issue, author, date, registry. That is honest and on point:
whatever writes the draft — a regex today, an LLM tomorrow — it CANNOT
smuggle anything past the contract: schema + reed + human signature stand
between the draft and the loom. The LLM upgrades the front door, not the
lock.

Project yarn (the letterhead constants) is a shared library: a new process
inherits the project's blocks from the common beam for free.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from . import jacquard as j

DRAFT_PATH = Path(__file__).resolve().parent / "patterns" / "hotfix-draft.yaml"

# Project yarn — shared by every pattern of the project (patterns/release.yaml → yarn)
PROJECT_YARN = {
    "project": "WEAVE",
    "repo": "github.com/JoTalbot/prolepsis",
    "license": "MIT",
    "registry": "ghcr.io/jotalbot/prolepsis",
    "maintainer": "JoTalbot",
}


class _Lit(str):
    """A multiline string — YAML writes it as a literal block (|)."""


def _lit(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="|")


yaml.add_representer(_Lit, _lit, yaml.SafeDumper)


# ───────────────────────── the order grammar (tiny, honest) ─────────────────────────

_KINDS = (("hotfix", r"\bhotfix\b|\bpatch\b|\bfix\b"),
          ("release", r"\brelease\b"))


def parse_order(text: str) -> dict:
    """Parse a free-text order in a tiny grammar. Raises ValueError with the
    list of unparseable fields — the grammar never guesses silently."""
    order: dict = {"raw": text.strip()}
    for kind, rx in _KINDS:
        if re.search(rx, text, re.I):
            order["kind"] = kind
            break
    else:
        order["kind"] = "release"

    m = re.search(r"\b(v\d+\.\d+\.\d+(?:-[\w.]+)?)\b", text)
    if m:
        order["release"] = m.group(1)
    m = re.search(r"#(\d+)\s*[—-]?\s*([^.]+?)\.", text)
    if m:
        order["issue"] = f"#{m.group(1)} — {m.group(2).strip()}"
    m = re.search(r"\bby ([A-Z][\w'-]+(?: [A-Z][\w'-]+)?)(?:\s+on|,|\.)", text)
    if m:
        order["author"] = m.group(1)
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if m:
        order["date"] = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.search(r"registry\s+([\w./:@-]+)", text)
    if m:
        order["registry"] = m.group(1).rstrip(".")   # '…/prolepsis.' → strip the sentence dot

    missing = [k for k in ("release", "issue", "date") if k not in order]
    if missing:
        raise ValueError(f"the order grammar could not find: {', '.join(missing)}; "
                         "expected something like: 'Hotfix v1.4.4 for issue #77 — session leak. "
                         "Cut by JoTalbot on 30.09.2026, registry ghcr.io/x/y.'")
    return order


# ───────────────────────── templates (a library of cuts) ─────────────────────────

def _hotfix_template(order: dict) -> dict:
    """A hotfix release: hotfix notes + CI report.
    The 'CI TEST REPORT' title is byte-identical to the Release process —
    the block deduplicates on the shared beam: a new process inherits the
    project's yarn for free."""
    return {
        "yarn": {**PROJECT_YARN, **({"registry": order["registry"]}
                                    if "registry" in order else {})},
        "data": {
            "release": order["release"],
            "date": order["date"],
            "issue": order["issue"],
            "note": order.get("note", "hotfix"),
        },
        "artifacts": {
            "HotfixNotes": {
                "title": "HOTFIX RELEASE NOTES · {{ pattern.release }}",
                "warping": "hard",
            },
            "TestReport": {
                "title": "CI TEST REPORT",
                "warping": "soft",
            },
        },
        "warping": {
            "shared": [{
                "into": ["HotfixNotes"],
                "block": _Lit(
                    "── WARP · version banner (woven once, fanned out) ──\n"
                    "{{ pattern.project }} {{ pattern.release }} · "
                    "{{ human_date(pattern.date) }} · {{ pattern.license }}"),
            }],
        },
        "events": {
            "HOTFIX_CUT": {
                "requires": [],
                "facts": ["version", "date"],
                "fact_types": {"version": "semver", "date": "date"},
                "selvedges": [{
                    "rule": "the tag must match the declared hotfix",
                    "check": "f.version == pattern.release",
                }],
                "weaves": {"HotfixNotes": {"weft": _Lit(
                    "Cut by {{ pattern.maintainer }} on {{ human_date(f.date) }}\n"
                    "Resolves {{ pattern.issue }}\n"
                    "\n"
                    "{{ pattern.note }}")}},
            },
            "HOTFIX_CI": {
                "requires": ["HOTFIX_CUT"],
                "facts": ["date", "passed", "failed"],
                "fact_types": {"date": "date", "passed": "number", "failed": "number"},
                "selvedges": [{
                    "rule": "no failing tests may pass the reed",
                    "check": "f.failed == 0",
                }],
                "weaves": {"TestReport": {"weft": _Lit(
                    "Suite: hotfix matrix · {{ human_date(f.date) }}\n"
                    "Tests: {{ f.passed }} passed · {{ f.failed }} failed\n"
                    "Gate: green — the reed may beat this weft in")}},
            },
        },
    }


TEMPLATES = {
    "hotfix": _hotfix_template,
}


# ───────────────────────── the draft ─────────────────────────

def draft_data(order: dict) -> dict:
    """Build deterministic pattern data without filesystem I/O."""
    if order.get("kind") not in TEMPLATES:
        raise ValueError(f"no template in the library for '{order.get('kind')}'")
    return TEMPLATES[order["kind"]](order)


def draft_pattern(order: dict, path: Path = DRAFT_PATH) -> Path:
    """Order → pattern draft: YAML validated by the schema and the reed.
    The draft waits for a human signature — no loom dresses it earlier."""
    pattern = draft_data(order)
    text = yaml.safe_dump(pattern, allow_unicode=True, sort_keys=False, width=100)
    header = (f"# PATTERNSMITH DRAFT · template '{order['kind']}' · order: "
              f"{order.get('raw', '')[:60]}\n"
              "# validated: pattern schema + reed · STATUS: awaiting human signature\n\n")
    path.write_text(header + text, encoding="utf-8")
    j.load_pattern(path)     # the draft must pass the schema and the reed right away
    return path


def take_order(text: str, path: Path = DRAFT_PATH) -> Path:
    """Free-text order → parsed order → pattern draft."""
    return draft_pattern(parse_order(text), path)
