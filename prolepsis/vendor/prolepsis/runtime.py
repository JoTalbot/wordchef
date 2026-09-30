# -*- coding: utf-8 -*-
"""
REFERENCE RUNTIME on the Canonical WEAVE IR — Phases 3–6 of the Canonical
program. See docs/canonical-ir.md §6.

The kernel (canonical.py) defines what execution MEANS; this module is one
possible EXECUTOR of that meaning. The division of powers (C28):

  · the RUNTIME decides: wave order, thread count, what a handler does,
    where artifacts live — all policy, all replaceable;
  · the IR decides: what is ready, what is permitted, what the log may
    contain, what the state digest is — all semantics, all shared.

Executor policy v2 — WAVES: every wave takes ALL ready nodes at once.
workers=1 runs the wave serially in document order; workers>1 runs it on a
thread pool. Whatever the completion order, records are appended in
document order, so sequential and concurrent runs of the same document+log
produce byte-identical logs: concurrency changes WHEN, not WHAT (R8).

Contract invariants this runtime proves on every run (see self_check):

  R1  determinism — two runs of the same document+handlers+log produce the
      same log digest and the same state digest (C12);
  R2  replay independence — reduce(document, log) without any runtime
      reproduces the runtime's state byte-for-byte, also through canonical
      serialization (C19);
  R3  no smuggling — a handler that returns undeclared outputs, or a node
      without a handler, fails canonically; nothing leaks into the state;
  R4  capabilities — a node whose non-pure effects lack grants fails with a
      structured capability_denied error (C14);
  R5  cancellation — a canceled node is never executed again (C16);
  R6  constraints — a node whose outputs break its constraints fails with
      constraint_broken (C7/C26);
  R7  binding — inputs resolve deterministically from requirements; an
      ambiguous or unresolvable input fails with input_resolution;
  R8  concurrency invariance — serial and thread-pooled runs of the same
      document+log yield byte-identical logs and states.

Event sourcing (Phase 5): the log is the truth; append_log()/read_log()
persist it as canonical JSONL, and the kernel's transitions() walks the
per-record state chain. Every prefix of a runtime log is a valid state:
each node's completed record is written BEFORE its commit.

Run:   python3 runtime.py    (self-check R1–R8, then the demo; exit 1 on failure)

Honesty note (rule 15): the demo handlers SIMULATE document work — sleeps
stand for I/O. The thread overlap is real wall-clock concurrency for
I/O-bound work; CPU-bound parallelism is NOT claimed here (GIL) — the
multi-process mill in loom.py and Phase 10 distribution cover that.
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .canonical import (
    CanonicalError, IRDocument, IR_VERSION, Node, Event, EventDecl,
    ExecutionRecord, Commit, Constraint, Effect, IRError, Intent, Port,
    Provenance,
    check_capabilities, check_value, compute_frontier, digest, dumps,
    evaluate, loads, reduce, transitions, example_document,
)

DEMO_DIR = Path(__file__).resolve().parent / "demo" / "canonical"

# ───────────────────────── the artifact store ─────────────────────────

class ArtifactStore:
    """A content-addressed home for node outputs (C21). The kernel never
    touches storage — this is runtime infrastructure. put() is CAS: equal
    content is stored once (the WEAVE beam idea, one block per content)."""

    def __init__(self, blocks: dict | None = None):
        self._blocks: dict[str, str] = dict(blocks or {})   # ref-suffix -> payload

    def put(self, value) -> dict:
        text = dumps(value)
        ref = {"$cas": digest(value)}
        self._blocks.setdefault(ref["$cas"], text)
        return ref

    def get(self, ref: dict) -> str:
        if check_value(ref, "ref"):
            raise CanonicalError(f"not a ref: {ref!r}")
        return self._blocks[ref["$cas"]]

    def to_canonical(self) -> dict:
        return dict(self._blocks)

    @classmethod
    def from_canonical(cls, value: dict) -> "ArtifactStore":
        return cls(value)

    def __len__(self):
        return len(self._blocks)

    def bytes(self) -> int:
        return sum(len(v) for v in self._blocks.values())

    def reclaim(self, roots) -> dict:
        """Mark-and-sweep unreferenced CAS blocks using explicit live roots.

        GC is runtime infrastructure, not Canonical IR semantics: callers must
        provide the refs that remain live (for example committed manifests).
        Payloads may themselves contain CAS refs, so the mark walk follows the
        stored canonical JSON. Nothing outside the supplied root closure is
        reachable and it is safe to move to the runtime's thrums.
        """
        live: set[str] = set()
        pending = []

        def mark(value):
            if isinstance(value, dict):
                if set(value) == {"$cas"} and isinstance(value["$cas"], str):
                    ref = value["$cas"]
                    if ref not in live:
                        pending.append(ref)
                    return
                for child in value.values():
                    mark(child)
            elif isinstance(value, list):
                for child in value:
                    mark(child)

        for root in roots:
            mark(root)
        while pending:
            ref = pending.pop()
            if ref in live:
                continue
            payload = self._blocks.get(ref)
            if payload is None:
                raise CanonicalError(f"GC root references missing CAS block: {ref}")
            live.add(ref)
            mark(loads(payload))

        before = len(self._blocks)
        reclaimed = sorted(set(self._blocks) - live)
        reclaimed_bytes = sum(len(self._blocks[ref]) for ref in reclaimed)
        for ref in reclaimed:
            del self._blocks[ref]
        return {
            "live_blocks": len(live),
            "reclaimed_objects": len(reclaimed),
            "reclaimed_bytes": reclaimed_bytes,
            "reclaimed_refs": reclaimed,
            "before": before,
            "after": len(self._blocks),
        }


# ───────────────── the runtime binding contract (v1) ─────────────────
#
# HOW a node's declared inputs are resolved. Deterministic, documented, and
# a candidate for canonization once a second runtime exists (Phase 9):
#
#   B1  facts of the LATEST event of every event type in the node's
#       requirements (matched by fact name);
#   B2  ref outputs of COMMITTED dependency nodes (a commit's artifacts are
#       ordered by the dependency's output-port declaration; port names are
#       the dataflow addresses — a name offered by two dependencies with
#       DIFFERENT values is an ambiguity error);
#   B3  the document's initial state.
#
# An input found with two DIFFERENT canonical values is ambiguous and fails;
# an input found nowhere is unresolvable and fails. No hidden globals (C7).

def _latest_required_events(document: IRDocument, node: Node, log) -> dict:
    latest: dict[str, dict] = {}
    for record in log:
        if isinstance(record, Event) and record.event_type in node.requirements:
            latest[record.event_type] = record.payload
    return latest


def _committed_requirements(document: IRDocument, node: Node, log) -> dict:
    commits: dict[str, Commit] = {}
    for record in log:
        if isinstance(record, Commit) and record.node in node.requirements:
            commits[record.node] = record
    return commits


def resolve_inputs(document: IRDocument, node: Node, log,
                   predicted_events: dict | None = None) -> dict:
    """Bind every declared input of `node` per B1–B3 (canonical values).

    `predicted_events` (speculation policy, Phase 7 / directive s10) offers
    PREDICTED payloads for requirement events that have not happened yet.
    A prediction is a pure runtime suggestion: it can bind inputs for a
    speculative branch, but never enters the log or the state — authority
    still requires the real event (promotion validates by digest)."""
    available: dict[str, tuple[str, object]] = {}

    def offer(name: str, source: str, value):
        if name not in available:
            available[name] = (source, value)
            return
        seen_source, seen_value = available[name]
        if dumps(seen_value) != dumps(value):
            raise CanonicalError(
                f"node '{node.id}': input '{name}' is ambiguous — "
                f"'{seen_source}' and '{source}' disagree (B/C7)")

    latest = _latest_required_events(document, node, log)
    for event_type, payload in latest.items():
        for name, value in payload.items():
            offer(name, f"event {event_type}", value)
    if predicted_events:
        for event_type, payload in predicted_events.items():
            if event_type in node.requirements and event_type not in latest:
                for name, value in payload.items():
                    offer(name, f"predicted event {event_type}", value)

    by_id = {n.id: n for n in document.nodes}
    for dep, commit in _committed_requirements(document, node, log).items():
        refs = list(commit.artifacts)
        for index, (name, ctype) in enumerate(by_id[dep].outputs):
            if ctype == "ref" and index < len(refs):
                offer(name, f"commit {dep}", refs[index])

    for name, value in dict(document.initial).items():
        offer(name, "initial", value)

    resolved: dict[str, object] = {}
    for name, ctype in node.inputs:
        if name not in available:
            raise CanonicalError(
                f"node '{node.id}': input '{name}' cannot be resolved from "
                "requirements or initial state (B/C7)")
        source, value = available[name]
        violation = check_value(value, ctype)
        if violation:
            raise CanonicalError(f"node '{node.id}': input '{name}' from "
                                 f"{source}: {violation}")
        resolved[name] = value
    return resolved


# ───────────────────────── the handler contract ─────────────────────────

class RuntimeContext:
    """What a handler may touch: its own execution identity and the store.
    Everything else (the log, other nodes, the clock) is closed (C7)."""

    __slots__ = ("node", "execution_id", "store", "document")

    def __init__(self, node: Node, execution_id: str, store: ArtifactStore,
                 document: IRDocument):
        self.node = node
        self.execution_id = execution_id
        self.store = store
        self.document = document


def handler(node: Node, inputs: dict, ctx: RuntimeContext) -> dict:
    """The signature every handler implements. Returns EXACTLY the node's
    declared outputs (name -> canonical value). A handler may raise IRError
    to fail with its own classification; any other exception is classified
    as a transient, retryable handler_crash."""
    raise NotImplementedError


# ───────────── Phase 7: speculation (directive s10: prediction != authority) ─────────────

class SpeculativeBranch:
    """One speculative execution (C17 policy): the node ran against
    PREDICTED events and completed. The records exist — auditable,
    reproducible — but they are NOT in the canonical log: a prediction may
    be computed, stored and reused, yet MUST NOT become authoritative
    without canonical validation. Promotion (execute() with the real log)
    compares input digests: equal → the branch's records enter the log
    byte-identical to a normal execution; different → the branch is
    discarded (invalidation) and the node executes normally."""

    __slots__ = ("node", "inputs_digest", "records")

    def __init__(self, node: str, inputs_digest: str, records: list):
        self.node = node
        self.inputs_digest = inputs_digest
        self.records = records

    def to_canonical(self) -> dict:
        """Auditable form — never a log record (prediction, not authority)."""
        return {"node": self.node, "inputs": self.inputs_digest,
                "records": [r.to_canonical() for r in self.records]}


def _speculate(document: IRDocument, node: Node, handlers: dict, granted,
               log, store: ArtifactStore, predictions: dict):
    """Try ONE speculative branch for a speculative node. Returns the branch
    on success, None when the prediction cannot bind the inputs or the
    attempt failed (failed attempts are simply discarded — the node will
    execute normally when it becomes ready)."""
    predicted = {event_type: payload for event_type, payload
                 in predictions.items() if event_type in node.requirements}
    try:
        records, inputs = _execute_node(document, node, handlers, granted,
                                        log, store, predicted_events=predicted)
    except CanonicalError:
        return None
    if not records or not isinstance(records[-1], Commit):
        return None                       # failed speculation — no branch
    return SpeculativeBranch(node.id, digest(inputs), records)


# ───────────── Phase 7: unraveling (batch cancellation, loom semantics) ─────────────

def _unravel_records(document: IRDocument, log, unravel: dict) -> list:
    """Batch cancellation policy: when an unraveler EVENT arrives, every
    PENDING node that transitively depends on its ANCHOR node (through the
    requirements graph, C6 — the only canonical dependency graph) is
    canceled. What is already woven stays woven: completed/failed/canceled
    nodes are terminal (C16) and are never touched. The compensation is an
    ordinary node requiring the unraveler event — it becomes ready exactly
    like any other node. Idempotent: canceled nodes are never re-canceled."""
    triggered = {r.event_type for r in log
                 if isinstance(r, Event) and r.event_type in unravel}
    if not triggered:
        return []
    anchors = {unravel[event] for event in triggered}
    requirements = {n.id: set(n.requirements) for n in document.nodes}
    dependents: set[str] = set()
    stack = [nid for nid, reqs in requirements.items() if reqs & anchors]
    while stack:
        nid = stack.pop()
        if nid in dependents:
            continue
        dependents.add(nid)
        stack += [other for other, reqs in requirements.items()
                  if nid in reqs]
    statuses = {r.node for r in log if isinstance(r, ExecutionRecord)}
    return [ExecutionRecord(n.id, "canceled")
            for n in document.nodes
            if n.id in dependents and n.id not in statuses]


# ───────────────── approval / authority policy (runtime-only) ─────────────
class ApprovalPolicy:
    """Derive runtime capability grants from trusted APPROVED events."""
    def __init__(self, trusted_sources):
        normalized = {}
        for capability, sources in trusted_sources.items():
            if not isinstance(capability, str) or not capability:
                raise CanonicalError("approval capability must be a non-empty string")
            values = frozenset(sources)
            if not values or any(not isinstance(source, str) or not source for source in values):
                raise CanonicalError(f"approval capability '{capability}' needs non-empty trusted sources")
            normalized[capability] = values
        self._trusted_sources = normalized

    @property
    def capabilities(self):
        return tuple(sorted(self._trusted_sources))

    def evaluate(self, log):
        granted, accepted, rejected = set(), [], []
        for record in log:
            if not isinstance(record, Event) or record.event_type != "APPROVED":
                continue
            payload = record.payload
            if not isinstance(payload, dict):
                rejected.append((record.id, "approval payload is not a map")); continue
            capability, source = payload.get("what"), payload.get("source")
            if not isinstance(capability, str) or not capability:
                rejected.append((record.id, "approval 'what' must be a non-empty string")); continue
            if not isinstance(source, str) or not source:
                rejected.append((record.id, "approval 'source' must be a non-empty string")); continue
            trusted = self._trusted_sources.get(capability)
            if trusted is None:
                rejected.append((record.id, f"capability '{capability}' is not approvable")); continue
            if source not in trusted:
                rejected.append((record.id, f"source '{source}' is not trusted for '{capability}'")); continue
            granted.add(capability)
            accepted.append({"event_id": record.id, "capability": capability, "source": source})
        return {"capabilities": tuple(sorted(granted)), "accepted": tuple(accepted), "rejected": tuple(rejected)}

    def capabilities_for(self, log):
        return set(self.evaluate(log)["capabilities"])

    def audit(self, log):
        result = self.evaluate(log)
        return {"capabilities": list(result["capabilities"]), "accepted": [dict(x) for x in result["accepted"]], "rejected": [list(x) for x in result["rejected"]]}


# ───────────────────────────── the executor ─────────────────────────────

class RunResult:
    """Everything one execute() produces. The log is the source of truth:
    state is always re-derivable from document + log (R2). `branches` are
    the SURVIVING speculative branches (Phase 7): predictions, computed and
    stored, never authoritative — pass them to the next execute() call; a
    branch is promoted only when the real world delivers exactly the inputs
    it predicted (R9)."""

    def __init__(self, document: IRDocument, log: list, store: ArtifactStore,
                 waves: int, executed: int, branches=(),
                 speculation: dict | None = None):
        self.document = document
        self.log = tuple(log)
        self.store = store
        self.waves = waves
        self.executed = executed
        self.branches = tuple(branches)
        self.speculation = speculation or {}
        self.state = reduce(document, list(log))

    @property
    def digest(self) -> str:
        return digest(self.state)

    @property
    def failures(self) -> list:
        return [r for r in self.log
                if isinstance(r, ExecutionRecord) and r.status == "failed"]


def _attempt_number(node: Node, log) -> int:
    return 1 + sum(1 for r in log
                   if isinstance(r, ExecutionRecord) and r.node == node.id)


def _failure(node: Node, execution_id: str, error_type: str, cause: str,
             recoverability: str, retryable: bool) -> list:
    return [ExecutionRecord(node.id, "failed",
                            IRError(node.id, node.operation, error_type, cause,
                                    recoverability, retryable))]


def _execute_node(document: IRDocument, node: Node, handlers: dict,
                  granted, log, store: ArtifactStore,
                  predicted_events: dict | None = None):
    """Run one ready node; return (records, inputs) — the records to append
    and the bound inputs (the speculation policy fingerprints branches by
    input digest). The completed record is written BEFORE the commit: every
    prefix of the log stays a valid state (event sourcing, kernel
    transitions())."""
    execution_id = f"{node.id}#{_attempt_number(node, log)}"

    missing = check_capabilities(granted, node.effects)
    if missing:                                            # R4 / C14
        return _failure(node, execution_id, "capability_denied",
                        f"missing capabilities: {missing}", "permanent", False), None

    operation = handlers.get(node.operation)
    if operation is None:                                  # R3
        return _failure(node, execution_id, "unknown_operation",
                        f"no handler registered for '{node.operation}'",
                        "permanent", False), None

    try:
        inputs = resolve_inputs(document, node, log, predicted_events)
    except CanonicalError as e:                            # R7
        return _failure(node, execution_id, "input_resolution", str(e),
                        "permanent", False), None

    ctx = RuntimeContext(node, execution_id, store, document)
    try:
        outputs = operation(node, inputs, ctx)
    except IRError as e:                                   # handler's own class
        return _failure(node, execution_id, e.error_type, e.cause,
                        e.recoverability, e.retryable), None
    except Exception as e:                                 # unclassified crash
        return _failure(node, execution_id, "handler_crash", str(e),
                        "transient", True), None

    declared = {name: ctype for name, ctype in node.outputs}
    if set(outputs) != set(declared):                      # R3
        return _failure(node, execution_id, "output_mismatch",
                        f"declared {sorted(declared)}, got {sorted(outputs)}",
                        "permanent", False), None
    for name, ctype in node.outputs:
        violation = check_value(outputs[name], ctype)
        if violation:
            return _failure(node, execution_id, "output_mismatch",
                            f"output '{name}': {violation}", "permanent", False), None

    scope = {"f": {**inputs, **outputs}, "doc": dict(document.initial)}
    for constraint in node.constraints:                    # R6 / C26
        if not evaluate(constraint.expr, scope):
            return _failure(node, execution_id, "constraint_broken",
                            constraint.message or constraint.id, "permanent", False), None

    artifacts = tuple(outputs[name] for name, ctype in node.outputs
                      if check_value(outputs[name], "ref") is None)
    provenance = Provenance(
        producer=node.id, operation=node.operation,
        version=document.ir_version, execution_id=execution_id,
        inputs=tuple(v for v in inputs.values()
                     if check_value(v, "ref") is None))
    commit = Commit(execution_id, node.id,
                    digest({"node": node.id, "outputs": outputs}), artifacts,
                    provenance)
    return [ExecutionRecord(node.id, "completed"), commit], inputs


def execute(document: IRDocument, handlers: dict, *, granted=(), log=(),
            store: ArtifactStore | None = None, workers: int = 1,
            max_waves: int = 10000, predictions: dict | None = None,
            branches=(), unravel: dict | None = None,
            approval_policy: ApprovalPolicy | None = None) -> RunResult:
    """Drive the frontier in WAVES until nothing is ready.

    Every wave takes all ready nodes at once (frontier concurrency, C11):
    workers=1 runs the wave serially in document order; workers>1 runs it
    on a thread pool. Records are appended in document order regardless of
    completion order, so the log is identical either way (R8). Failed and
    canceled nodes are terminal (C16) and never re-run.

    Phase 7 policies (the kernel does not change — this is HOW, not WHAT):

    predictions  {event_type: payload} — predicted futures for speculation.
                 Speculative nodes (C17: blocked + speculative flag) run
                 against their predicted requirement events; the result is
                 kept as a SpeculativeBranch — computed, stored, NEVER in
                 the log (prediction != authority).
    branches     branches surviving a previous execute() call. A ready node
                 with a branch whose input digest matches reality is
                 PROMOTED: the branch's records enter the log byte-identical
                 to a normal execution (R9). A mismatched branch is
                 invalidated — the node just executes normally.
    unravel      {event_type: anchor_node_id} — batch cancellation: when an
                 unraveler event arrives, every PENDING node transitively
                 dependent on the anchor is canceled; the compensation is an
                 ordinary node requiring the event (R10).

    handlers: {operation symbol: callable(node, inputs, ctx) -> outputs}
    granted:  capability names granted for THIS run (C14).
    approval_policy: optional runtime authority policy. Trusted APPROVED events in log become additional capability grants.
    log:      prior records — the world's events and earlier executions.
    """
    if workers < 1:
        raise CanonicalError("workers must be >= 1")
    log = list(log)
    store = store or ArtifactStore()
    if approval_policy is not None:
        # APPROVED is a runtime-authority record, not a Canonical IR event.
        # Read it for authority, then keep it out of the canonical reducer so
        # C29 remains strict about undeclared events and the IR stays frozen.
        granted = set(granted) | approval_policy.capabilities_for(log)
        log = [record for record in log
               if not (isinstance(record, Event) and record.event_type == "APPROVED")]
    branches = {b.node: b for b in branches}
    counters = {"speculated": 0, "promoted": 0, "invalidated": 0,
                "canceled": 0}
    waves = executed = 0
    while True:
        # Phase 7: batch cancellation first — canceled is terminal (C16)
        if unravel:
            canceled = _unravel_records(document, log, unravel)
            if canceled:
                log.extend(canceled)
                counters["canceled"] += len(canceled)
        frontier = compute_frontier(document, log)
        wave = [n for n in document.nodes if frontier[n.id] == "ready"]
        # Phase 7: promotion — a ready node with a matching live branch
        promoted_wave = []
        for node in wave:
            branch = branches.pop(node.id, None)
            if branch is None:
                promoted_wave.append(node)
                continue
            try:
                actual = resolve_inputs(document, node, log)
            except CanonicalError:
                actual = None
            if actual is not None and digest(actual) == branch.inputs_digest:
                log.extend(branch.records)      # validated prediction
                counters["promoted"] += 1
            else:
                counters["invalidated"] += 1    # wrong guess — redo honestly
                promoted_wave.append(node)
        wave = promoted_wave
        # Phase 7: speculate BEFORE the wave decision — an empty ready wave
        # with a speculative node is still progress (a branch, never a log
        # record); a truly quiet frontier (nothing ready, nothing new to
        # speculate) is the only exit
        speculated_now = 0
        if predictions:
            for node in document.nodes:
                if (frontier.get(node.id) == "speculative"
                        and node.id not in branches):
                    branch = _speculate(document, node, handlers, granted,
                                        log, store, predictions)
                    if branch is not None:
                        branches[node.id] = branch
                        counters["speculated"] += 1
                        speculated_now += 1
        if not wave and speculated_now == 0:
            break
        if not wave:
            continue
        waves += 1
        executed += len(wave)
        if waves > max_waves:
            raise CanonicalError("executor exceeded max_waves — the frontier "
                                 "is not emptying")
        if workers > 1 and len(wave) > 1:
            with ThreadPoolExecutor(max_workers=min(workers, len(wave))) as pool:
                outcomes = list(pool.map(
                    lambda n: _execute_node(document, n, handlers, granted,
                                            log, store)[0], wave))
        else:
            outcomes = [_execute_node(document, n, handlers, granted, log,
                                      store)[0] for n in wave]
        for records in outcomes:      # document order, whatever finished first
            log.extend(records)
    result = RunResult(document, log, store, waves, executed,
                       branches=branches.values(), speculation=counters)
    # R1/R2 self-proof: the state must be re-derivable from the log alone
    replay = reduce(document, list(result.log))
    assert digest(replay) == result.digest, "runtime state is not replayable"
    return result


# ───────────── log serialization + persistence (cross-language) ─────────────

def log_to_canonical(log) -> list:
    """The ordered log as a canonical JSON-serializable list of records."""
    return [record.to_canonical() for record in log]


def log_from_canonical(records) -> list:
    """Rebuild the log from its canonical form (C19: the log carries no
    Python — these shapes are the language-neutral contract)."""
    log = []
    for record in records:
        if "event_type" in record:
            log.append(Event.from_canonical(record))
        elif "status" in record:
            log.append(ExecutionRecord.from_canonical(record))
        elif "state" in record and "artifacts" in record:
            log.append(Commit.from_canonical(record))
        else:
            raise CanonicalError(f"unknown log record {record!r} (C29)")
    return log


def append_log(path, records) -> None:
    """Append records to an append-only canonical JSONL file — the log is
    the truth (C8/C19), the file is one possible home for it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(dumps(record.to_canonical()) + "\n")


