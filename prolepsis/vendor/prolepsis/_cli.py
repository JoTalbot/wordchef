#!/usr/bin/env python3
"""Prolepsis command-line surface for Canonical WEAVE programs.

The CLI is deliberately thin: source compilation enters an existing C23
frontend, execution enters the reference runtime, replay enters the pure
kernel, and CAS commands enter the persistent runtime store. No command adds
canonical semantics.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import canonical, jacquard, runtime, third_frontend, workflow_frontend
from .agent_platform import AgentGateway, AgentRequest, JsonExecutionStore
from .adapter_abi import AdapterRequest, adapt_request, adapt_with
from .persistent_cas import PersistentArtifactStore


def compile_source(path: Path):
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        source = jacquard.load_pattern(path)
        return adapt_request(AdapterRequest(source, str(path)))
    if suffix == ".json":
        source = json.loads(path.read_text(encoding="utf-8"))
        return adapt_with(AdapterRequest(source, str(path)), workflow_frontend.lower)
    if suffix in {".prolepsis", ".prs"}:
        source = path.read_text(encoding="utf-8")
        return adapt_with(
            AdapterRequest(source, str(path)),
            lambda text: third_frontend.lower(third_frontend.parse(text)),
        )
    raise ValueError(f"unsupported source extension: {path.suffix}")


def load_document(path: Path):
    value = canonical.loads(path.read_text(encoding="utf-8"))
    return canonical.IRDocument.from_canonical(value)


def parse_event(spec: str, clock: int):
    try:
        event_type, payload = spec.split("=", 1)
    except ValueError as exc:
        raise ValueError("event must use EVENT_TYPE=JSON_OBJECT") from exc
    if not event_type:
        raise ValueError("event type must not be empty")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("event payload must be a JSON object")
    return canonical.Event(
        f"cli-{clock}", event_type, clock, "cli", value
    )


# Re-export for backward compatibility with any caller that imported
# `generic_handler` from the top-level `prolepsis` CLI module.
from ._handlers import generic_handler  # noqa: E402,F401


def _agent_gateway(state_dir):
    return AgentGateway(JsonExecutionStore(state_dir))

def cmd_agent_execute(args):
    gateway=_agent_gateway(args.state_dir)
    events=tuple(parse_event(spec,i) for i,spec in enumerate(args.event,1))
    request=AgentRequest(args.request_id,args.agent_id,args.source,
                         capabilities=tuple(args.grant),events=tuple(
                             {"type":e.event_type,"payload":e.payload} for e in events),
                         workers=args.workers)
    result=gateway.execute(request)
    print(canonical.dumps(result.to_dict()))
    return 0 if result.state=="COMPLETED" else 2

def cmd_agent_status(args):
    print(canonical.dumps(_agent_gateway(args.state_dir).status(args.execution_id)))
    return 0

def cmd_agent_verify(args):
    result=_agent_gateway(args.state_dir).verify(args.execution_id,args.source)
    print(canonical.dumps(result))
    return 0 if result["verified"] else 2

def cmd_agent_replay(args):
    result=_agent_gateway(args.state_dir).replay(args.execution_id,args.source)
    print(canonical.dumps(result))
    return 0 if result["matches"] else 2

def cmd_agent_cancel(args):
    result = _agent_gateway(args.state_dir).cancel(args.execution_id, reason=args.reason)
    print(canonical.dumps(result.to_dict()))
    return 0

def cmd_agent_checkpoint(args):
    result = _agent_gateway(args.state_dir).checkpoint(args.execution_id)
    print(canonical.dumps(result))
    return 0

def cmd_agent_conformance(args):
    import subprocess
    # tests/ lives at the repo root, next to the top-level prolepsis.py shim,
    # not inside the installed `prolepsis` package. When running from an
    # installed wheel the conformance suite is still exercised by
    # `tests/test_agent_platform.py` in the source tree; the CLI gate exits
    # with a clear message if the suite is not found.
    repo_root = Path(__file__).resolve().parent.parent
    candidate = repo_root / "tests" / "test_agent_platform.py"
    if not candidate.exists():
        # When installed as a package, conformance must be invoked from the
        # source tree (we intentionally do not ship tests with the wheel).
        print("prolepsis: agent-conformance requires the tests/ directory from the source tree",
              file=sys.stderr)
        return 2
    proc = subprocess.run([sys.executable, str(candidate)])
    return proc.returncode


def cmd_agent_serve(args):
    import os
    from .agent_server import AgentHttpServer, SERVER_VERSION, DEFAULT_ASYNC_WORKERS, DEFAULT_ASYNC_QUEUE
    allowed = frozenset(args.allow) if args.allow else None
    token = args.auth_token or os.environ.get("PROLEPSIS_AUTH_TOKEN")
    if args.generate_token:
        token = __import__("secrets").token_urlsafe(32)
        print(f"prolepsis: generated bearer token: {token}", file=sys.stderr)
    server = AgentHttpServer(
        args.host, args.port, args.state_dir,
        allowed_capabilities=allowed,
        async_workers=int(args.async_workers),
        async_queue_size=int(args.queue_size),
        auth_token=token,
    )
    auth_note = "auth=on" if token else "auth=off (loopback-trust)"
    print(
        f"prolepsis agent-serve {SERVER_VERSION} listening on http://{args.host}:{args.port} "
        f"(state_dir={args.state_dir}, async_workers={args.async_workers}, queue={args.queue_size}, {auth_note})",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nprolepsis: graceful shutdown", file=sys.stderr)
        server.graceful_shutdown(timeout=args.drain_timeout)
    else:
        server.server_close()
    return 0


def cmd_agent_mcp(args):
    from .agent_mcp import McpServer
    allowed = frozenset(args.allow) if args.allow else None
    server = McpServer(args.state_dir, allowed_capabilities=allowed)
    server.run()
    return 0

def cmd_compile(args):
    result = compile_source(Path(args.source))
    text = canonical.dumps(result.document.to_canonical())
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    print(f"document: {result.document.addr}", file=sys.stderr)


def cmd_check(args):
    result = compile_source(Path(args.source))
    print("OK")
    print(f"source: {args.source}")
    print(f"ir: {result.document.ir_version}")
    print(f"address: {result.document.addr}")


def cmd_run(args):
    result = compile_source(Path(args.source))
    events = [parse_event(spec, i) for i, spec in enumerate(args.event, 1)]
    store = PersistentArtifactStore(args.store) if args.store else runtime.ArtifactStore()
    previous = runtime.read_log(args.log) if args.log and Path(args.log).exists() else []
    result_run = runtime.execute(
        result.document,
        {"weave.artifact": generic_handler,
         "jacquard.render": generic_handler,
         "jacquard.warp": generic_handler,
         "jacquard.unravel": generic_handler},
        granted=args.grant,
        log=previous + events,
        store=store,
        workers=args.workers,
    )
    if args.log:
        Path(args.log).unlink(missing_ok=True)
        runtime.append_log(args.log, result_run.log)
    if args.store:
        roots = [artifact for record in result_run.log
                 if isinstance(record, canonical.Commit)
                 for artifact in record.artifacts]
        store.write_manifest("latest", roots)
    print(f"address: {result.document.addr}")
    print(f"digest: {result_run.digest}")
    print(f"waves: {result_run.waves}")
    print(f"executed: {result_run.executed}")
    if result_run.failures:
        print(f"failures: {len(result_run.failures)}", file=sys.stderr)
        return 2
    return 0


def cmd_replay(args):
    document = load_document(Path(args.document))
    log = runtime.read_log(args.log)
    state = canonical.reduce(document, log)
    print(f"address: {document.addr}")
    print(f"digest: {canonical.digest(state)}")
    if args.output:
        Path(args.output).write_text(
            canonical.dumps(state) + "\n", encoding="utf-8"
        )
    else:
        print(canonical.dumps(state))


def cmd_diff(args):
    before = canonical.loads(Path(args.before).read_text(encoding="utf-8"))
    after = canonical.loads(Path(args.after).read_text(encoding="utf-8"))
    diff = runtime.state_diff(before, after)
    print(canonical.dumps(diff))


def cmd_inspect(args):
    document = load_document(Path(args.document))
    print(f"address: {document.addr}")
    print(f"ir_version: {document.ir_version}")
    print(f"compatibility: {document.compatibility_version}")
    print(f"nodes: {len(document.nodes)}")
    print(f"events: {len(document.events)}")
    print("node_ids: " + ", ".join(node.id for node in document.nodes))


def cmd_gc(args):
    store = PersistentArtifactStore(args.store)
    if args.verify:
        result = store.verify()
        print(canonical.dumps(result))
        return 0
    result = store.reclaim_live()
    print(canonical.dumps(result))


def parser():
    p = argparse.ArgumentParser(
        prog="prolepsis",
        description="Compile, validate, execute, replay and inspect WEAVE programs.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    compile_p = sub.add_parser("compile", help="lower source to Canonical IR")
    compile_p.add_argument("source")
    compile_p.add_argument("-o", "--output")
    compile_p.set_defaults(func=cmd_compile)

    check = sub.add_parser("check", help="compile and validate source")
    check.add_argument("source")
    check.set_defaults(func=cmd_check)

    run = sub.add_parser("run", help="execute source through the reference runtime")
    run.add_argument("source")
    run.add_argument("--event", action="append", default=[],
                     help="EVENT_TYPE=JSON_OBJECT, repeatable")
    run.add_argument("--grant", action="append", default=[])
    run.add_argument("--workers", type=int, default=1)
    run.add_argument("--log")
    run.add_argument("--store", help="persistent CAS directory")
    run.set_defaults(func=cmd_run)

    replay = sub.add_parser("replay", help="replay a canonical JSONL log")
    replay.add_argument("document")
    replay.add_argument("log")
    replay.add_argument("-o", "--output")
    replay.set_defaults(func=cmd_replay)

    diff = sub.add_parser("diff", help="diff two canonical state JSON files")
    diff.add_argument("before")
    diff.add_argument("after")
    diff.set_defaults(func=cmd_diff)

    inspect = sub.add_parser("inspect", help="inspect a canonical IR document")
    inspect.add_argument("document")
    inspect.set_defaults(func=cmd_inspect)

    agent_execute = sub.add_parser("agent-execute", help="execute through Agent Protocol v1")
    agent_execute.add_argument("source")
    agent_execute.add_argument("--state-dir", required=True)
    agent_execute.add_argument("--request-id", required=True)
    agent_execute.add_argument("--agent-id", required=True)
    agent_execute.add_argument("--event", action="append", default=[])
    agent_execute.add_argument("--grant", action="append", default=[])
    agent_execute.add_argument("--workers", type=int, default=1)
    agent_execute.set_defaults(func=cmd_agent_execute)

    agent_status = sub.add_parser("agent-status", help="inspect Agent Platform execution")
    agent_status.add_argument("execution_id")
    agent_status.add_argument("--state-dir", required=True)
    agent_status.set_defaults(func=cmd_agent_status)

    agent_verify = sub.add_parser("agent-verify", help="verify an Agent Platform execution")
    agent_verify.add_argument("execution_id")
    agent_verify.add_argument("source")
    agent_verify.add_argument("--state-dir", required=True)
    agent_verify.set_defaults(func=cmd_agent_verify)

    agent_replay = sub.add_parser("agent-replay", help="replay an Agent Platform execution")
    agent_replay.add_argument("execution_id")
    agent_replay.add_argument("source")
    agent_replay.add_argument("--state-dir", required=True)
    agent_replay.set_defaults(func=cmd_agent_replay)

    agent_cancel = sub.add_parser("agent-cancel", help="cancel an Agent Platform execution")
    agent_cancel.add_argument("execution_id")
    agent_cancel.add_argument("--state-dir", required=True)
    agent_cancel.add_argument("--reason", default="user_cancelled")
    agent_cancel.set_defaults(func=cmd_agent_cancel)

    agent_checkpoint = sub.add_parser("agent-checkpoint", help="create a checkpoint for an Agent Platform execution")
    agent_checkpoint.add_argument("execution_id")
    agent_checkpoint.add_argument("--state-dir", required=True)
    agent_checkpoint.set_defaults(func=cmd_agent_checkpoint)

    agent_conf = sub.add_parser("agent-conformance", help="run the Agent Platform conformance suite")
    agent_conf.set_defaults(func=cmd_agent_conformance)

    agent_serve = sub.add_parser("agent-serve", help="start the Agent Platform HTTP/REST server")
    agent_serve.add_argument("--state-dir", default=".prolepsis-state")
    agent_serve.add_argument("--host", default="127.0.0.1")
    agent_serve.add_argument("--port", type=int, default=7397)
    agent_serve.add_argument("--async-workers", type=int, default=2,
                             help="number of background worker threads for async=true executions")
    agent_serve.add_argument("--queue-size", type=int, default=128,
                             help="bounded capacity of the async execution queue")
    agent_serve.add_argument("--drain-timeout", type=float, default=30.0,
                             help="seconds to drain queued async work on SIGINT")
    agent_serve.add_argument("--auth-token", default=None,
                             help="bearer token required on all endpoints except /v1/health "
                                  "(falls back to PROLEPSIS_AUTH_TOKEN env var)")
    agent_serve.add_argument("--generate-token", action="store_true",
                             help="generate a random 32-byte bearer token at startup and print it to stderr")
    agent_serve.add_argument("--allow", action="append", default=None,
                             help="capability to allow (repeatable); if omitted, all declared capabilities are granted")
    agent_serve.set_defaults(func=cmd_agent_serve)

    agent_mcp = sub.add_parser("agent-mcp", help="start the Agent Platform MCP stdio server")
    agent_mcp.add_argument("--state-dir", default=".prolepsis-mcp-state")
    agent_mcp.add_argument("--allow", action="append", default=None,
                           help="capability to allow (repeatable); if omitted, all declared capabilities are granted")
    agent_mcp.set_defaults(func=cmd_agent_mcp)

    gc = sub.add_parser("gc", help="verify or reclaim a persistent CAS")
    gc.add_argument("store")
    gc.add_argument("--verify", action="store_true")
    gc.set_defaults(func=cmd_gc)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except (ValueError, KeyError, canonical.CanonicalError) as exc:
        print(f"prolepsis: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
