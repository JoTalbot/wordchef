"""Thin, stable Python SDK for Agent Protocol v1.

The SDK intentionally exposes Agent Platform concepts rather than Canonical IR
internals. It is a convenience facade over AgentGateway and therefore keeps
the reference execution semantics in one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .agent_platform import (
    AgentGateway,
    AgentRequest,
    JsonExecutionStore,
    PROTOCOL_VERSION,
    ReferenceAgent,
)


@dataclass(frozen=True)
class ExecutionRef:
    """Stable agent-facing reference returned by execute."""

    execution_id: str
    request_id: str
    state: str
    digest: str | None = None


@dataclass(frozen=True)
class ExecutionStatus:
    execution_id: str
    request_id: str
    agent_id: str
    state: str
    digest: str | None
    document_address: str | None
    artifact_refs: tuple[dict[str, str], ...]
    checkpoints: tuple[dict[str, Any], ...]
    error: dict[str, Any] | None
    result: dict[str, Any] | None


class AgentClient:
    """Agent-facing client for local Agent Protocol v1 execution."""

    def __init__(self, state_dir: str | Path, *, protocol_version: str = PROTOCOL_VERSION):
        if protocol_version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported agent protocol version: {protocol_version}")
        self.state_dir = Path(state_dir)
        self.gateway = AgentGateway(JsonExecutionStore(self.state_dir))

    def execute(
        self,
        source: str | Path,
        *,
        request_id: str,
        agent_id: str,
        capabilities: Iterable[str] = (),
        events: Iterable[dict[str, Any]] = (),
        workers: int = 1,
    ) -> ExecutionRef:
        request = AgentRequest(
            request_id=request_id,
            agent_id=agent_id,
            source=str(source),
            capabilities=tuple(capabilities),
            events=tuple(events),
            workers=workers,
            protocol_version=PROTOCOL_VERSION,
        )
        execution = self.gateway.execute(request)
        return ExecutionRef(
            execution_id=execution.execution_id,
            request_id=execution.request_id,
            state=execution.state,
            digest=execution.digest,
        )

    def status(self, execution_id: str) -> ExecutionStatus:
        data = self.gateway.status(execution_id)
        return ExecutionStatus(
            execution_id=data["execution_id"],
            request_id=data["request_id"],
            agent_id=data["agent_id"],
            state=data["state"],
            digest=data.get("digest"),
            document_address=data.get("document_address"),
            artifact_refs=tuple(data.get("artifact_refs", ())),
            checkpoints=tuple(data.get("checkpoints", ())),
            error=data.get("error"),
            result=data.get("result"),
        )

    def cancel(self, execution_id: str, reason: str = "user_cancelled") -> ExecutionStatus:
        self.gateway.cancel(execution_id, reason=reason)
        return self.status(execution_id)

    def pause(self, execution_id: str, reason: str = "paused") -> ExecutionStatus:
        self.gateway.pause(execution_id, reason=reason)
        return self.status(execution_id)

    def resume(self, execution_id: str) -> ExecutionStatus:
        self.gateway.resume(execution_id)
        return self.status(execution_id)

    def checkpoint(self, execution_id: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.gateway.checkpoint(execution_id, metadata=metadata)

    def artifacts(self, execution_id: str) -> tuple[dict[str, str], ...]:
        return self.status(execution_id).artifact_refs

    def replay(self, execution_id: str, source: str | Path) -> dict[str, Any]:
        return self.gateway.replay(execution_id, str(source))

    def verify(self, execution_id: str, source: str | Path) -> dict[str, Any]:
        return self.gateway.verify(execution_id, str(source))


def execute(
    source: str | Path,
    *,
    state_dir: str | Path,
    request_id: str,
    agent_id: str,
    capabilities: Iterable[str] = (),
    events: Iterable[dict[str, Any]] = (),
    workers: int = 1,
) -> ExecutionRef:
    """Convenience function for one-shot agent execution."""
    return AgentClient(state_dir).execute(
        source,
        request_id=request_id,
        agent_id=agent_id,
        capabilities=capabilities,
        events=events,
        workers=workers,
    )
