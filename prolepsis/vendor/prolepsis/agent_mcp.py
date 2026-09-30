"""Minimal MCP (Model Context Protocol) server for Prolepsis Agent Platform.

Implements the stdio transport of JSON-RPC 2.0 MCP (2024-11-05 schema) without
third-party dependencies. This lets any MCP-compatible client (Claude Desktop,
Cursor, agentic IDEs, etc.) drive agent workflows through Prolepsis as a set
of typed tools:

  prolepsis_execute     — compile and execute a .prolepsis source against the
                          in-process AgentGateway.
  prolepsis_status      — fetch current state, digest, events and artifacts of
                          an execution.
  prolepsis_cancel      — cancel a non-terminal execution.
  prolepsis_checkpoint  — create an audit checkpoint.
  prolepsis_artifacts   — list content-addressed artifact refs for an execution.
  prolepsis_verify      — replay and cryptographically verify a prior execution.

The server uses the same AgentGateway and JsonExecutionStore as the HTTP
server and CLI. It is launched by MCP clients as a stdio subprocess and
respects the zero-ambient-authority boundary: every execution request must
explicitly declare requested capabilities and the absolute path to a
.prolepsis source file.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

from .agent_platform import (
    AgentGateway,
    AgentProtocolError,
    AgentRequest,
    CapabilityPolicy,
    JsonExecutionStore,
    PROTOCOL_VERSION,
)

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "prolepsis-agent"
SERVER_VERSION = "0.39.0"


def _jsonrpc_result(id_value: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_value, "result": result}


def _jsonrpc_error(id_value: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id_value, "error": err}


def _text_content(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "prolepsis_execute",
        "description": (
            "Compile a .prolepsis source file and execute it through the Prolepsis "
            "Agent Gateway. Returns the execution record including execution_id, "
            "state, digest, events, and artifact references. The source path must "
            "be absolute; capabilities must be explicitly listed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Absolute path to the .prolepsis source file."},
                "request_id": {"type": "string", "description": "Idempotency key for this request."},
                "agent_id": {"type": "string", "description": "Caller-supplied agent identifier."},
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Capabilities the agent is requesting (e.g. weave.artifact).",
                    "default": ["weave.artifact"],
                },
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "payload": {"type": "object"},
                        },
                        "required": ["type"],
                    },
                    "description": "Canonical input events feeding the execution.",
                    "default": [],
                },
                "workers": {"type": "integer", "minimum": 1, "default": 1},
            },
            "required": ["source", "request_id", "agent_id"],
        },
    },
    {
        "name": "prolepsis_status",
        "description": "Fetch the current status record of an existing Prolepsis execution by id.",
        "inputSchema": {
            "type": "object",
            "properties": {"execution_id": {"type": "string"}},
            "required": ["execution_id"],
        },
    },
    {
        "name": "prolepsis_cancel",
        "description": "Cancel a non-terminal Prolepsis execution.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "execution_id": {"type": "string"},
                "reason": {"type": "string", "default": "user_cancelled"},
            },
            "required": ["execution_id"],
        },
    },
    {
        "name": "prolepsis_checkpoint",
        "description": "Create an immutable audit checkpoint for an execution.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "execution_id": {"type": "string"},
                "metadata": {"type": "object", "default": {}},
            },
            "required": ["execution_id"],
        },
    },
    {
        "name": "prolepsis_artifacts",
        "description": "List content-addressed artifact refs produced by a completed execution.",
        "inputSchema": {
            "type": "object",
            "properties": {"execution_id": {"type": "string"}},
            "required": ["execution_id"],
        },
    },
    {
        "name": "prolepsis_verify",
        "description": (
            "Replay an execution from its source and verify the recorded digest "
            "matches the recomputed digest. Returns verified=true/false."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "execution_id": {"type": "string"},
                "source": {"type": "string", "description": "Absolute path to the .prolepsis source file."},
            },
            "required": ["execution_id", "source"],
        },
    },
]


class McpServer:
    """Stdio JSON-RPC 2.0 server exposing Prolepsis as MCP tools."""

    def __init__(
        self,
        state_dir: str | Path,
        *,
        allowed_capabilities: frozenset[str] | None = None,
        in_stream=None,
        out_stream=None,
    ):
        self.state_dir = Path(state_dir)
        self.policy = CapabilityPolicy(allowed_capabilities)
        self.gateway = AgentGateway(JsonExecutionStore(self.state_dir), policy=self.policy)
        self._lock = threading.Lock()
        self._initialized = False
        self._in = in_stream if in_stream is not None else sys.stdin.buffer
        self._out = out_stream if out_stream is not None else sys.stdout.buffer

    # ----- framing -----
    def _write_message(self, obj: dict[str, Any]) -> None:
        body = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._out.write(header + body)
        self._out.flush()

    def _read_message(self) -> dict[str, Any] | None:
        """Read one LSP-style framed JSON-RPC message over stdio."""
        headers: dict[str, str] = {}
        while True:
            line = self._in.readline()
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                break
            try:
                decoded = line.decode("ascii").strip()
            except UnicodeDecodeError:
                continue
            if ":" in decoded:
                key, value = decoded.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        length_header = headers.get("content-length")
        if not length_header:
            return None
        try:
            length = int(length_header)
        except ValueError:
            return None
        body = self._in.read(length)
        if len(body) < length:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    # ----- dispatch -----
    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if "method" not in message:
            return _jsonrpc_error(message.get("id"), -32600, "invalid request")
        method = message["method"]
        params = message.get("params") or {}
        msg_id = message.get("id")

        if method == "initialize":
            self._initialized = True
            return _jsonrpc_result(msg_id, {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })
        if method == "notifications/initialized":
            return None  # notification, no response
        if not self._initialized:
            return _jsonrpc_error(msg_id, -32002, "server not initialized")
        if method == "ping":
            return _jsonrpc_result(msg_id, {})
        if method == "tools/list":
            return _jsonrpc_result(msg_id, {"tools": TOOL_SCHEMAS})
        if method == "tools/call":
            return self._tool_call(msg_id, params)
        return _jsonrpc_error(msg_id, -32601, f"method not found: {method}")

    def _tool_call(self, msg_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            with self._lock:
                if name == "prolepsis_execute":
                    result = self._tool_execute(arguments)
                elif name == "prolepsis_status":
                    result = self._tool_status(arguments)
                elif name == "prolepsis_cancel":
                    result = self._tool_cancel(arguments)
                elif name == "prolepsis_checkpoint":
                    result = self._tool_checkpoint(arguments)
                elif name == "prolepsis_artifacts":
                    result = self._tool_artifacts(arguments)
                elif name == "prolepsis_verify":
                    result = self._tool_verify(arguments)
                else:
                    return _jsonrpc_error(msg_id, -32601, f"unknown tool: {name}")
            return _jsonrpc_result(msg_id, {
                "content": [_text_content(json.dumps(result, sort_keys=True, indent=2))],
                "isError": bool(result.get("error")) if isinstance(result, dict) else False,
            })
        except AgentProtocolError as exc:
            return _jsonrpc_result(msg_id, {
                "content": [_text_content(f"protocol_error: {exc}")],
                "isError": True,
            })
        except Exception as exc:  # pragma: no cover - defensive
            return _jsonrpc_result(msg_id, {
                "content": [_text_content(f"{type(exc).__name__}: {exc}")],
                "isError": True,
            })

    def _require_source(self, arguments: dict[str, Any]) -> str:
        source = arguments.get("source")
        if not source:
            raise AgentProtocolError("source is required")
        source_path = Path(source)
        if not source_path.is_absolute():
            raise AgentProtocolError("source must be an absolute filesystem path")
        if not source_path.exists():
            raise FileNotFoundError(f"source file not found: {source}")
        return str(source_path)

    def _tool_execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        for required in ("source", "request_id", "agent_id"):
            if not arguments.get(required):
                raise AgentProtocolError(f"{required} is required")
        request = AgentRequest(
            request_id=str(arguments["request_id"]),
            agent_id=str(arguments["agent_id"]),
            source=self._require_source(arguments),
            capabilities=tuple(arguments.get("capabilities", ["weave.artifact"])),
            events=tuple(arguments.get("events", ())),
            workers=int(arguments.get("workers", 1)),
        )
        execution = self.gateway.execute(request)
        return execution.to_dict()

    def _tool_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        execution_id = arguments.get("execution_id")
        if not execution_id:
            raise AgentProtocolError("execution_id is required")
        return self.gateway.status(execution_id)

    def _tool_cancel(self, arguments: dict[str, Any]) -> dict[str, Any]:
        execution_id = arguments.get("execution_id")
        if not execution_id:
            raise AgentProtocolError("execution_id is required")
        execution = self.gateway.cancel(execution_id, reason=str(arguments.get("reason", "user_cancelled")))
        return execution.to_dict()

    def _tool_checkpoint(self, arguments: dict[str, Any]) -> dict[str, Any]:
        execution_id = arguments.get("execution_id")
        if not execution_id:
            raise AgentProtocolError("execution_id is required")
        metadata = arguments.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise AgentProtocolError("metadata must be an object")
        return self.gateway.checkpoint(execution_id, metadata=metadata)

    def _tool_artifacts(self, arguments: dict[str, Any]) -> dict[str, Any]:
        execution_id = arguments.get("execution_id")
        if not execution_id:
            raise AgentProtocolError("execution_id is required")
        data = self.gateway.status(execution_id)
        return {"execution_id": execution_id, "artifact_refs": data.get("artifact_refs", [])}

    def _tool_verify(self, arguments: dict[str, Any]) -> dict[str, Any]:
        execution_id = arguments.get("execution_id")
        if not execution_id:
            raise AgentProtocolError("execution_id is required")
        source = self._require_source(arguments)
        return self.gateway.verify(execution_id, source)

    # ----- main loop -----
    def run(self) -> None:
        while True:
            message = self._read_message()
            if message is None:
                return
            if "id" not in message and message.get("method", "").startswith("notifications/"):
                # notification — handle but send no response
                self.handle(message)
                continue
            response = self.handle(message)
            if response is not None:
                self._write_message(response)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Prolepsis MCP stdio server")
    parser.add_argument("--state-dir", default=".prolepsis-mcp-state", help="directory for execution state JSON files")
    parser.add_argument(
        "--allow",
        action="append",
        default=None,
        help="capability to allow (repeatable); if omitted all capabilities are granted (open local policy)",
    )
    args = parser.parse_args()
    allowed = frozenset(args.allow) if args.allow else None
    server = McpServer(args.state_dir, allowed_capabilities=allowed)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