def read_log(path) -> list:
    """Rebuild the log from a canonical JSONL file — the replay path."""
    with open(path, encoding="utf-8") as handle:
        return log_from_canonical(
            [loads(line) for line in handle if line.strip()])


# ───────────────────── state diff (audit tooling) ─────────────────────

def state_diff(before: dict, after: dict) -> dict:
    """A canonical diff of two states (the 'diffable' requirement of the
    per-transition model): per section, what changed. Pure tooling — the
    semantics live in the digest chain, this only makes changes readable."""
    diff = {}
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if dumps(b) != dumps(a):
            diff[key] = {"before": b, "after": a}
    return diff


# ───────────────────────── the self-check (R1–R8) ─────────────────────────

def _demo_handlers():
    """Simulated operations for the example document. The work is SIMULATED
    (rule 15); the machinery around it is real."""
    def artifact(node, inputs, ctx):
        time.sleep(0.001)                    # a token gesture of "work"
        payload = {"operation": node.operation, "node": node.id,
                   "inputs": inputs}
        return {name: ctx.store.put(payload) for name, _ in node.outputs}
    return {"weave.artifact": artifact}


def _doc(nodes: list, events: list, requires: list) -> IRDocument:
    """A minimal well-formed document around the given nodes/events."""
    return IRDocument.from_canonical({
        "ir_version": IR_VERSION, "schema_version": "1.0.0",
        "compatibility_version": IR_VERSION,
        "namespaces": {}, "initial": {},
        "intent": {"goal": "g", "requires": requires, "constraints": [],
                   "prefer": []},
        "nodes": nodes, "events": events})


