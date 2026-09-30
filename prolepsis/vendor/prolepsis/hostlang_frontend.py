#!/usr/bin/env python3
"""Host-language interop frontend for C23.

This is deliberately an embedding contract, not a fake parser for ten
languages. A host source file carries a PROLEPSIS-WORKFLOW v1 JSON block in
ordinary comments; the block is extracted, parsed, lowered through the same
C23 kernel gate, and tagged with the host language in provenance.

Supported comment syntaxes cover common source families:
Rust/Go/TypeScript/JavaScript/C/C++/Java/C#/Kotlin/Swift/Python.
The canonical semantics remain entirely outside the host language.
"""
from __future__ import annotations

import json
import re

from . import workflow_frontend
from .adapter_abi import AdapterResult, kernel_gate

ABI_VERSION = "1.0.0"
LANGUAGES = (
    "rust", "go", "typescript", "javascript", "c", "cpp",
    "java", "csharp", "kotlin", "swift", "python",
)

class HostLanguageError(ValueError):
    """Invalid host-language embedding with a stable diagnostic."""

def _comment_lines(source: str, language: str) -> list[str]:
    if language not in LANGUAGES:
        raise HostLanguageError(f"unsupported host language: {language}")
    lines = source.splitlines()
    out: list[str] = []
    in_block = False
    for raw in lines:
        text = raw.strip()
        if "PROLEPSIS-BEGIN" in text:
            in_block = True
            text = text.split("PROLEPSIS-BEGIN", 1)[1]
        if in_block:
            text = re.sub(r"^\s*(?://|#|/\*|\*|--|;|\*)\s?", "", text)
            if "PROLEPSIS-END" in text:
                text = text.split("PROLEPSIS-END", 1)[0]
                in_block = False
            if text.strip():
                out.append(text.rstrip())
    if in_block:
        raise HostLanguageError("unterminated PROLEPSIS-BEGIN block")
    if not out:
        raise HostLanguageError("missing PROLEPSIS-BEGIN/END block")
    return out

def parse(source: str, language: str) -> dict:
    """Extract a bounded PROLEPSIS-WORKFLOW v1 manifest from host comments."""
    text = "\n".join(_comment_lines(source, language))
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HostLanguageError(
            f"invalid embedded PROLEPSIS JSON at line {exc.lineno}: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise HostLanguageError("embedded source must be a JSON object")
    if value.get("source_version") != "PROLEPSIS-WORKFLOW v1":
        raise HostLanguageError("embedded source must declare PROLEPSIS-WORKFLOW v1")
    return value

def lower(source: str, language: str) -> AdapterResult:
    """Lower an embedded workflow through the ordinary C23 kernel gate."""
    parsed = parse(source, language)
    result = workflow_frontend.lower(parsed)
    checked = kernel_gate(result.document)
    provenance = dict(result.provenance)
    provenance["source_language"] = language
    provenance["source_embedding"] = "PROLEPSIS-HOST v1"
    provenance["document_addr"] = checked.addr
    provenance["abi_version"] = ABI_VERSION
    return AdapterResult(checked, provenance, result.diagnostics, ABI_VERSION)

def wrap(workflow_json: str, language: str) -> str:
    """Create a deterministic comment-wrapped host source for tests/tools."""
    if language not in LANGUAGES:
        raise HostLanguageError(f"unsupported host language: {language}")
    prefix = "#" if language == "python" else "//"
    body = "\n".join(f"{prefix} {line}" for line in workflow_json.splitlines())
    return f"{prefix} PROLEPSIS-BEGIN\n{body}\n{prefix} PROLEPSIS-END\n"

if __name__ == "__main__":
    print("PROLEPSIS-HOST v1")
    print("languages: " + ", ".join(LANGUAGES))
