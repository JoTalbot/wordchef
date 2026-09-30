"""Stable Agent Platform v1 facade over the existing Prolepsis runtime."""
from __future__ import annotations
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from . import canonical
from . import runtime
PROTOCOL_VERSION = "1.0"
EVENT_VERSION = "1.0"
TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
TRANSITIONS = {
    "CREATED": {"VALIDATING", "QUEUED", "CANCELLED"},
    "QUEUED": {"VALIDATING", "CANCELLED"},
    "VALIDATING": {"READY", "FAILED", "CANCELLED"},
    "READY": {"RUNNING", "CANCELLED"},
    "RUNNING": {"WAITING", "PAUSED", "COMPLETED", "FAILED", "CANCELLED", "RECOVERING"},
    "WAITING": {"RUNNING", "CANCELLED", "FAILED"},
    "PAUSED": {"RUNNING", "CANCELLED"},
    "RECOVERING": {"RUNNING", "FAILED", "CANCELLED"},
    "COMPLETED": set(), "FAILED": set(), "CANCELLED": set(),
}


class AgentProtocolError(ValueError):
    pass


class CapabilityPolicy:
    """Explicit capability authorization policy."""

    def __init__(self, allowed: frozenset[str] | None = None):
        self.allowed = allowed

    def authorize(self, agent_id: str, requested: tuple[str, ...]) -> tuple[list[str], list[str]]:
        if self.allowed is None:
            return list(requested), []
        granted, denied = [], []
        for cap in requested:
            if cap in self.allowed:
                granted.append(cap)
            else:
                denied.append(cap)
        return granted, denied


@dataclass(frozen=True)
class AgentRequest:
    request_id: str
    agent_id: str
    source: str
    capabilities: tuple[str, ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    workers: int = 1
    protocol_version: str = PROTOCOL_VERSION

    def validate(self):
        if self.protocol_version != PROTOCOL_VERSION:
            raise AgentProtocolError("unsupported agent protocol version")
        if not self.request_id or not self.agent_id or not self.source:
            raise AgentProtocolError("request_id, agent_id and source are required")
        if self.workers < 1:
            raise AgentProtocolError("workers must be >= 1")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise AgentProtocolError("capabilities must be unique")
        for capability in self.capabilities:
            if not isinstance(capability, str) or not capability:
                raise AgentProtocolError("capabilities must contain non-empty strings")
        for event in self.events:
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise AgentProtocolError("events must contain a string type")
            if not isinstance(event.get("payload", {}), dict):
                raise AgentProtocolError("event payload must be an object")


@dataclass(frozen=True)
class AgentEvent:
    execution_id: str
    event_id: str
    sequence: int
    type: str
    source: str
    payload: dict[str, Any]
    version: str = EVENT_VERSION

    def to_dict(self):
        return {
            "version": self.version,
            "execution_id": self.execution_id,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "type": self.type,
            "source": self.source,
            "payload": self.payload,
        }


@dataclass
class Execution:
    execution_id: str
    request_id: str
    agent_id: str
    state: str = "CREATED"
    digest: str | None = None
    document_address: str | None = None
    events: list[AgentEvent] = field(default_factory=list)
    artifact_refs: list[dict[str, str]] = field(default_factory=list)
    error: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    runtime_log: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)

    def transition(self, target):
        if target not in TRANSITIONS.get(self.state, set()):
            raise AgentProtocolError(f"illegal execution transition {self.state}->{target}")
        self.state = target

    def emit(self, event_type, source, payload):
        event = AgentEvent(
            self.execution_id,
            f"{self.execution_id}:e{len(self.events) + 1}",
            len(self.events) + 1,
            event_type,
            source,
            payload,
        )
        self.events.append(event)
        return event

    def to_dict(self):
        return {
            "execution_id": self.execution_id,
            "request_id": self.request_id,
            "agent_id": self.agent_id,
            "state": self.state,
            "digest": self.digest,
            "document_address": self.document_address,
            "events": [e.to_dict() for e in self.events],
            "artifact_refs": self.artifact_refs,
            "error": self.error,
            "result": self.result,
            "runtime_log": self.runtime_log,
            "checkpoints": self.checkpoints,
        }


class JsonExecutionStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, execution_id):
        return self.root / f"{execution_id}.json"

    def save(self, execution):
        target = self._path(execution.execution_id)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(execution.to_dict(), sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        tmp.replace(target)

    def load(self, execution_id):
        try:
            return json.loads(self._path(execution_id).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise AgentProtocolError("execution not found") from exc

    def find_request(self, request_id):
        for path in self.root.glob("exec_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("request_id") == request_id:
                return data
        return None


class AgentGateway:
    def __init__(self, store, handlers=None, policy: CapabilityPolicy | None = None,
                 on_event=None):
        self.store = store
        if handlers is None:
            from ._handlers import DEFAULT_HANDLERS
            handlers = dict(DEFAULT_HANDLERS)
        self.handlers = handlers
        self.policy = policy if policy is not None else CapabilityPolicy()
        self._request_index = {}
        self._on_event = on_event  # callback(AgentEvent) invoked on every emit()

    def _notify(self, event):
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:
                # Listener failure must never break canonical execution.
                pass

    @staticmethod
    def _from_dict(data):
        e = Execution(
            data["execution_id"],
            data["request_id"],
            data["agent_id"],
            data["state"],
            data.get("digest"),
            data.get("document_address"),
            error=data.get("error"),
            result=data.get("result"),
        )
        e.runtime_log = list(data.get("runtime_log", []))
        e.artifact_refs = list(data.get("artifact_refs", []))
        e.checkpoints = list(data.get("checkpoints", []))
        e.events = [
            AgentEvent(
                x["execution_id"], x["event_id"], x["sequence"], x["type"],
                x["source"], x["payload"], x.get("version", EVENT_VERSION)
            )
            for x in data.get("events", [])
        ]
        return e

    def _find_idempotent(self, request_id):
        eid = self._request_index.get(request_id)
        if eid:
            return self._from_dict(self.store.load(eid))
        data = self.store.find_request(request_id)
        return self._from_dict(data) if data else None

    def _emit(self, e, event_type, source, payload):
        event = e.emit(event_type, source, payload)
        self._notify(event)
        return event

    def _new_execution(self, request):
        e = Execution(f"exec_{secrets.token_hex(12)}", request.request_id, request.agent_id)
        self._request_index[request.request_id] = e.execution_id
        self.store.save(e)
        return e

    def prepare(self, request):
        """Create an execution record in QUEUED state without running anything.

        Used by async transports (HTTP worker pool, queue-based runtimes) that
        want to reserve an execution_id immediately and execute the work on a
        background thread. Calling execute() with the same request_id afterwards
        is idempotent: it will find this record and continue from QUEUED.
        """
        request.validate()
        existing = self._find_idempotent(request.request_id)
        if existing:
            return existing
        e = self._new_execution(request)
        e.transition("QUEUED")
        self._emit(e,"execution.queued", "gateway", {})
        self.store.save(e)
        return e

    def _run(self, e, request):
        """Drive an already-created Execution to completion. Does NOT allocate a
        new id or validate idempotency; the caller owns that."""
        try:
            from ._cli import compile_source

            if e.state == "QUEUED":
                e.transition("VALIDATING")
                self._emit(e,"execution.validating", request.agent_id, {})
                self._emit(e,
                    "capability.requested",
                    request.agent_id,
                    {"capabilities": list(request.capabilities)},
                )
            elif e.state == "CREATED":
                e.transition("VALIDATING")
                self._emit(e,"execution.validating", request.agent_id, {})
                self._emit(e,
                    "capability.requested",
                    request.agent_id,
                    {"capabilities": list(request.capabilities)},
                )
            # if state is VALIDATING-or-beyond we are in a recovery/restart; the
            # existing execute() path currently only handles fresh starts, which
            # matches previous behaviour. For QUEUED/CREATED we proceed below.

            granted_caps, denied_caps = self.policy.authorize(request.agent_id, request.capabilities)
            if denied_caps:
                self._emit(e,"capability.denied", "policy", {"denied": denied_caps, "agent_id": request.agent_id})
                e.error = {"code": "capability_denied", "denied": denied_caps}
                e.transition("FAILED")
                self._emit(e,"execution.failed", "gateway", e.error)
                self.store.save(e)
                return e

            result = compile_source(Path(request.source))
            e.document_address = result.document.addr
            if e.state == "VALIDATING":
                e.transition("READY")
                self._emit(e,"execution.ready", request.agent_id, {"document": e.document_address})
                self.store.save(e)

            e.transition("RUNNING")
            self._emit(e,"execution.running", request.agent_id, {})
            self._emit(e,
                "capability.granted",
                "policy",
                {"capabilities": granted_caps},
            )
            events = [
                canonical.Event(
                    f"{e.execution_id}:input:{i}",
                    x["type"],
                    i,
                    request.agent_id,
                    x.get("payload", {}),
                )
                for i, x in enumerate(request.events, 1)
            ]
            run = runtime.execute(
                result.document,
                self.handlers,
                granted=granted_caps,
                log=events,
                workers=request.workers,
            )
            e.runtime_log = runtime.log_to_canonical(run.log)
            e.digest = run.digest
            e.result = {
                "waves": run.waves,
                "executed": run.executed,
                "failures": len(run.failures),
                "speculation": dict(run.speculation),
            }
            for record in run.log:
                if isinstance(record, canonical.Commit):
                    for ref in record.artifacts:
                        e.artifact_refs.append({"id": ref["$cas"], "node": record.node})
            if run.failures:
                has_cap_denied = any(f.reason == "capability_denied" for f in run.failures)
                e.error = {
                    "code": "capability_denied" if has_cap_denied else "execution_failed",
                    "count": len(run.failures),
                    "failures": [
                        {"node": f.node, "reason": f.reason, "message": f.message}
                        for f in run.failures
                    ],
                }
                if has_cap_denied:
                    for f in run.failures:
                        if f.reason == "capability_denied":
                            self._emit(e,
                                "capability.denied",
                                "policy",
                                {"node": f.node, "reason": f.reason, "message": f.message},
                            )
                e.transition("FAILED")
                self._emit(e,"execution.failed", "runtime", e.error)
            else:
                e.transition("COMPLETED")
                self._emit(e,"execution.completed", "runtime", {"digest": e.digest})
        except Exception as exc:
            e.error = {
                "code": "agent_execution_error",
                "message": str(exc),
                "type": type(exc).__name__,
            }
            if e.state not in TERMINAL:
                e.transition("FAILED")
            self._emit(e,"execution.failed", "gateway", e.error)
        self.store.save(e)
        return e

    def execute(self, request):
        request.validate()
        existing = self._find_idempotent(request.request_id)
        if existing:
            return existing

        e = self._new_execution(request)
        return self._run(e, request)

    def execute_queued(self, execution_id: str, request) -> Execution:
        """Resume a QUEUED execution created by prepare() and run it to
        completion. Used by background worker threads."""
        request.validate()
        data = self.store.load(execution_id)
        e = self._from_dict(data)
        if e.state in TERMINAL:
            return e
        return self._run(e, request)

    def status(self, execution_id):
        return self.store.load(execution_id)

    def cancel(self, execution_id: str, reason: str = "user_cancelled") -> Execution:
        data = self.store.load(execution_id)
        e = self._from_dict(data)
        if e.state in TERMINAL:
            raise AgentProtocolError(f"cannot cancel terminal execution in state {e.state}")
        e.transition("CANCELLED")
        e.error = {"code": "cancelled", "reason": reason}
        self._emit(e,"execution.cancelled", "gateway", {"reason": reason})
        self.store.save(e)
        return e

    def pause(self, execution_id: str, reason: str = "paused") -> Execution:
        data = self.store.load(execution_id)
        e = self._from_dict(data)
        if e.state != "RUNNING":
            raise AgentProtocolError(f"cannot pause execution in state {e.state}")
        e.transition("PAUSED")
        self._emit(e,"execution.paused", "gateway", {"reason": reason})
        self.store.save(e)
        return e

    def resume(self, execution_id: str) -> Execution:
        data = self.store.load(execution_id)
        e = self._from_dict(data)
        if e.state != "PAUSED":
            raise AgentProtocolError(f"cannot resume execution in state {e.state}")
        e.transition("RUNNING")
        self._emit(e,"execution.resumed", "gateway", {})
        self.store.save(e)
        return e

    def checkpoint(self, execution_id: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        data = self.store.load(execution_id)
        e = self._from_dict(data)
        record = {
            "checkpoint_id": f"chk_{len(e.checkpoints) + 1}_{secrets.token_hex(4)}",
            "execution_id": execution_id,
            "state": e.state,
            "digest": e.digest,
            "sequence": len(e.events),
            "metadata": metadata or {},
        }
        e.checkpoints.append(record)
        self._emit(e,"checkpoint.created", "gateway", record)
        self.store.save(e)
        return record

    def verify(self, execution_id, source):
        recorded = self._from_dict(self.store.load(execution_id))
        if recorded.state != "COMPLETED":
            return {
                "execution_id": execution_id,
                "verified": False,
                "reason": "execution_not_completed",
                "recorded_digest": recorded.digest,
            }
        replayed = self.replay(execution_id, source)
        return {
            "execution_id": execution_id,
            "verified": (
                replayed["document_matches"]
                and replayed["matches"]
                and recorded.digest is not None
            ),
            "document_address": replayed["document_address"],
            "recorded_digest": recorded.digest,
            "recomputed_digest": replayed["digest"],
        }

    def replay(self, execution_id, source):
        e = self._from_dict(self.store.load(execution_id))
        from ._cli import compile_source as _compile_source

        document = _compile_source(Path(source)).document
        log = runtime.log_from_canonical(e.runtime_log)
        state = canonical.reduce(document, log)
        digest_value = canonical.digest(state)
        return {
            "execution_id": execution_id,
            "document_address": document.addr,
            "document_matches": document.addr == e.document_address,
            "digest": digest_value,
            "matches": digest_value == e.digest,
        }


class ReferenceAgent:
    """Minimal reference agent demonstrating complete lifecycle:
    intent -> validate -> execute -> capability decision -> artifact -> checkpoint -> replay -> verify.
    """

    def __init__(self, gateway: AgentGateway, agent_id: str = "reference-agent"):
        self.gateway = gateway
        self.agent_id = agent_id

    def run_lifecycle(
        self,
        source: str | Path,
        *,
        request_id: str,
        capabilities: tuple[str, ...] = ("weave.artifact",),
        events: tuple[dict[str, Any], ...] = (),
    ) -> dict[str, Any]:
        req = AgentRequest(
            request_id=request_id,
            agent_id=self.agent_id,
            source=str(source),
            capabilities=capabilities,
            events=events,
        )
        req.validate()
        execution = self.gateway.execute(req)
        status = self.gateway.status(execution.execution_id)
        chk = self.gateway.checkpoint(execution.execution_id, {"intent": "reference_audit"})
        replay = self.gateway.replay(execution.execution_id, str(source))
        verification = self.gateway.verify(execution.execution_id, str(source))
        return {
            "execution_id": execution.execution_id,
            "state": execution.state,
            "digest": execution.digest,
            "status": status,
            "checkpoint": chk,
            "artifact_refs": execution.artifact_refs,
            "replay": replay,
            "verification": verification,
        }