def self_check() -> None:
    document = example_document()
    handlers = _demo_handlers()

    def world_log(extra=()):
        return [
            Event("e1", "TAG_CUT", 1, "release-bot",
                  {"version": "v2.0.0", "date": "2026-11-01"}),
            Event("e2", "CI_GREEN", 2, "ci",
                  {"date": "2026-11-01", "passed": 612, "failed": 0}),
            *extra,
        ]

    published = Event("e3", "PUBLISHED", 3, "bot", {"date": "2026-11-02"})

    # R1 determinism: two runs, identical digests
    a = execute(document, handlers, granted=["external_service"],
                log=world_log([published]))
    b = execute(document, handlers, granted=["external_service"],
                log=world_log([published]))
    assert digest(log_to_canonical(a.log)) == digest(log_to_canonical(b.log))
    assert a.digest == b.digest

    # R2 replay independence — also through canonical serialization, and
    # every prefix is a valid state (event sourcing, kernel transitions)
    assert digest(reduce(document, log_from_canonical(
        loads(dumps(log_to_canonical(a.log)))))) == a.digest
    assert a.state["frontier"]["announcement"] == "completed"
    chain = transitions(document, a.log)
    assert len(chain) == len(a.log)
    assert chain[-1]["state_digest"] == a.digest
    # every completed node's commit carries its provenance (C20/§8)
    for record in a.log:
        if isinstance(record, Commit):
            assert record.provenance.producer == record.node
            assert record.provenance.execution_id == record.execution_id
    before_published = reduce(document, list(a.log[:6]))   # prefix state
    diff = state_diff(before_published, a.state)
    assert "frontier" in diff and "executions" in diff and "commits" in diff

    # R3 no smuggling
    sneaky = dict(handlers)
    sneaky["weave.artifact"] = lambda n, i, c: {n.outputs[0][0]: {"bogus": 1}}
    smuggled = execute(document, sneaky, log=world_log())
    assert smuggled.failures and smuggled.failures[0].error.error_type == \
        "output_mismatch"
    no_handler = execute(document, {}, log=world_log())
    assert no_handler.failures[0].error.error_type == "unknown_operation"

    # CAS reclamation: explicit roots keep transitive refs; orphaned blocks move to thrums.
    gc_store = ArtifactStore()
    leaf = gc_store.put({"payload": "live"})
    live_manifest = gc_store.put({"leaf": leaf})
    orphan = gc_store.put({"payload": "orphan"})
    stats = gc_store.reclaim([live_manifest])
    assert stats["live_blocks"] == 2
    assert stats["reclaimed_objects"] == 1
    assert stats["reclaimed_bytes"] > 0
    assert orphan["$cas"] in stats["reclaimed_refs"]
    assert len(gc_store) == 2
    try:
        gc_store.reclaim([{"$cas": "sha256:" + "f" * 64}])
    except CanonicalError as exc:
        assert "missing CAS block" in str(exc)
    else:
        raise AssertionError("GC must reject missing live roots")

    # R4 capabilities
    denied = execute(document, handlers, granted=[], log=world_log([published]))
    denial = [f for f in denied.failures if f.error.node == "announcement"]
    assert denial and denial[0].error.error_type == "capability_denied"

    # R5 cancellation: a canceled node is never executed
    canceled_log = [Event("e1", "TAG_CUT", 1, "release-bot",
                          {"version": "v2.0.0", "date": "2026-11-01"}),
                    ExecutionRecord("release_notes", "canceled")]
    result = execute(document, handlers, granted=[], log=canceled_log)
    assert result.state["executions"]["release_notes"] == "canceled"
    assert all(not isinstance(r, Commit) or r.node != "release_notes"
               for r in result.log)
    assert result.state["frontier"]["release_notes"] == "canceled"

    # R6 constraints: a node whose outputs break its own constraint fails
    strict = _doc(
        [{"id": "probe", "operation": "weave.artifact",
          "inputs": [], "outputs": [["failed", "int"]],
          "requirements": ["TAG_CUT"],
          "constraints": [{
              "id": "no_failures",
              "expr": {"eq": [{"get": ["f", "failed"]}, {"lit": 0}]},
              "message": "probe must not report failures"}],
          "effects": [{"kind": "pure"}]}],
        [{"event_type": "TAG_CUT", "facts": [["date", "date"]]}],
        ["probe"])
    liar = {"weave.artifact": lambda n, i, c: {"failed": 1}}
    broken = execute(strict, liar, log=[
        Event("w1", "TAG_CUT", 1, "w", {"date": "2026-11-01"})])
    assert broken.failures and broken.failures[0].error.error_type == \
        "constraint_broken"

    # R7 binding: ambiguity and unresolvable inputs both fail canonically
    clashing = [Event("e1", "TAG_CUT", 1, "bot",
                      {"version": "v2.0.0", "date": "2026-11-01"}),
                Event("e2", "CI_GREEN", 2, "ci",
                      {"date": "2026-12-31", "passed": 1, "failed": 0})]
    ambiguous = execute(document, handlers, log=clashing)
    assert ambiguous.failures[0].error.error_type == "input_resolution"
    orphan = _doc(
        [{"id": "orphan", "operation": "weave.artifact",
          "inputs": [["nowhere", "string"]], "outputs": [],
          "requirements": ["TAG_CUT"],
          "effects": [{"kind": "pure"}]}],
        [{"event_type": "TAG_CUT", "facts": [["date", "date"]]}],
        ["orphan"])
    unresolved = execute(orphan, {"weave.artifact": lambda n, i, c: {}}, log=[
        Event("w1", "TAG_CUT", 1, "w", {"date": "2026-11-01"})])
    assert unresolved.failures[0].error.error_type == "input_resolution"

    # R8 concurrency invariance: a diamond a → {b, c} → d — the pooled wave
    # and the serial wave produce byte-identical logs and states
    diamond = _doc(
        [{"id": "a", "operation": "weave.op", "inputs": [],
          "outputs": [["a", "ref"]], "requirements": ["START"],
          "effects": [{"kind": "pure"}]},
         {"id": "b", "operation": "weave.op", "inputs": [["a", "ref"]],
          "outputs": [["b", "ref"]], "requirements": ["a"],
          "effects": [{"kind": "pure"}]},
         {"id": "c", "operation": "weave.op", "inputs": [["a", "ref"]],
          "outputs": [["c", "ref"]], "requirements": ["a"],
          "effects": [{"kind": "pure"}]},
         {"id": "d", "operation": "weave.op",
          "inputs": [["b", "ref"], ["c", "ref"]],
          "outputs": [["d", "ref"]], "requirements": ["b", "c"],
          "effects": [{"kind": "pure"}]}],
        [{"event_type": "START", "facts": [["date", "date"]]}],
        ["d"])

    def slow(node, inputs, ctx):
        time.sleep(0.02)
        return {name: ctx.store.put({"node": node.id}) for name, _ in node.outputs}

    start = [Event("s1", "START", 1, "w", {"date": "2026-11-05"})]
    serial = execute(diamond, {"weave.op": slow}, log=start)
    pooled = execute(diamond, {"weave.op": slow}, workers=4, log=start)
    assert digest(log_to_canonical(serial.log)) == digest(log_to_canonical(pooled.log))
    assert serial.digest == pooled.digest
    assert serial.waves == pooled.waves == 3          # a | b,c | d
    assert serial.executed == pooled.executed == 4

    # R9 speculation invariance (Phase 7, directive s10): a speculative run
    # and an honest run of the same world produce the SAME canonical log and
    # digest — prediction never becomes authority by itself. Invalidation
    # (a wrong prediction) changes nothing either: the branch is discarded
    # and the node executes normally.
    spec_doc = _doc(
        [{"id": "notes", "operation": "weave.op",
          "inputs": [["date", "date"]], "outputs": [["n", "ref"]],
          "requirements": ["TAG"], "effects": [{"kind": "pure"}]},
         {"id": "preview", "operation": "weave.op",
          "inputs": [["date", "date"], ["passed", "int"]],
          "outputs": [["p", "ref"]], "requirements": ["TAG", "CI"],
          "constraints": [{"id": "sane",
                           "expr": {"ge": [{"get": ["f", "passed"]},
                                           {"lit": 0}]},
                           "message": "passed cannot be negative"}],
          "effects": [{"kind": "pure"}], "speculative": True}],
        [{"event_type": "TAG", "facts": [["date", "date"]]},
         {"event_type": "CI", "facts": [["date", "date"], ["passed", "int"]]}],
        ["notes", "preview"])

    def simple(node, inputs, ctx):
        return {name: ctx.store.put({"node": node.id,
                                     **{k: v for k, v in inputs.items()
                                        if k != "date"}})
                for name, _ in node.outputs}

    tag = [Event("t1", "TAG", 1, "w", {"date": "2026-11-05"})]
    green = Event("g1", "CI", 2, "ci",
                  {"date": "2026-11-05", "passed": 612})
    honest = execute(spec_doc, {"weave.op": simple}, log=tag + [green])
    # speculating run: first call with the TAG world + a CORRECT prediction
    right = execute(spec_doc, {"weave.op": simple}, log=tag,
                    predictions={"CI": {"date": "2026-11-05",
                                        "passed": 612}})
    assert right.speculation["speculated"] == 1
    assert len(right.branches) == 1
    assert digest(log_to_canonical(right.log)) != digest(
        log_to_canonical(honest.log))        # CI still missing — honest world
    promoted = execute(spec_doc, {"weave.op": simple},
                       log=list(right.log) + [green],
                       branches=right.branches)
    assert promoted.speculation["promoted"] == 1
    assert promoted.digest == honest.digest  # promotion == honest execution
    # the SAME records enter the log — the ORDER may differ (the event
    # arrives at a different point in the stream): runtime change is not
    # semantic change (s30), and the digest is the semantics
    assert sorted(map(dumps, log_to_canonical(promoted.log))) == sorted(
        map(dumps, log_to_canonical(honest.log)))
    # wrong prediction: the branch is invalidated, semantics still identical
    wrong = execute(spec_doc, {"weave.op": simple}, log=tag,
                    predictions={"CI": {"date": "2026-11-05",
                                        "passed": 611}})
    recovered = execute(spec_doc, {"weave.op": simple},
                        log=list(wrong.log) + [green],
                        branches=wrong.branches)
    assert recovered.speculation["invalidated"] == 1
    assert recovered.digest == honest.digest  # nothing leaked from the guess
    # dependency safety: the branch offers NOTHING to other nodes — it is
    # not in the log, and inputs resolve from the log only
    assert all(not isinstance(r, Commit) or r.node != "preview"
               for r in right.log)

    # R10 unraveling (Phase 7): the unraveler event cancels every PENDING
    # dependent of its anchor; woven work stays woven (C16); the
    # compensation — an ordinary node requiring the event — still runs;
    # every prefix replays (R2) and the cancellation is idempotent.
    un_doc = _doc(
        [{"id": "beam", "operation": "weave.op", "inputs": [],
          "outputs": [["b", "ref"]], "requirements": ["START"],
          "effects": [{"kind": "pure"}]},
         {"id": "mid", "operation": "weave.op", "inputs": [["b", "ref"]],
          "outputs": [["m", "ref"]], "requirements": ["beam"],
          "effects": [{"kind": "pure"}]},
         {"id": "far", "operation": "weave.op", "inputs": [["m", "ref"]],
          "outputs": [["f", "ref"]], "requirements": ["mid"],
          "effects": [{"kind": "pure"}]},
         {"id": "compensation", "operation": "weave.op",
          "inputs": [["reason", "string"]], "outputs": [["c", "ref"]],
          "requirements": ["REJECTED"], "effects": [{"kind": "pure"}]}],
        [{"event_type": "START", "facts": []},
         {"event_type": "REJECTED", "facts": [["reason", "string"]]}],
        ["far", "compensation"])
    world = [Event("s1", "START", 1, "w", {}),
             Event("r1", "REJECTED", 2, "maintainer",
                   {"reason": "regression in smoke tests"})]
    un = execute(un_doc, {"weave.op": simple}, log=world,
                 unravel={"REJECTED": "beam"})
    statuses = un.state["executions"]
    assert statuses == {"beam": "completed", "mid": "canceled",
                        "far": "canceled",
                        "compensation": "completed"}, statuses
    assert un.state["frontier"]["far"] == "canceled"
    assert "compensation" in un.state["commits"]   # woven compensation
    assert un.speculation["canceled"] == 2         # mid + far, one policy
    # idempotent: replaying the FULL log cancels nothing new
    again = execute(un_doc, {"weave.op": simple}, log=list(un.log),
                    unravel={"REJECTED": "beam"})
    assert again.speculation["canceled"] == 0
    assert again.digest == un.digest
    # woven work stays woven: an anchor that COMPLETED before the event is
    # never un-completed; only pending dependents fall
    seq = execute(un_doc, {"weave.op": simple},
                  log=[world[0],
                       ExecutionRecord("beam", "completed"),
                       Commit("beam#1", "beam", "sha256:" + "a" * 64, ()),
                       world[1]],
                  unravel={"REJECTED": "beam"})
    assert seq.state["executions"]["beam"] == "completed"
    assert seq.state["executions"]["mid"] == "canceled"

    print("SELF-CHECK R1–R10: all invariants hold")


