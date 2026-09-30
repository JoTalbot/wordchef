#!/usr/bin/env python3
"""C23 Adapter ABI v1: one source-to-Canonical-IR acceptance boundary.

Every frontend supplies a deterministic lowerer. `adapt_with` normalizes its
result, rejects error diagnostics, reparses canonical bytes through the kernel
gate, and records ABI provenance. Repair candidates re-enter this same path;
the repair callback receives source requests, never mutable Canonical IR.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import canonical as ir
from . import adapter

ABI_VERSION = "1.0.0"
AdapterLowerer = Callable[[Any], "AdapterResult | adapter.AdapterBundle"]


@dataclass(frozen=True)
class FrontendSpec:
    """Registered source frontend contract at the C23 boundary."""
    name: str
    source_version: str
    lowerer: AdapterLowerer


_FRONTENDS: dict[str, FrontendSpec] = {}


def register_frontend(name: str, lowerer: AdapterLowerer, *,
                      source_version: str = "1") -> FrontendSpec:
    """Register one deterministic source producer without adding IR semantics."""
    if not isinstance(name, str) or not name or any(ch.isspace() for ch in name):
        raise ValueError("frontend name must be a non-empty token")
    if not callable(lowerer):
        raise TypeError("frontend lowerer must be callable")
    if not isinstance(source_version, str) or not source_version:
        raise ValueError("source_version must be a non-empty string")
    existing = _FRONTENDS.get(name)
    if existing is not None and existing != FrontendSpec(name, source_version, lowerer):
        raise AdapterABIError(f"C23-FRONTEND-DUPLICATE: {name!r}")
    spec = FrontendSpec(name, source_version, lowerer)
    _FRONTENDS[name] = spec
    return spec


def frontend(name: str) -> FrontendSpec:
    """Return a registered frontend or a stable C23 lookup failure."""
    try:
        return _FRONTENDS[name]
    except KeyError as exc:
        raise AdapterABIError(f"C23-FRONTEND-UNKNOWN: {name!r}") from exc


def frontends() -> tuple[FrontendSpec, ...]:
    """Return registered frontends in deterministic name order."""
    return tuple(_FRONTENDS[name] for name in sorted(_FRONTENDS))


def compile_source(request: "AdapterRequest", frontend_name: str) -> "AdapterResult":
    """Compile a registered source through the same C23 ABI gate."""
    spec = frontend(frontend_name)
    result = adapt_with(request, spec.lowerer)
    # Source provenance belongs to the producer itself. The registry version
    # is separate so registration never rewrites or falsely validates it.
    result.provenance["frontend"] = spec.name
    result.provenance["frontend_version"] = spec.source_version
    return result


@dataclass(frozen=True)
class AdapterRequest:
    source: Any
    source_name: str = "source"


@dataclass(frozen=True)
class AdapterFinding:
    severity: str
    code: str
    message: str
    source: str | None = None

    def to_canonical(self) -> dict:
        value = {"severity": self.severity, "code": self.code,
                 "message": self.message}
        if self.source is not None:
            value["source"] = self.source
        return value


@dataclass(frozen=True)
class AdapterResult:
    document: ir.IRDocument
    provenance: dict
    diagnostics: tuple[dict, ...]
    adapter_version: str = ABI_VERSION

    def to_canonical(self) -> dict:
        return {"abi_version": self.adapter_version,
                "document": self.document.to_canonical(),
                "provenance": self.provenance,
                "diagnostics": list(self.diagnostics)}


class AdapterABIError(Exception):
    """A C23 boundary failure. The kernel remains authoritative."""


def kernel_gate(document: ir.IRDocument) -> ir.IRDocument:
    """Mandatory C23 gate: reparse canonical bytes before acceptance."""
    if not isinstance(document, ir.IRDocument):
        raise AdapterABIError("C23-ADAPTER-TYPE: lowerer did not return IRDocument")
    encoded = ir.dumps(document.to_canonical())
    checked = ir.IRDocument.from_canonical(ir.loads(encoded))
    if checked.addr != document.addr:
        raise AdapterABIError("C23-KERNEL-DIGEST: canonical address changed")
    return checked


def adapt_with(request: AdapterRequest, lowerer: AdapterLowerer) -> AdapterResult:
    """Run any source frontend through the shared C23 ABI and kernel gate.

    A lowerer may return either the Jacquard `AdapterBundle` witness or an
    `AdapterResult` from another independent frontend. It cannot return raw
    JSON/IR maps or bypass the mandatory canonical-byte reparse.
    """
    if not isinstance(request, AdapterRequest):
        raise AdapterABIError("C23-REQUEST-TYPE: expected AdapterRequest")
    if not callable(lowerer):
        raise AdapterABIError("C23-LOWERER-TYPE: expected a callable lowerer")
    try:
        candidate = lowerer(request.source)
        if isinstance(candidate, adapter.AdapterBundle):
            document = candidate.document
            provenance = dict(candidate.provenance)
            diagnostics = tuple(d.to_canonical() for d in candidate.diagnostics)
        elif isinstance(candidate, AdapterResult):
            if candidate.adapter_version != ABI_VERSION:
                raise AdapterABIError(
                    f"C23-ABI-VERSION: unsupported result ABI "
                    f"{candidate.adapter_version!r}; expected {ABI_VERSION!r}")
            document = candidate.document
            provenance = dict(candidate.provenance)
            diagnostics = tuple(candidate.diagnostics)
        else:
            raise AdapterABIError(
                "C23-ADAPTER-TYPE: lowerer must return AdapterBundle or AdapterResult")
        if not isinstance(provenance, dict):
            raise AdapterABIError("C23-PROVENANCE-TYPE: expected a mapping")
        if any(not isinstance(diagnostic, dict) for diagnostic in diagnostics):
            raise AdapterABIError("C23-DIAGNOSTIC-TYPE: expected canonical mappings")
        errors = [diagnostic for diagnostic in diagnostics
                  if diagnostic.get("severity") == "error"]
        if errors:
            raise AdapterABIError(f"C23-DIAGNOSTIC-ERROR: {errors}")
        document = kernel_gate(document)
    except AdapterABIError:
        raise
    except Exception as exc:
        # Frontend-specific parse/validation failures must not leak around the
        # ABI boundary. KeyboardInterrupt/SystemExit remain process controls.
        raise AdapterABIError(
            f"C23-LOWER-REJECT: {type(exc).__name__}: {exc}") from exc

    provenance["abi_version"] = ABI_VERSION
    provenance["document_addr"] = document.addr
    return AdapterResult(document, provenance, diagnostics, ABI_VERSION)


def adapt_request(request: AdapterRequest) -> AdapterResult:
    """Jacquard's C23 witness, retained as the backwards-compatible entry."""
    return adapt_with(
        request,
        lambda source: adapter.adapt(source, source_name=request.source_name))


