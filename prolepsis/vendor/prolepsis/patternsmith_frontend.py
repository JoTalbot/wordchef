#!/usr/bin/env python3
"""Patternsmith order → C23 adapter → Canonical IR.

Patternsmith is a source producer, not a second semantic language. Its order
grammar and templates produce Jacquard source data; this frontend validates
that source with the Jacquard schema/reed and then crosses the shared C23 ABI.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from . import adapter_abi
from . import jacquard
from . import patternsmith
import yaml


def lower(source: str) -> adapter_abi.AdapterResult:
    """Compile one Patternsmith order through the shared C23 Jacquard path."""
    order = patternsmith.parse_order(source)
    draft = patternsmith.draft_data(order)
    with tempfile.TemporaryDirectory(prefix="prolepsis-patternsmith-") as tmp:
        path = Path(tmp) / "draft.yaml"
        path.write_text(
            "# Patternsmith C23 candidate\n"
            + yaml.safe_dump(draft, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8",
        )
        loaded = jacquard.load_pattern(path)
    return adapter_abi.adapt_request(
        adapter_abi.AdapterRequest(loaded, "patternsmith://order")
    )


def main() -> int:
    source = (
        "Hotfix v1.4.4 for issue #77 — session leak in the refresh flow. "
        "Cut by JoTalbot on 30.09.2026, registry ghcr.io/jotalbot/prolepsis."
    )
    first = lower(source)
    second = lower(source)
    assert first.document.to_canonical() == second.document.to_canonical()
    assert first.document.addr == second.document.addr
    assert first.adapter_version == adapter_abi.ABI_VERSION
    assert first.provenance["document_addr"] == first.document.addr
    print("Patternsmith → Jacquard → C23 → Canonical IR: PASS")
    print(f"document: {first.document.addr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
