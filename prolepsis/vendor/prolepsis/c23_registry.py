#!/usr/bin/env python3
"""Built-in C23 frontend registry and source-to-runtime entry point."""
from __future__ import annotations

from dataclasses import dataclass

from . import adapter
from . import adapter_abi
from . import executor
from . import hostlang_frontend
from . import patternsmith_frontend
from . import runtime
from . import third_frontend
from . import workflow_frontend

BUILTIN_FRONTENDS = (
    ("jacquard", "1"),
    ("patternsmith", "1"),
    ("prolepsis-script", "1"),
    ("workflow", "1"),
    ("rust", "1"),
    ("go", "1"),
    ("typescript", "1"),
    ("javascript", "1"),
    ("c", "1"),
    ("cpp", "1"),
    ("java", "1"),
    ("csharp", "1"),
    ("kotlin", "1"),
    ("swift", "1"),
    ("python", "1"),
)


@dataclass(frozen=True)
class SourceExecution:
    """The complete source -> Canonical IR -> selected backend result."""

    adapter: adapter_abi.AdapterResult
    run: executor.ExecutionReport

    @property
    def document(self):
        return self.adapter.document

    @property
    def digest(self) -> str:
        return self.run.digest


def _jacquard_lower(source):
    return adapter.adapt(source, source_name="registry://jacquard")


def _script_lower(source):
    return third_frontend.lower(third_frontend.parse(source))


def _host_lower(language):
    return lambda source: hostlang_frontend.lower(source, language)


def register_builtins() -> tuple[adapter_abi.FrontendSpec, ...]:
    adapter_abi.register_frontend("jacquard", _jacquard_lower, source_version="1")
    adapter_abi.register_frontend("workflow", workflow_frontend.lower, source_version="1")
    adapter_abi.register_frontend("prolepsis-script", _script_lower, source_version="1")
    adapter_abi.register_frontend("patternsmith", patternsmith_frontend.lower, source_version="1")
    for language in hostlang_frontend.LANGUAGES:
        try:
            adapter_abi.frontend(language)
        except adapter_abi.AdapterABIError:
            adapter_abi.register_frontend(
                language, _host_lower(language), source_version="1")
    return tuple(adapter_abi.frontend(name) for name, _ in BUILTIN_FRONTENDS)


def frontends() -> tuple[adapter_abi.FrontendSpec, ...]:
    register_builtins()
    return tuple(adapter_abi.frontend(name) for name, _ in BUILTIN_FRONTENDS)


def compile_source(request: adapter_abi.AdapterRequest,
                   frontend_name: str) -> adapter_abi.AdapterResult:
    register_builtins()
    return adapter_abi.compile_source(request, frontend_name)


def execute_source(request: adapter_abi.AdapterRequest,
                   frontend_name: str,
                   handlers: dict | None = None,
                   *,
                   backend_name: str = "reference",
                   granted=(),
                   log=(),
                   store: runtime.ArtifactStore | None = None,
                   workers: int = 1,
                   predictions: dict | None = None,
                   branches=(),
                   unravel: dict | None = None,
                   binary: str | None = None) -> SourceExecution:
    """Compile through C23, then execute the accepted IR via one backend.

    Source representations never cross the backend boundary. Backend choice
    is execution policy; Canonical IR remains the sole semantic center.
    """
    result = compile_source(request, frontend_name)
    kwargs = {
        "log": log, "handlers": handlers, "granted": granted,
        "workers": workers, "store": store, "predictions": predictions,
        "branches": branches, "unravel": unravel,
    }
    if binary is not None:
        kwargs["binary"] = binary
    report = executor.execute(
        result.document, backend_name=backend_name, **kwargs)
    return SourceExecution(result, report)


if __name__ == "__main__":
    for spec in frontends():
        print(f"{spec.name} {spec.source_version}")
