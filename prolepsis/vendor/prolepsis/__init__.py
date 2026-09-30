"""Prolepsis / WEAVE — canonical event-sourced agent runtime.

Public entry points:
- `prolepsis.main(argv=None)` — the CLI entry point (installed as the
  `prolepsis` console script).
- `prolepsis.compile_source(path)` — compile a WEAVE source file to Canonical IR.
- `prolepsis.generic_handler(...)` — default weave.artifact/jacquard.* capability
  handler used by the Agent Platform when no explicit handler map is supplied.

Submodules (`canonical`, `runtime`, `agent_platform`, `agent_server`,
`agent_mcp`, `agent_sdk`, `adapter`, `adapter_abi`, ...) are also importable.
"""
from __future__ import annotations

from ._cli import main, compile_source, load_document
from ._handlers import generic_handler, DEFAULT_HANDLERS

__all__ = [
    "main",
    "compile_source",
    "load_document",
    "generic_handler",
    "DEFAULT_HANDLERS",
]
