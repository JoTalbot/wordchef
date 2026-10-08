"""Word Chef × Prolepsis — the execution bridge.

This module is the ONLY door between game state and the Prolepsis Agent
Platform. Every meaningful game operation becomes an asynchronous, durable,
replayable execution with a canonical digest, CAS artifacts and checkpoints.

The bridge uses:
* ``AgentGateway`` (Agent Platform v1) — lifecycle, idempotency, capabilities;
* ``JsonExecutionStore`` — persistent execution envelopes (restart-safe);
* ``PersistentArtifactStore`` — content-addressed artifacts (durable);
* game-aware weaver handlers — deterministic recompute + refusal on mismatch.

Prolepsis v0.39.0 (WEAVE / JACQUARD v0.4 patterns) is vendored under
``prolepsis/vendor/`` and pinned by version.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

_HERE = Path(__file__).resolve().parent.parent          # .../prolepsis/
_VENDOR = _HERE / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from prolepsis import adapter, jacquard                      # noqa: E402
from prolepsis.agent_platform import (                       # noqa: E402
    AgentGateway,
    AgentProtocolError,
    AgentRequest,
    CapabilityPolicy,
    JsonExecutionStore,
)
from prolepsis.persistent_cas import PersistentArtifactStore  # noqa: E402

from wordchef_prolepsis.handlers import make_handlers
from wordchef_game.content import CONTENT_DIGEST        # noqa: E402

PROLEPSIS_VERSION = "0.39.0"
AGENT_PROTOCOL_VERSION = "1.0"
GAME_CAPABILITY = "weave.artifact"

PATTERN_DIR = _HERE / "patterns" / "wordchef"

# The ten game operations → Jacquard patterns.
OP_SOURCES: dict[str, str] = {
    "match.create": "match.yaml",
    "round.start": "round.yaml",
    "order.generate": "order.yaml",
    "dish.submit": "dish.yaml",          # processing + score award (one cloth)
    "prep.service": "dish.yaml",
    "bonus.apply": "bonus.yaml",
    "chaos.event": "bonus.yaml",
    "round.end": "round.yaml",
    "result.commit": "match.yaml",
    "leaderboard.update": "leaderboard.yaml",
    "leaderboard.commit": "leaderboard.yaml",
    "match.verify": "verify.yaml",
}


@dataclass
class OpRecord:
    """Everything an operator or an auditor needs about one game operation."""
    op: str
    request_id: str
    execution_id: str
    state: str
    digest: str | None
    artifact_refs: list[dict[str, str]] = field(default_factory=list)
    checkpoint_id: str | None = None
    verified: bool | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op, "request_id": self.request_id,
            "execution_id": self.execution_id, "state": self.state,
            "digest": self.digest, "artifact_refs": self.artifact_refs,
            "checkpoint_id": self.checkpoint_id, "verified": self.verified,
            "artifacts": self.artifacts, "error": self.error,
            "duration_ms": round(self.duration_ms, 3),
        }


class WordChefRuntime:
    """Prolepsis-backed execution runtime for the Word Chef game."""

    def __init__(self, root: str | Path, *, workers: int = 2,
                 on_event: Callable[[dict[str, Any]], None] | None = None):
        self.root = Path(root)
        self.state_dir = self.root / "executions"
        self.cas_dir = self.root / "cas"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cas_dir.mkdir(parents=True, exist_ok=True)

        self.cas = PersistentArtifactStore(self.cas_dir)
        self.templates: dict[str, dict] = {}
        self.pattern_scope: dict[str, dict] = {}
        self._load_templates()

        self._on_event = on_event
        self.audit_log: list[dict[str, Any]] = []
        self._audit_lock = threading.Lock()

        handlers = make_handlers(self.cas, self.templates, self.pattern_scope)
        policy = CapabilityPolicy(allowed=frozenset({GAME_CAPABILITY}))
        self.gateway = AgentGateway(
            JsonExecutionStore(self.state_dir),
            handlers=handlers, policy=policy,
            on_event=self._record_audit_event,
        )
        self._pool = ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="wc-prolepsis")
        self._async_lock = threading.Lock()

    # ───────────────────────── setup ─────────────────────────

    def _load_templates(self) -> None:
        for name in set(OP_SOURCES.values()):
            path = PATTERN_DIR / name
            pattern = jacquard.load_pattern(path)
            bundle = adapter.adapt(pattern, str(path))
            initial = dict(bundle.document.initial)
            for node_id, template in bundle.templates.items():
                self.templates.setdefault(node_id, template)
                self.pattern_scope.setdefault(node_id, initial)

    def _record_audit_event(self, event) -> None:
        data = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        with self._audit_lock:
            self.audit_log.append(data)
            if len(self.audit_log) > 5000:
                del self.audit_log[:1000]
        if self._on_event is not None:
            try:
                self._on_event(data)
            except Exception:
                pass

    # ───────────────────────── execution ─────────────────────────

    def source_for(self, op: str) -> Path:
        try:
            return PATTERN_DIR / OP_SOURCES[op]
        except KeyError as exc:
            raise ValueError(f"unknown game operation '{op}'") from exc

    def execute_op(
        self,
        op: str,
        events: Iterable[dict[str, Any]],
        *,
        request_id: str,
        agent_id: str = "wordchef-engine",
        capabilities: Iterable[str] = (GAME_CAPABILITY,),
        checkpoint: bool = True,
        verify: bool = True,
    ) -> OpRecord:
        """Run one game operation to completion on the Prolepsis loom."""
        started = time.perf_counter()
        source = self.source_for(op)
        events = self._bind_content(events)
        request = AgentRequest(
            request_id=request_id,
            agent_id=agent_id,
            source=str(source),
            capabilities=tuple(capabilities),
            events=tuple(events),
            protocol_version=AGENT_PROTOCOL_VERSION,
        )
        execution = self.gateway.execute(request)
        record = OpRecord(
            op=op, request_id=request_id,
            execution_id=execution.execution_id,
            state=execution.state,
            digest=execution.digest,
            artifact_refs=list(execution.artifact_refs),
            error=execution.error,
        )
        if checkpoint and execution.state == "COMPLETED":
            chk = self.gateway.checkpoint(
                execution.execution_id,
                {"op": op, "request_id": request_id, "game": "wordchef"},
            )
            record.checkpoint_id = chk["checkpoint_id"]
        if verify and execution.state == "COMPLETED":
            verdict = self.gateway.verify(execution.execution_id, str(source))
            record.verified = bool(verdict.get("verified"))
            record.artifacts = self.read_artifacts(execution.execution_id)
        record.duration_ms = (time.perf_counter() - started) * 1000.0
        return record

    def execute_op_async(
        self,
        op: str,
        events: Iterable[dict[str, Any]],
        *,
        request_id: str,
        agent_id: str = "wordchef-engine",
        verify: bool = True,
    ) -> Future:
        """Async execution path: reserve the execution id now, run in a worker.

        Uses the Agent Platform's ``prepare``/``execute_queued`` handshake so
        retries with the same request_id are idempotent.
        """
        events = self._bind_content(events)
        source = self.source_for(op)
        request = AgentRequest(
            request_id=request_id, agent_id=agent_id, source=str(source),
            capabilities=(GAME_CAPABILITY,), events=events,
            protocol_version=AGENT_PROTOCOL_VERSION,
        )
        prepared = self.gateway.prepare(request)

        def _run() -> OpRecord:
            started = time.perf_counter()
            execution = self.gateway.execute_queued(prepared.execution_id, request)
            record = OpRecord(
                op=op, request_id=request_id,
                execution_id=execution.execution_id,
                state=execution.state,
                digest=execution.digest,
                artifact_refs=list(execution.artifact_refs),
                error=execution.error,
            )
            if execution.state == "COMPLETED":
                chk = self.gateway.checkpoint(
                    execution.execution_id,
                    {"op": op, "request_id": request_id, "game": "wordchef"},
                )
                record.checkpoint_id = chk["checkpoint_id"]
                if verify:
                    verdict = self.gateway.verify(execution.execution_id, str(source))
                    record.verified = bool(verdict.get("verified"))
                    record.artifacts = self.read_artifacts(execution.execution_id)
            record.duration_ms = (time.perf_counter() - started) * 1000.0
            return record

        return self._pool.submit(_run)

    @staticmethod
    def _bind_content(events: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
        """Bind every execution to the exact canonical content pack."""
        bound: list[dict[str, Any]] = []
        for event in events:
            item = dict(event)
            payload = dict(item.get("payload") or {})
            payload.setdefault("content_digest", CONTENT_DIGEST)
            item["payload"] = payload
            bound.append(item)
        return tuple(bound)

    # ───────────────────────── inspection ─────────────────────────

    def status(self, execution_id: str) -> dict[str, Any]:
        return self.gateway.status(execution_id)

    def read_artifacts(self, execution_id: str) -> list[dict[str, Any]]:
        """Read artifact payloads back from the durable CAS."""
        status = self.gateway.status(execution_id)
        out: list[dict[str, Any]] = []
        for ref in status.get("artifact_refs", ()):
            try:
                payload = json.loads(self.cas.get({"$cas": ref["id"]}))
            except Exception:
                payload = {"unavailable": ref["id"]}
            out.append({"ref": ref, "payload": payload})
        return out

    def replay(self, execution_id: str) -> dict[str, Any]:
        op = self._op_for_execution(execution_id)
        return self.gateway.replay(execution_id, str(self.source_for(op)))

    def verify(self, execution_id: str) -> dict[str, Any]:
        op = self._op_for_execution(execution_id)
        return self.gateway.verify(execution_id, str(self.source_for(op)))

    def _op_for_execution(self, execution_id: str) -> str:
        status = self.gateway.status(execution_id)
        request_id = status.get("request_id", "")
        # request ids are namespaced as "<op>:<rest>" by ops.py
        for op in OP_SOURCES:
            if request_id.startswith(op + ":"):
                return op
        return "match.create"

    def audit_events(self, execution_id: str | None = None,
                     limit: int = 200) -> list[dict[str, Any]]:
        with self._audit_lock:
            events = list(self.audit_log)
        if execution_id:
            events = [e for e in events if e.get("execution_id") == execution_id]
        return events[-limit:]

    def executions(self) -> list[dict[str, Any]]:
        rows = []
        for path in sorted(self.state_dir.glob("exec_*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rows.append({k: data.get(k) for k in (
                "execution_id", "request_id", "agent_id", "state",
                "digest", "document_address")})
        return rows

    # ───────────────────────── platform acceptance ─────────────────────────

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "protocol": AGENT_PROTOCOL_VERSION,
                "runtime": f"prolepsis-{PROLEPSIS_VERSION}"}

    def ready(self) -> dict[str, Any]:
        ready = self.state_dir.exists() and self.cas_dir.exists()
        return {"status": "ready" if ready else "not_ready",
                "protocol": AGENT_PROTOCOL_VERSION,
                "content_digest": CONTENT_DIGEST}

    def version(self) -> dict[str, Any]:
        return {"server": f"wordchef-runtime/{PROLEPSIS_VERSION}",
                "protocol": AGENT_PROTOCOL_VERSION,
                "prolepsis": PROLEPSIS_VERSION,
                "content_digest": CONTENT_DIGEST}

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=False)