# ───────────────────────────── the demo ─────────────────────────────

def mill_document(cloths: int) -> IRDocument:
    """The Phase 6 mill: `cloths` independent weaves fed by one event, then
    one beam assembling them. Port names are the dataflow addresses: every
    cloth exposes its own output port; the beam declares them all."""
    nodes = [{"id": f"cloth_{i}", "operation": "weave.cloth",
              "inputs": [["date", "date"]],
              "outputs": [[f"cloth_{i}", "ref"]],
              "requirements": ["LOOM_STARTED"],
              "effects": [{"kind": "pure"}]}
             for i in range(1, cloths + 1)]
    nodes.append({"id": "beam", "operation": "weave.beam",
                  "inputs": [[f"cloth_{i}", "ref"] for i in range(1, cloths + 1)],
                  "outputs": [["beam", "ref"]],
                  "requirements": [f"cloth_{i}" for i in range(1, cloths + 1)],
                  "effects": [{"kind": "pure"}]})
    return _doc(nodes, [{"event_type": "LOOM_STARTED", "facts": [["date", "date"]]}],
                ["beam"])


def demo() -> None:
    document = example_document()
    handlers = _demo_handlers()

    print("REFERENCE RUNTIME on the Canonical IR — phases 3–6")
    print(f"  document: {document.addr}")
    print("  (handler work is SIMULATED; the executor machinery is real)")

    # ── scene 1: the world speaks, the runtime weaves what it may ──
    log = [
        Event("e1", "TAG_CUT", 1, "release-bot",
              {"version": "v2.0.0", "date": "2026-11-01"}),
        Event("e2", "CI_GREEN", 2, "ci",
              {"date": "2026-11-01", "passed": 612, "failed": 0}),
    ]
    print("\n  [1] world events: TAG_CUT, CI_GREEN")
    started = time.perf_counter()
    first = execute(document, handlers, granted=["external_service"], log=log)
    elapsed = (time.perf_counter() - started) * 1000
    print(f"      executor: {first.waves} waves · {first.executed} executions · "
          f"{elapsed:.1f} ms machinery")
    print(f"      frontier: {dumps(first.state['frontier'])}")
    print("      'announcement' is speculative — NOT executed (Phase 7 policy)")

    # ── scene 2: the world speaks again; the speculative node ripens ──
    log2 = list(first.log) + [Event("e3", "PUBLISHED", 3, "bot",
                                    {"date": "2026-11-02"})]
    print("\n  [2] world event: PUBLISHED")
    second = execute(document, handlers, granted=["external_service"], log=log2,
                     store=first.store)
    print(f"      executor: {second.waves} wave · {second.executed} execution · "
          f"frontier: {dumps(second.state['frontier'])}")
    print(f"      state digest: {second.digest}")

    # ── scene 3: event sourcing — the chain, the file, the replay ──
    chain = transitions(document, second.log)
    path = DEMO_DIR / "log.jsonl"
    path.unlink(missing_ok=True)                 # a fresh demo log each run
    append_log(path, second.log)
    from_file = read_log(path)
    replayed = reduce(document, from_file)
    print(f"\n  [3] event sourcing: {len(chain)} transition records; "
          f"every prefix is a valid state")
    print(f"      chain: {chain[0]['state_digest'][:26]}… → "
          f"{chain[-1]['state_digest'][:26]}…")
    before = reduce(document, list(second.log[:6]))
    diff = state_diff(before, second.state)
    print(f"      state diff across the PUBLISHED wave: "
          f"{sorted(diff)} changed; 'announcement' → "
          f"{diff['frontier']['after']['announcement']}")
    print(f"      persisted {len(second.log)} records → {path.name} "
          f"({path.stat().st_size} bytes), replayed from the file: "
          f"byte-equal {digest(replayed) == second.digest}")

    # ── scene 4: the same world, without the external_service grant ──
    print("\n  [4] same world, no 'external_service' capability granted:")
    denied = execute(document, handlers, granted=[], log=list(log2))
    for failure in denied.failures:
        e = failure.error
        print(f"      {e.node} → FAILED {e.error_type} ({e.recoverability}, "
              f"retryable={e.retryable}): {e.cause}")
    again = execute(document, handlers, granted=["external_service"],
                    log=list(denied.log))
    assert again.digest == denied.digest
    print(f"      C16: re-run with the grant changes nothing "
          f"({again.digest == denied.digest}) — failure is terminal")

    # ── scene 5: THE IR MILL — frontier concurrency, honestly measured ──
    cloths = 24
    mill = mill_document(cloths)

    def cloth(node, inputs, ctx):
        time.sleep(0.05)                        # simulated I/O per cloth
        return {name: ctx.store.put({"pattern": "release",
                                     "date": inputs["date"]})
                for name, _ in node.outputs}

    def beam(node, inputs, ctx):
        time.sleep(0.02)
        return {"beam": ctx.store.put({"assembled": sorted(inputs)})}

    def rush(node, inputs, ctx):                # no-sleep control → machinery
        if node.operation == "weave.cloth":
            return {name: ctx.store.put({"pattern": "release",
                                         "date": inputs["date"]})
                    for name, _ in node.outputs}
        return {"beam": ctx.store.put({"assembled": sorted(inputs)})}

    mill_handlers = {"weave.cloth": cloth, "weave.beam": beam}
    rush_handlers = {"weave.cloth": rush, "weave.beam": rush}
    start = [Event("m1", "LOOM_STARTED", 1, "mill", {"date": "2026-11-05"})]

    t0 = time.perf_counter()
    seq = execute(mill, mill_handlers, log=start)
    t_seq = time.perf_counter() - t0
    t0 = time.perf_counter()
    par = execute(mill, mill_handlers, log=start, workers=8)
    t_par = time.perf_counter() - t0
    t0 = time.perf_counter()
    execute(mill, rush_handlers, log=start)
    t_mach = time.perf_counter() - t0
    same = digest(log_to_canonical(seq.log)) == digest(log_to_canonical(par.log))
    records = len(seq.log)

    print(f"\n  [5] THE IR MILL — {cloths} cloths + 1 beam, "
          f"simulated 50 ms I/O per cloth")
    print(f"      sequential (1 worker):  {t_seq:.2f} s · {seq.waves} waves · "
          f"{seq.executed} executions")
    print(f"      concurrent (8 workers): {t_par:.2f} s · {par.waves} waves — "
          f"×{t_seq / t_par:.1f}")
    print(f"      byte-identical logs: {same} — concurrency changed WHEN, "
          f"not WHAT (R8)")
    print(f"      artifacts: {len(par.store)} blocks · {par.store.bytes()} bytes "
          f"({cloths} cloths wove one pattern — CAS stored it once)")
    print(f"      machinery (no-sleep control): {t_mach * 1000:.1f} ms · "
          f"{t_mach * 1000 / records:.2f} ms per log record")
    print("      honesty: sleeps simulate I/O; the thread overlap is real")
    print("      wall-clock for I/O-bound work only — CPU parallelism needs")
    print("      processes (the loom.py mill) or distribution (Phase 10)")


if __name__ == "__main__":
    try:
        self_check()
        demo()
    except (CanonicalError, AssertionError) as e:
        print(f"RUNTIME FAILURE: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