def repair_loop(initial: AdapterRequest,
                repair: Callable[[AdapterRequest, tuple[AdapterFinding, ...]], AdapterRequest],
                *, max_attempts: int = 4,
                lowerer: AdapterLowerer | None = None) -> AdapterResult:
    """Repair only source candidates; every attempt re-enters the C23 ABI."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    request = initial
    history = []
    for attempt in range(1, max_attempts + 1):
        try:
            return (adapt_request(request) if lowerer is None
                    else adapt_with(request, lowerer))
        except AdapterABIError as exc:
            finding = AdapterFinding("error", "C23-REPAIR_REQUIRED",
                                     str(exc), request.source_name)
            history.append(str(exc))
            if attempt == max_attempts:
                raise AdapterABIError(
                    f"C23-REPAIR_EXHAUSTED after {max_attempts} attempts: " +
                    " | ".join(history)) from exc
            request = repair(request, (finding,))
            if not isinstance(request, AdapterRequest):
                raise AdapterABIError(
                    "C23-REPAIR-TYPE: repair callback must return AdapterRequest")


def self_check() -> None:
    pattern = adapter.j.load_pattern("patterns/release.yaml")
    request = AdapterRequest(pattern, "patterns/release.yaml")
    first = adapt_request(request)
    again = adapt_request(request)
    assert first.document.addr == again.document.addr
    assert first.provenance["abi_version"] == ABI_VERSION
    assert first.provenance["document_addr"] == first.document.addr

    attempts = {"count": 0}

    def repair(req, findings):
        attempts["count"] += 1
        assert findings[0].code == "C23-REPAIR_REQUIRED"
        return AdapterRequest(req.source, req.source_name)

    repaired = repair_loop(request, repair, max_attempts=2)
    assert repaired.document.addr == first.document.addr
    assert attempts["count"] == 0

    broken = dict(pattern)
    broken["data"] = dict(pattern["data"])
    broken["data"]["__float"] = 1.5

    def repair_invalid(req, findings):
        assert findings[0].severity == "error"
        return req

    try:
        repair_loop(AdapterRequest(broken, "broken.yaml"), repair_invalid,
                    max_attempts=2)
    except AdapterABIError as exc:
        assert "C23-REPAIR_EXHAUSTED" in str(exc)
    else:
        raise AssertionError("invalid source escaped the C23 ABI")
    print("C23 ABI v1: shared lowerer boundary + kernel gate + provenance + "
          "repair boundary ✓")


if __name__ == "__main__":
    self_check()
