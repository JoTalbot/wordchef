"""Zero-dependency HTTP/REST server for Agent Platform v1.

Exposes AgentGateway operations over plain HTTP/JSON using stdlib http.server.
No third-party framework is required; the server binds to 127.0.0.1 by default
to avoid accidental exposure, matching the security boundary documented in
docs/SECURITY.md (zero ambient authority — bind address, auth token and any
listener addresses are explicit operator choices, never ambient open ports).

API surface (all endpoints return JSON unless noted):
  POST   /v1/executions                       create and start execution (sync or async)
  GET    /v1/executions                       list recent execution summaries
  GET    /v1/executions/{execution_id}        get execution status
  GET    /v1/executions/{execution_id}/events stream execution events as SSE (text/event-stream)
  GET    /v1/events                           stream server-wide events as SSE
  POST   /v1/executions/{execution_id}/cancel
  POST   /v1/executions/{execution_id}/pause
  POST   /v1/executions/{execution_id}/resume
  POST   /v1/executions/{execution_id}/checkpoint
  GET    /v1/executions/{execution_id}/artifacts
  POST   /v1/executions/{execution_id}/replay
  POST   /v1/executions/{execution_id}/verify
  GET    /v1/health                           liveness probe (no auth required)
  GET    /v1/ready                            readiness probe (auth required when token set)
  GET    /v1/version                          protocol and server version
  GET    /metrics                             Prometheus text exposition (text/plain)

Authentication: pass `--auth-token TOKEN` (or set PROLEPSIS_AUTH_TOKEN) to
require `Authorization: Bearer <TOKEN>` on every endpoint except /v1/health.
Tokens are compared with hmac.compare_digest to prevent timing attacks. Without
a configured token, the server operates in trusted-loopback mode (as before).

Async execution: pass `"async": true` in POST /v1/executions to enqueue work
onto a bounded background pool. The response is HTTP 202 with the QUEUED
summary; clients poll GET /v1/executions/{id} or subscribe to GET
/v1/executions/{id}/events (SSE) until state is COMPLETED/FAILED/CANCELLED.
"""
from __future__ import annotations

import hmac
import json
import queue
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .agent_platform import (
    AgentEvent,
    AgentGateway,
    AgentProtocolError,
    AgentRequest,
    JsonExecutionStore,
    PROTOCOL_VERSION,
)

SERVER_VERSION = "0.39.0"
MAX_BODY_BYTES = 10 * 1024 * 1024
DEFAULT_ASYNC_WORKERS = 2
DEFAULT_ASYNC_QUEUE = 128
SSE_KEEPALIVE_SECONDS = 15.0
SSE_HISTORY_LIMIT = 128  # per-execution history buffer for late subscribers


class AgentHttpError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


class EventHub:
    """Bounded pub/sub fanout for AgentEvents.

    Subscribers receive events matching a predicate; a per-execution ring
    buffer replays recent events to clients that connect after emission.
    Back-pressured or slow subscribers are dropped (SSE is fire-and-forget;
    durable delivery belongs to the canonical event log, not this live fanout).
    """

    _SENTINEL = object()

    def __init__(self):
        self._lock = threading.Lock()
        self._subs: dict[int, dict[str, Any]] = {}
        self._next_id = 1
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._global: list[dict[str, Any]] = []

    def _event_to_dict(self, event: AgentEvent) -> dict[str, Any]:
        return {
            "version": event.version,
            "execution_id": event.execution_id,
            "event_id": event.event_id,
            "sequence": event.sequence,
            "type": event.type,
            "source": event.source,
            "payload": event.payload,
        }

    def publish(self, event: AgentEvent) -> None:
        payload = self._event_to_dict(event)
        dead = []
        with self._lock:
            # append to per-execution ring buffer
            hist = self._history.setdefault(event.execution_id, [])
            hist.append(payload)
            if len(hist) > SSE_HISTORY_LIMIT:
                del hist[:-SSE_HISTORY_LIMIT]
            # append global ring
            self._global.append(payload)
            if len(self._global) > SSE_HISTORY_LIMIT * 4:
                del self._global[:-SSE_HISTORY_LIMIT * 4]
            for sub_id, sub in list(self._subs.items()):
                if sub["predicate"](payload):
                    try:
                        sub["q"].put_nowait(payload)
                    except queue.Full:
                        dead.append(sub_id)
            for sid in dead:
                self._subs.pop(sid, None)

    def subscribe(self, predicate=lambda e: True, history: list[dict[str, Any]] | None = None,
                  max_queue: int = 256) -> tuple[int, queue.Queue]:
        q: queue.Queue = queue.Queue(maxsize=max_queue)
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            self._subs[sub_id] = {"q": q, "predicate": predicate}
            if history:
                for item in history:
                    try:
                        q.put_nowait(item)
                    except queue.Full:
                        break
        return sub_id, q

    def unsubscribe(self, sub_id: int) -> None:
        with self._lock:
            self._subs.pop(sub_id, None)

    def history_for(self, execution_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history.get(execution_id, ()))

    def global_history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._global)


class PrometheusMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.executions_started = 0
        self.executions_completed = 0
        self.executions_failed = 0
        self.executions_cancelled = 0
        self.executions_async_queued = 0
        self.checkpoints_created = 0
        self.capability_denials = 0
        self.http_requests = 0
        self.http_errors = 0
        self.sse_connections = 0
        self.sse_disconnects = 0
        self.replay_verified = 0
        self.replay_mismatches = 0
        self.auth_failures = 0
        self.queue_depth = 0

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            current = getattr(self, name, None)
            if current is None:
                setattr(self, name, value)
            else:
                setattr(self, name, current + value)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "prolepsis_executions_started_total": self.executions_started,
                "prolepsis_executions_completed_total": self.executions_completed,
                "prolepsis_executions_failed_total": self.executions_failed,
                "prolepsis_executions_cancelled_total": self.executions_cancelled,
                "prolepsis_executions_async_queued_total": self.executions_async_queued,
                "prolepsis_checkpoints_created_total": self.checkpoints_created,
                "prolepsis_capability_denials_total": self.capability_denials,
                "prolepsis_http_requests_total": self.http_requests,
                "prolepsis_http_errors_total": self.http_errors,
                "prolepsis_sse_connections_total": self.sse_connections,
                "prolepsis_sse_disconnects_total": self.sse_disconnects,
                "prolepsis_auth_failures_total": self.auth_failures,
                "prolepsis_replay_verified_total": self.replay_verified,
                "prolepsis_replay_mismatches_total": self.replay_mismatches,
                "prolepsis_async_queue_depth": self.queue_depth,
                "prolepsis_server_uptime_seconds": max(0, int(time.time() - self.started_at)),
            }

    def render(self) -> str:
        snap = self.snapshot()
        gauges = {"prolepsis_async_queue_depth", "prolepsis_server_uptime_seconds"}
        lines = []
        for key, value in snap.items():
            lines.append(f"# HELP {key} Agent Platform server metric.")
            lines.append(f"# TYPE {key} {'gauge' if key in gauges else 'counter'}")
            lines.append(f"{key} {value}")
        return "\n".join(lines) + "\n"


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length_header = handler.headers.get("Content-Length")
    if length_header is None:
        return {}
    try:
        length = int(length_header)
    except ValueError as exc:
        raise AgentHttpError(400, "invalid_content_length", "Content-Length must be an integer") from exc
    if length < 0 or length > MAX_BODY_BYTES:
        raise AgentHttpError(413, "payload_too_large", f"request body exceeds {MAX_BODY_BYTES // (1024*1024)} MiB limit")
    raw = handler.rfile.read(length) if length else b""
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentHttpError(400, "invalid_json", f"request body must be valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentHttpError(400, "invalid_json", "request body must be a JSON object")
    return data


def _split_path(path: str) -> tuple[str, ...]:
    return tuple(segment for segment in urlparse(path).path.split("/") if segment)


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _format_sse(event: str, payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n".encode("utf-8")


class AgentHttpHandler(BaseHTTPRequestHandler):
    server: "AgentHttpServer"
    server_version = f"ProlepsisAgentServer/{SERVER_VERSION}"
    # Endpoints that do NOT require Bearer authentication.
    PUBLIC_PATHS = {("v1", "health")}

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    # ----- authentication -----
    def _check_auth(self) -> None:
        if self.server.auth_token is None:
            return
        if _split_path(self.path) in self.PUBLIC_PATHS:
            return
        header = self.headers.get("Authorization", "")
        if not header.lower().startswith("bearer "):
            self.server.metrics.inc("auth_failures")
            raise AgentHttpError(401, "unauthorized", "missing or malformed Authorization: Bearer <token>")
        provided = header.split(" ", 1)[1].strip().encode("utf-8")
        expected = self.server.auth_token.encode("utf-8")
        if not hmac.compare_digest(provided, expected):
            self.server.metrics.inc("auth_failures")
            raise AgentHttpError(401, "unauthorized", "invalid bearer token")

    # ----- I/O helpers -----
    def _write(self, status: int, body: bytes, content_type: str, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Prolepsis-Protocol", PROTOCOL_VERSION)
        self.send_header("X-Prolepsis-Server", SERVER_VERSION)
        self.send_header("Cache-Control", "no-cache, no-transform")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _write_json(self, status: int, payload: dict[str, Any], extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._write(status, body, "application/json; charset=utf-8", extra_headers)

    def _write_text(self, status: int, text: str, content_type: str = "text/plain; charset=utf-8",
                    extra_headers: dict[str, str] | None = None) -> None:
        self._write(status, text.encode("utf-8"), content_type, extra_headers)

    def _write_error(self, exc: AgentHttpError) -> None:
        self.server.metrics.inc("http_errors")
        self._write_json(exc.status, exc.to_dict())

    def _gateway(self) -> AgentGateway:
        return self.server.gateway

    def _handle_sse(self, execution_id: str | None) -> None:
        """Stream events using Server-Sent Events. Never returns until the client
        disconnects, the server shuts down, or (for per-execution streams) a
        terminal state is reached."""
        if execution_id is not None:
            # Verify execution exists before opening the stream.
            current = self._gateway().status(execution_id)
            predicate = lambda e, eid=execution_id: e.get("execution_id") == eid
            history = self.server.hub.history_for(execution_id)
        else:
            current = None
            predicate = lambda e: True
            history = self.server.hub.global_history()

        sub_id, q = self.server.hub.subscribe(predicate=predicate, history=history)
        self.server.metrics.inc("sse_connections")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            # Send an initial open event so clients know the subscription worked.
            self.wfile.write(_format_sse("open", {
                "execution_id": execution_id,
                "server": SERVER_VERSION,
                "protocol": PROTOCOL_VERSION,
            }))
            self.wfile.flush()
            last_keepalive = time.time()
            while True:
                # Detect client disconnect via wfile errors.
                if self.server._shutdown_event.is_set():
                    self.wfile.write(_format_sse("server_shutdown", {}))
                    self.wfile.flush()
                    break
                try:
                    payload = q.get(timeout=1.0)
                except queue.Empty:
                    if time.time() - last_keepalive > SSE_KEEPALIVE_SECONDS:
                        try:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            break
                        last_keepalive = time.time()
                    continue
                try:
                    self.wfile.write(_format_sse("agent_event", payload))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
                # For per-execution streams, end on terminal events.
                if execution_id is not None and payload.get("type", "").startswith((
                    "execution.completed", "execution.failed", "execution.cancelled"
                )):
                    self.wfile.write(_format_sse("done", {"execution_id": execution_id,
                                                          "state": _terminal_from_event(payload["type"])}))
                    self.wfile.flush()
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.server.hub.unsubscribe(sub_id)
            self.server.metrics.inc("sse_disconnects")

    # ----- HTTP verbs -----
    def do_GET(self) -> None:
        self.server.metrics.inc("http_requests")
        try:
            self._check_auth()
            parts = _split_path(self.path)
            if parts == ("v1", "health"):
                return self._write_json(200, {"status": "ok", "protocol": PROTOCOL_VERSION})
            if parts == ("v1", "ready"):
                ready = not self.server._shutdown_event.is_set() and self.server._accepting
                return self._write_json(200 if ready else 503, {
                    "status": "ready" if ready else "draining",
                    "protocol": PROTOCOL_VERSION,
                    "auth_required": self.server.auth_token is not None,
                    "queue_depth": self.server.metrics.snapshot()["prolepsis_async_queue_depth"],
                })
            if parts == ("v1", "version"):
                return self._write_json(200, {
                    "server": SERVER_VERSION,
                    "protocol": PROTOCOL_VERSION,
                    "auth_required": self.server.auth_token is not None,
                })
            if parts == ("metrics",):
                return self._write_text(200, self.server.metrics.render())
            if parts == ("v1", "events"):
                return self._handle_sse(None)
            if parts == ("v1", "executions"):
                return self._handle_list()
            if len(parts) == 3 and parts[0] == "v1" and parts[1] == "executions":
                return self._handle_status(parts[2])
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "executions" and parts[3] == "events":
                return self._handle_sse(parts[2])
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "executions" and parts[3] == "artifacts":
                return self._handle_artifacts(parts[2])
            raise AgentHttpError(404, "not_found", f"unknown route: {self.path}")
        except AgentHttpError as exc:
            self._write_error(exc)
        except AgentProtocolError as exc:
            self._write_error(AgentHttpError(400, "protocol_error", str(exc)))
        except Exception as exc:
            self._write_error(AgentHttpError(500, "internal_error", f"{type(exc).__name__}: {exc}"))

    def do_POST(self) -> None:
        self.server.metrics.inc("http_requests")
        try:
            self._check_auth()
            body = _read_json(self)
            parts = _split_path(self.path)
            if parts == ("v1", "executions"):
                return self._handle_execute(body)
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "executions" and parts[3] == "cancel":
                return self._handle_cancel(parts[2], body)
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "executions" and parts[3] == "pause":
                return self._handle_pause(parts[2], body)
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "executions" and parts[3] == "resume":
                return self._handle_resume(parts[2])
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "executions" and parts[3] == "checkpoint":
                return self._handle_checkpoint(parts[2], body)
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "executions" and parts[3] == "replay":
                return self._handle_replay(parts[2], body)
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "executions" and parts[3] == "verify":
                return self._handle_verify(parts[2], body)
            raise AgentHttpError(404, "not_found", f"unknown route: {self.path}")
        except AgentHttpError as exc:
            self._write_error(exc)
        except AgentProtocolError as exc:
            self._write_error(AgentHttpError(400, "protocol_error", str(exc)))
        except FileNotFoundError as exc:
            self._write_error(AgentHttpError(400, "source_not_found", str(exc)))
        except Exception as exc:
            self._write_error(AgentHttpError(500, "internal_error", f"{type(exc).__name__}: {exc}"))

    # ----- helpers -----
    def _build_request(self, body: dict[str, Any]) -> AgentRequest:
        for required in ("source", "request_id", "agent_id"):
            if not body.get(required):
                raise AgentHttpError(400, "missing_field", f"field '{required}' is required")
        source_path = Path(body["source"])
        if not source_path.is_absolute():
            raise AgentHttpError(400, "invalid_source", "source must be an absolute filesystem path")
        workers = int(body.get("workers", 1))
        if workers < 1:
            raise AgentHttpError(400, "invalid_workers", "workers must be >= 1")
        capabilities = body.get("capabilities", ())
        events = body.get("events", ())
        if not isinstance(capabilities, (list, tuple)) or not all(isinstance(c, str) for c in capabilities):
            raise AgentHttpError(400, "invalid_capabilities", "capabilities must be a list of strings")
        if not isinstance(events, (list, tuple)) or not all(
            isinstance(e, dict) and isinstance(e.get("type"), str) for e in events
        ):
            raise AgentHttpError(400, "invalid_events", "events must be a list of {type, payload?} objects")
        return AgentRequest(
            request_id=str(body["request_id"]),
            agent_id=str(body["agent_id"]),
            source=str(source_path),
            capabilities=tuple(capabilities),
            events=tuple(events),
            workers=workers,
        )

    # ----- handlers -----
    def _handle_list(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        limit = min(int(query.get("limit", ["25"])[0]), 200)
        summaries = []
        state_dir = self.server.state_dir
        paths = sorted(state_dir.glob("exec_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        for p in paths:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            summaries.append({
                "execution_id": data.get("execution_id"),
                "request_id": data.get("request_id"),
                "agent_id": data.get("agent_id"),
                "state": data.get("state"),
                "digest": data.get("digest"),
                "document_address": data.get("document_address"),
                "error": data.get("error"),
            })
        self._write_json(200, {"executions": summaries, "limit": limit})

    def _handle_execute(self, body: dict[str, Any]) -> None:
        request = self._build_request(body)
        is_async = _parse_bool(body.get("async"), default=False)
        self.server.metrics.inc("executions_started")
        if is_async:
            if not self.server._accepting:
                raise AgentHttpError(503, "server_draining", "server is shutting down; not accepting new async work")
            with self.server._lock:
                existing = self._gateway()._find_idempotent(request.request_id)
                if existing is None:
                    prepared = self._gateway().prepare(request)
                    queued_id = prepared.execution_id
                else:
                    queued_id = existing.execution_id
            try:
                self.server.enqueue(queued_id, request)
            except queue.Full as exc:
                raise AgentHttpError(503, "queue_full", "async execution queue is full; retry later") from exc
            self.server.metrics.inc("executions_async_queued")
            record = self._gateway().status(queued_id)
            return self._write_json(202, record, {"Location": f"/v1/executions/{queued_id}"})
        with self.server._lock:
            execution = self._gateway().execute(request)
        self._record_terminal_metrics(execution.to_dict())
        status = 200 if execution.state == "COMPLETED" else 409 if execution.state == "FAILED" else 200
        self._write_json(status, execution.to_dict())

    def _record_terminal_metrics(self, record: dict[str, Any]) -> None:
        state = record.get("state")
        if state == "COMPLETED":
            self.server.metrics.inc("executions_completed")
        elif state == "FAILED":
            self.server.metrics.inc("executions_failed")
            if record.get("error", {}).get("code") == "capability_denied":
                self.server.metrics.inc("capability_denials")
        elif state == "CANCELLED":
            self.server.metrics.inc("executions_cancelled")

    def _handle_status(self, execution_id: str) -> None:
        self._write_json(200, self._gateway().status(execution_id))

    def _handle_artifacts(self, execution_id: str) -> None:
        data = self._gateway().status(execution_id)
        self._write_json(200, {"execution_id": execution_id, "artifact_refs": data.get("artifact_refs", [])})

    def _handle_cancel(self, execution_id: str, body: dict[str, Any]) -> None:
        reason = str(body.get("reason", "user_cancelled"))
        with self.server._lock:
            execution = self._gateway().cancel(execution_id, reason=reason)
        self._record_terminal_metrics(execution.to_dict())
        self._write_json(200, execution.to_dict())

    def _handle_pause(self, execution_id: str, body: dict[str, Any]) -> None:
        reason = str(body.get("reason", "paused"))
        with self.server._lock:
            execution = self._gateway().pause(execution_id, reason=reason)
        self._write_json(200, execution.to_dict())

    def _handle_resume(self, execution_id: str) -> None:
        with self.server._lock:
            execution = self._gateway().resume(execution_id)
        self._write_json(200, execution.to_dict())

    def _handle_checkpoint(self, execution_id: str, body: dict[str, Any]) -> None:
        metadata = body.get("metadata", {})
        if not isinstance(metadata, dict):
            raise AgentHttpError(400, "invalid_metadata", "metadata must be a JSON object")
        with self.server._lock:
            record = self._gateway().checkpoint(execution_id, metadata=metadata)
        self.server.metrics.inc("checkpoints_created")
        self._write_json(200, record)

    def _require_source(self, body: dict[str, Any]) -> str:
        source = body.get("source")
        if not source:
            raise AgentHttpError(400, "missing_field", "field 'source' is required")
        source_path = Path(source)
        if not source_path.is_absolute():
            raise AgentHttpError(400, "invalid_source", "source must be an absolute filesystem path")
        return str(source_path)

    def _handle_replay(self, execution_id: str, body: dict[str, Any]) -> None:
        source = self._require_source(body)
        result = self._gateway().replay(execution_id, source)
        if result.get("matches"):
            self.server.metrics.inc("replay_verified")
        else:
            self.server.metrics.inc("replay_mismatches")
        self._write_json(200, result)

    def _handle_verify(self, execution_id: str, body: dict[str, Any]) -> None:
        source = self._require_source(body)
        result = self._gateway().verify(execution_id, source)
        if result.get("verified"):
            self.server.metrics.inc("replay_verified")
        else:
            self.server.metrics.inc("replay_mismatches")
        self._write_json(200 if result.get("verified") else 409, result)


def _terminal_from_event(event_type: str) -> str:
    if event_type == "execution.completed":
        return "COMPLETED"
    if event_type == "execution.failed":
        return "FAILED"
    if event_type == "execution.cancelled":
        return "CANCELLED"
    return event_type


class AgentHttpServer(ThreadingHTTPServer):
    """Threaded HTTP server hosting a single AgentGateway with a bounded async
    worker pool, pub/sub event hub, Prometheus metrics and optional bearer-token
    authentication."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        host: str,
        port: int,
        state_dir: str | Path,
        *,
        allowed_capabilities: frozenset[str] | None = None,
        async_workers: int = DEFAULT_ASYNC_WORKERS,
        async_queue_size: int = DEFAULT_ASYNC_QUEUE,
        auth_token: str | None = None,
    ):
        from . import agent_platform as ap
        self.state_dir = Path(state_dir)
        self.policy = ap.CapabilityPolicy(allowed_capabilities)
        self.hub = EventHub()
        self.auth_token = auth_token
        self.gateway = AgentGateway(
            JsonExecutionStore(self.state_dir),
            policy=self.policy,
            on_event=self.hub.publish,
        )
        self.metrics = PrometheusMetrics()
        self._lock = threading.Lock()
        self._task_queue: queue.Queue[tuple[str, AgentRequest] | None] = queue.Queue(maxsize=async_queue_size)
        self._shutdown_event = threading.Event()
        self._accepting = True
        self._worker_threads: list[threading.Thread] = []
        for i in range(max(1, async_workers)):
            t = threading.Thread(target=self._worker_loop, name=f"prolepsis-agent-w-{i}", daemon=True)
            t.start()
            self._worker_threads.append(t)
        super().__init__((host, port), AgentHttpHandler)

    def _worker_loop(self) -> None:
        while True:
            try:
                task = self._task_queue.get(timeout=0.5)
            except queue.Empty:
                if self._shutdown_event.is_set() and self._task_queue.empty():
                    return
                continue
            if task is None:
                self._task_queue.task_done()
                return
            self.metrics.inc("queue_depth", -1)
            execution_id, request = task
            try:
                with self._lock:
                    execution = self.gateway.execute_queued(execution_id, request)
                self._process_metrics(execution.to_dict())
            except Exception:
                pass
            finally:
                self._task_queue.task_done()

    def _process_metrics(self, record: dict[str, Any]) -> None:
        state = record.get("state")
        if state == "COMPLETED":
            self.metrics.inc("executions_completed")
        elif state == "FAILED":
            self.metrics.inc("executions_failed")
            if record.get("error", {}).get("code") == "capability_denied":
                self.metrics.inc("capability_denials")
        elif state == "CANCELLED":
            self.metrics.inc("executions_cancelled")

    def enqueue(self, execution_id: str, request: AgentRequest) -> None:
        self.metrics.inc("queue_depth", 1)
        self._task_queue.put_nowait((execution_id, request))

    def generate_token(self) -> str:
        """Generate and install a random bearer token (utility for operators)."""
        token = secrets.token_urlsafe(32)
        self.auth_token = token
        return token

    def graceful_shutdown(self, timeout: float = 30.0) -> None:
        self._accepting = False
        end = time.time() + timeout
        while not self._task_queue.empty() and time.time() < end:
            time.sleep(0.1)
        for _ in self._worker_threads:
            try:
                self._task_queue.put_nowait(None)
            except queue.Full:
                pass
        self._shutdown_event.set()
        # Give SSE handlers a moment to flush server_shutdown events.
        time.sleep(0.2)
        self.shutdown()
        for t in self._worker_threads:
            t.join(timeout=2.0)
        self.server_close()

    def serve_forever_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="prolepsis-agent-http", daemon=True)
        thread.start()
        return thread
