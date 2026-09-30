# -*- coding: utf-8 -*-
"""
CANONICAL WEAVE IR v1 — the language-neutral semantic kernel of Prolepsis
===========================================================================
Phase 1 of the Canonical program. The architecture freeze (Phase 0) is in
docs/canonical-ir.md; the machine-checkable contracts C0–C32 live in
conformance.py.

  INTENT + CONSTRAINTS + STATE + EVENTS
      ↓
  Canonical WEAVE IR                ← this module (entities, types, expressions)
      ↓
  Execution Frontier                ← compute_frontier (pure status computation)
      ↓
  parallel / speculative / distributed execution   ← runtimes (loom.py today)

The Python runtime remains the reference implementation; this module defines
what its semantics MEAN, independently of Python. Canonical rules:

  · C22 serialization — canonical_json: sorted keys, no whitespace, UTF-8,
    ints only. FLOATS ARE NOT CANONICAL IN v1 (a deliberate, documented
    decision: cross-language byte-equality of floats is a minefield; ints,
    strings and content-addressed refs cover the current kernel);
  · C21 addressing — a document's identity is the sha256 of its canonical
    serialization; external binaries travel as {"$cas": "sha256:…"} refs;
  · C3 types — null, bool, int, string, list, map, ref, date, semver;
    language-specific types live only in adapters (Phase 8);
  · C0/C29 expressions — operator TREES, not source code: no Python, no
    eval, a closed operator set; the evaluator is pure;
  · C1 identity — ids are explicit, unique per document, immutable;
  · C10 frontier — ready / blocked / running / completed / failed /
    canceled / speculative; canceled is terminal (C16);
  · C25 distribution — the kernel does no I/O and imports nothing beyond
    hashlib/json/re/dataclasses (audited by the conformance suite).

Scheduling POLICY (WEAVE's hard/soft/overspun warping) is a runtime hint,
not canonical semantics: auto-warping derives levels from history at run
time, so a level cannot be part of the meaning. Hints ride in Node.hints.

v1.1 (IR_VERSION 1.1.0, compatibility major 1 — documents unchanged):
the ordered LOG now also carries Commits (C18: one per node, valid only
against a completed execution) and failed ExecutionRecords carry their
canonical structured error (C15). State gained a "commits" section; v1.0
state digests are not comparable with v1.1 — that is what versioning is for.
v1.2 (additive): transitions() — per-transition state records (C9's full
wording): fold the log record by record and capture each prefix's canonical
state digest: the event-sourcing chain. KERNEL_CONCEPTS grew by one entry —
a deliberate act, documented in docs/canonical-ir.md.
v1.3 (architectural audit, directive v2): LOGGED_TRANSITIONS — the frontier
transition matrix becomes data (exhaustively machine-checked in the
conformance suite); Commit gains an optional provenance (C20: how a result
came to be — producer, operation, IR version, execution, input refs).
v1.4 (adapter-driven): the 'prior' port — event constraints may look BACK
at the latest payload of every event that already happened (Jacquard's
fact("EVENT") lowered to get(["prior", "EVENT", field])). Strictly earlier
records only; a reference to an event that has not happened is a
deterministic unknown-port rejection. Old documents and logs are unchanged.

Run the minimal working example:   python3 canonical.py
Run the conformance suite:         python3 conformance.py
Run the reference runtime demo:    python3 runtime.py
"""

from __future__ import annotations

import hashlib
from types import MappingProxyType
import json
import re
from dataclasses import dataclass, field

IR_VERSION = "1.4.0"
SCHEMA_VERSION = "1.0.0"
COMPATIBILITY_MAJOR = 1            # C2: breaking semantic changes bump this

IMPLICIT_NAMESPACES = MappingProxyType({   # C27: read-only
    "weave": "https://prolepsis.dev/ns/weave"})

# The kernel concept inventory (C30: the kernel stays minimal — audited).
KERNEL_CONCEPTS = (
    "dumps", "loads", "digest",                       # C22 serialization
    "CANONICAL_TYPES", "check_value",                 # C3/C4 values
    "EXPRESSION_OPS", "check_expression", "evaluate", # C0/C29 expressions
    "EFFECT_KINDS",                                   # C13 effects
    "Effect", "Constraint", "Intent",                 # intent layer (C31)
    "Port", "Node", "EventDecl",                      # declaration layer
    "Event", "ExecutionRecord",                       # instance layer (C8)
    "IRError", "Capability", "Commit", "Provenance",  # C15/C14/C18/C20
    "IRDocument",                                     # the document (C1/C2)
    "FRONTIER_STATUSES", "compute_frontier",          # C10/C11
    "reduce",                                         # C12/C19 replay
    "transitions",                                    # C9 per-transition records
    "check_capabilities",                             # C14/C26
)


class CanonicalError(Exception):
    """A violation of the canonical contracts (C0–C32)."""


# ───────────────────────── C22: deterministic serialization ─────────────────────────

def dumps(value) -> str:
    """Canonical JSON: null/bool/int/string/list/map only; map keys sorted
    by code point (== UTF-8 byte order); no whitespace; ints arbitrary
    precision. Floats, sets and arbitrary objects are rejected."""
    out: list[str] = []
    _write(value, out)
    return "".join(out)


def _write(v, out: list):
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, int):                 # bool is handled above
        out.append(repr(v))
    elif isinstance(v, str):
        out.append(json.dumps(v, ensure_ascii=False))
    elif isinstance(v, (list, tuple)):
        out.append("[")
        for i, item in enumerate(v):
            if i:
                out.append(",")
            _write(item, out)
        out.append("]")
    elif isinstance(v, dict):
        for k in v:
            if not isinstance(k, str):
                raise CanonicalError("map keys must be strings")
        out.append("{")
        for i, k in enumerate(sorted(v)):
            if i:
                out.append(",")
            out.append(json.dumps(k, ensure_ascii=False))
            out.append(":")
            _write(v[k], out)
        out.append("}")
    else:
        raise CanonicalError(
            f"not a canonical value: {type(v).__name__} "
            "(C4/C22: floats, sets and arbitrary objects are not canonical; "
            "binaries travel as {\"$cas\": \"sha256:…\"} refs)")


def _reject_float(text: str):
    raise CanonicalError(f"float literal '{text}' is not canonical (C22)")


def _reject_constant(text: str):
    raise CanonicalError(f"'{text}' is not canonical (C22)")


def loads(text: str):
    """Parse canonical JSON strictly: duplicate keys, floats and NaN/Infinity
    are rejected. Returns plain canonical values."""
    def pairs(items):
        keys = [k for k, _ in items]
        if len(keys) != len(set(keys)):
            raise CanonicalError("duplicate map keys (C22)")
        return dict(items)

    try:
        value = json.loads(text, object_pairs_hook=pairs,
                           parse_float=_reject_float,
                           parse_constant=_reject_constant)
    except json.JSONDecodeError as error:
        # C22 hardening (found by tools/fuzz.py): trailing content, raw
        # control characters, broken escapes — every malformed input is a
        # CANONICAL rejection, never a host exception
        raise CanonicalError(f"not canonical JSON: {error} (C22)") from error
    validate_value(value)
    return value


def digest(value) -> str:
    """Content address of any canonical value (C21)."""
    return "sha256:" + hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def validate_value(value) -> None:
    """Raise unless the value is canonical (C4)."""
    _write(value, [])            # dumps validates everything we need


# ───────────────────────── C3/C4: canonical types ─────────────────────────

CANONICAL_TYPES = ("null", "bool", "int", "string", "list", "map",
                   "ref", "date", "semver")

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SEMVER = re.compile(r"^v?\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def check_value(value, ctype: str) -> str | None:
    """A type guard at the execution boundary (C3): returns the violation
    text, or None when the value conforms to the canonical type."""
    if ctype not in CANONICAL_TYPES:
        return f"unknown canonical type '{ctype}' (C3)"
    if ctype == "null":
        return None if value is None else f"'null' expected, got {type(value).__name__}"
    if ctype == "bool":
        return None if isinstance(value, bool) else f"'bool' expected, got {type(value).__name__}"
    if ctype == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"'int' expected, got {type(value).__name__}"
        return None
    if ctype == "string":
        return None if isinstance(value, str) else f"'string' expected, got {type(value).__name__}"
    if ctype == "list":
        if not isinstance(value, list):
            return f"'list' expected, got {type(value).__name__}"
        for i, item in enumerate(value):
            try:
                validate_value(item)
            except CanonicalError as e:
                return f"item {i}: {e}"
        return None
    if ctype == "map":
        if not isinstance(value, dict):
            return f"'map' expected, got {type(value).__name__}"
        try:
            validate_value(value)
        except CanonicalError as e:
            return str(e)
        return None
    if ctype == "ref":                    # a content-addressed reference (C4/C21)
        if (not isinstance(value, dict) or set(value) != {"$cas"}
                or not isinstance(value["$cas"], str)
                or not value["$cas"].startswith("sha256:")
                or not _HEX64.fullmatch(value["$cas"][7:])):
            return "'ref' expected: {\"$cas\": \"sha256:<64 hex>\"}"
        return None
    if ctype == "date":
        if not isinstance(value, str) or not _DATE.fullmatch(value):
            return f"'date' expected (YYYY-MM-DD), got {value!r}"
        return None
    if ctype == "semver":
        if not isinstance(value, str) or not _SEMVER.fullmatch(value):
            return f"'semver' expected (like v1.4.2), got {value!r}"
        return None
    return f"unknown canonical type '{ctype}' (C3)"


# ───────────────────────── C0/C29: canonical expressions ─────────────────────────
#
# Expressions are OPERATOR TREES, not source code. No host language is
# involved, the operator set is closed, and nothing outside the scope is
# reachable (C7: implicit shared state is forbidden).
#
#   {"lit": 3}                                  literal (canonical value)
#   {"get": ["f", "version"]}                   port path access
#   {"eq"|"ne"|"lt"|"le"|"gt"|"ge": [a, b]}     comparisons
#   {"and"|"or": [a, b, …]}  {"not": [a]}       boolean
#   {"add"|"sub"|"mul": [a, b]}                 integer arithmetic
#   {"sum": [listexpr, "field"]}                sum a field over a list of maps
#   {"count": [listexpr]}                       list length

EXPRESSION_OPS = ("lit", "get", "eq", "ne", "lt", "le", "gt", "ge",
                  "and", "or", "not", "add", "sub", "mul", "sum", "count")
_BINARY = ("eq", "ne", "lt", "le", "gt", "ge", "add", "sub", "mul")


def check_expression(expr) -> str | None:
    """Structural validity of an expression tree (C0: closed operator set)."""
    if not isinstance(expr, dict) or len(expr) != 1:
        return "an expression is a map with exactly one operator"
    op, args = next(iter(expr.items()))
    if op not in EXPRESSION_OPS:
        return f"unknown operator '{op}' (C0: the operator set is closed)"
    if op == "lit":
        try:
            validate_value(args)
        except CanonicalError as e:
            return f"literal: {e}"
        return None
    if op == "get":
        if (not isinstance(args, list) or not args
                or not all(isinstance(p, str) for p in args)):
            return "'get' takes a non-empty list of strings (a port path)"
        return None
    if op in _BINARY:
        if not isinstance(args, list) or len(args) != 2:
            return f"'{op}' takes exactly two arguments"
        for a in args:
            problem = check_expression(a)
            if problem:
                return problem
        return None
    if op in ("and", "or"):
        if not isinstance(args, list) or not args:
            return f"'{op}' takes one or more arguments"
        for a in args:
            problem = check_expression(a)
            if problem:
                return problem
        return None
    if op == "not":
        if not isinstance(args, list) or len(args) != 1:
            return "'not' takes exactly one argument"
        return check_expression(args[0])
    if op == "sum":
        if (not isinstance(args, list) or len(args) != 2
                or not isinstance(args[1], str)):
            return "'sum' takes [list-expression, field-name]"
        return check_expression(args[0])
    if op == "count":
        if not isinstance(args, list) or len(args) != 1:
            return "'count' takes exactly one argument"
        return check_expression(args[0])
    return f"unknown operator '{op}' (C0)"


def evaluate(expr, scope: dict):
    """Pure evaluation against a port scope. Unknown ports raise (C7)."""
    problem = check_expression(expr)
    if problem:
        raise CanonicalError(problem)
    op, args = next(iter(expr.items()))
    if op == "lit":
        return args
    if op == "get":
        current = scope
        for name in args:
            if not isinstance(current, dict) or name not in current:
                raise CanonicalError(
                    f"unknown port '{'.'.join(args)}' "
                    "(C7: implicit shared state is forbidden)")
            current = current[name]
        return current
    if op in ("eq", "ne"):
        left, right = (evaluate(a, scope) for a in args)
        return (left == right) if op == "eq" else (left != right)
    if op in ("lt", "le", "gt", "ge"):
        left, right = (evaluate(a, scope) for a in args)
        comparable = (isinstance(left, (int, str)) and isinstance(right, (int, str))
                      and not isinstance(left, bool) and not isinstance(right, bool)
                      and type(left) is type(right))
        if not comparable:
            raise CanonicalError(f"'{op}': only int|int or string|string compare (C3)")
        return {"lt": left < right, "le": left <= right,
                "gt": left > right, "ge": left >= right}[op]
    if op == "and":
        return all(bool(evaluate(a, scope)) for a in args)
    if op == "or":
        return any(bool(evaluate(a, scope)) for a in args)
    if op == "not":
        return not bool(evaluate(args[0], scope))
    if op in ("add", "sub", "mul"):
        left, right = (evaluate(a, scope) for a in args)
        if isinstance(left, bool) or isinstance(right, bool) \
                or not isinstance(left, int) or not isinstance(right, int):
            raise CanonicalError(f"'{op}': canonical arithmetic is int-only (C22)")
        return {"add": left + right, "sub": left - right, "mul": left * right}[op]
    if op == "sum":
        items, name = args[0], args[1]
        total = 0
        for item in evaluate(items, scope):
            if not isinstance(item, dict) or name not in item \
                    or isinstance(item[name], bool) or not isinstance(item[name], int):
                raise CanonicalError(f"'sum': every item needs an int field '{name}'")
            total += item[name]
        return total
    if op == "count":
        return len(evaluate(args[0], scope))
    raise CanonicalError(f"unknown operator '{op}' (C0)")


# ───────────────────────── C13: effects ─────────────────────────

EFFECT_KINDS = ("pure", "filesystem", "network", "process",
                "database", "secret", "external_service")


# ───────────────────────── entities ─────────────────────────

def _strict(value, allowed: tuple, context: str) -> dict:
    """C29 (no hidden semantics): a serialized entity carries exactly the
    allowed keys — anything else is rejected, not ignored. Keys suffixed
    with '!' are required; the rest are optional."""
    if not isinstance(value, dict):
        raise CanonicalError(f"{context}: a map expected")
    names = {k.rstrip("!") for k in allowed}
    required = {k.rstrip("!") for k in allowed if k.endswith("!")}
    unknown = set(value) - names
    if unknown:
        raise CanonicalError(f"{context}: unknown keys {sorted(unknown)} (C29)")
    missing = required - set(value)
    if missing:
        raise CanonicalError(f"{context}: missing keys {sorted(missing)}")
    return value


def _opt(value, key, default):
    return value[key] if key in value else default


@dataclass(frozen=True)
class Effect:
    """A declared side-effect class of an operation (C13). Undeclared
    effects are forbidden; kinds are a closed set."""
    kind: str
    params: tuple = ()          # tuple[(name, value)] — canonical values

    def to_canonical(self):
        return {"kind": self.kind,
                "params": {k: v for k, v in self.params}}

    @classmethod
    def from_canonical(cls, value):
        v = _strict(value, ("kind", "params"), "effect")
        kind = v["kind"]
        if kind not in EFFECT_KINDS:
            raise CanonicalError(f"unknown effect kind '{kind}' (C13)")
        params = tuple(sorted(v.get("params", {}).items())) if v.get("params") else ()
        for _, pv in params:
            validate_value(pv)
        return cls(kind, params)

    def __post_init__(self):
        if self.kind not in EFFECT_KINDS:
            raise CanonicalError(f"unknown effect kind '{self.kind}' (C13)")


@dataclass(frozen=True)
class Constraint:
    """A named invariant expression (a WEAVE selvedge, canonically)."""
    id: str
    expr: dict
    message: str = ""

    def __post_init__(self):
        problem = check_expression(self.expr)
        if problem:
            raise CanonicalError(f"constraint '{self.id}': {problem}")
        if not isinstance(self.id, str) or not self.id:
            raise CanonicalError("constraint id must be a non-empty string")
        if not isinstance(self.message, str):
            raise CanonicalError("constraint message must be a string")

    def to_canonical(self):
        return {"id": self.id, "expr": self.expr, "message": self.message}

    @classmethod
    def from_canonical(cls, value):
        v = _strict(value, ("id!", "expr!", "message"), "constraint")
        return cls(v["id"], v["expr"], v.get("message", ""))


@dataclass(frozen=True)
class Intent:
    """WHAT and WHY, never HOW (C31): a goal, its requirements, constraints,
    preferences and effects. The runtime derives an execution plan from it;
    an intent is never a fixed command sequence."""
    goal: str
    requires: tuple = ()        # event types / node ids
    constraints: tuple = ()     # tuple[Constraint]
    prefer: tuple = ()          # preference symbols (non-binding)
    effects: tuple = ()         # tuple[Effect]

    def to_canonical(self):
        return {"goal": self.goal,
                "requires": list(self.requires),
                "constraints": [c.to_canonical() for c in self.constraints],
                "prefer": list(self.prefer),
                "effects": [e.to_canonical() for e in self.effects]}

    @classmethod
    def from_canonical(cls, value):
        v = _strict(value, ("goal!", "requires", "constraints", "prefer", "effects"),
                    "intent")
        return cls(v["goal"],
                   tuple(v.get("requires", ())),
                   tuple(Constraint.from_canonical(c) for c in v.get("constraints", ())),
                   tuple(v.get("prefer", ())),
                   tuple(Effect.from_canonical(e) for e in v.get("effects", ())))

    def __post_init__(self):
        if not isinstance(self.goal, str) or not self.goal:
            raise CanonicalError("intent goal must be a non-empty string (C31)")


Port = tuple                    # (name, canonical type) — kept minimal (C30)


@dataclass(frozen=True)
class Node:
    """An executable operation (C5): explicit inputs/outputs (ports),
    explicit requirements (C6), declared effects (C13), constraints, and
    non-semantic scheduling hints (WEAVE warping levels live here)."""
    id: str
    operation: str              # a namespaced symbol (C27)
    inputs: tuple = ()          # tuple[Port]
    outputs: tuple = ()         # tuple[Port]
    requirements: tuple = ()    # node ids / event types (C6)
    constraints: tuple = ()     # tuple[Constraint]
    effects: tuple = ()         # tuple[Effect] — must be non-empty (C13)
    speculative: bool = False   # C17: a speculative node prepares in advance
    hints: tuple = ()           # tuple[(k, v)] — scheduling policy, non-semantic

    def to_canonical(self):
        return {"id": self.id,
                "operation": self.operation,
                "inputs": [list(p) for p in self.inputs],
                "outputs": [list(p) for p in self.outputs],
                "requirements": list(self.requirements),
                "constraints": [c.to_canonical() for c in self.constraints],
                "effects": [e.to_canonical() for e in self.effects],
                "speculative": self.speculative,
                "hints": {k: v for k, v in self.hints}}

    @classmethod
    def from_canonical(cls, value):
        v = _strict(value, ("id!", "operation!", "inputs", "outputs", "requirements",
                            "constraints", "effects", "speculative", "hints"), "node")
        return cls(v["id"], v["operation"],
                   tuple((p[0], p[1]) for p in v.get("inputs", ())),
                   tuple((p[0], p[1]) for p in v.get("outputs", ())),
                   tuple(v.get("requirements", ())),
                   tuple(Constraint.from_canonical(c) for c in v.get("constraints", ())),
                   tuple(Effect.from_canonical(e) for e in v.get("effects", ())),
                   bool(v.get("speculative", False)),
                   tuple(sorted(v.get("hints", {}).items())))

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", self.id):
            raise CanonicalError(f"node id '{self.id}' must match [a-z][a-z0-9_-]* (C1)")
        if not self.effects:
            raise CanonicalError(f"node '{self.id}': undeclared effects are forbidden "
                                 "(C13) — declare at least the 'pure' effect")
        seen = set()
        for name, ctype in self.inputs + self.outputs:
            if name in seen:
                raise CanonicalError(f"node '{self.id}': duplicate port '{name}'")
            seen.add(name)
            if ctype not in CANONICAL_TYPES:
                raise CanonicalError(f"node '{self.id}': port '{name}' has unknown "
                                     f"type '{ctype}' (C3)")


@dataclass(frozen=True)
class EventDecl:
    """An event TYPE declaration (C8): the facts it carries (name + canonical
    type) and the constraints every instance must satisfy (the reed)."""
    event_type: str
    facts: tuple = ()           # tuple[(name, canonical type)]
    constraints: tuple = ()     # tuple[Constraint]

    def to_canonical(self):
        return {"event_type": self.event_type,
                "facts": [list(f) for f in self.facts],
                "constraints": [c.to_canonical() for c in self.constraints]}

    @classmethod
    def from_canonical(cls, value):
        v = _strict(value, ("event_type!", "facts", "constraints"), "event declaration")
        return cls(v["event_type"],
                   tuple((f[0], f[1]) for f in v.get("facts", ())),
                   tuple(Constraint.from_canonical(c) for c in v.get("constraints", ())))

    def __post_init__(self):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.event_type):
            raise CanonicalError(f"event type '{self.event_type}' must match "
                                 "[A-Z][A-Z0-9_]* (C1)")
        seen = set()
        for name, ctype in self.facts:
            if name in seen:
                raise CanonicalError(f"event '{self.event_type}': duplicate fact '{name}'")
            seen.add(name)
            if ctype not in CANONICAL_TYPES:
                raise CanonicalError(f"event '{self.event_type}': fact '{name}' has "
                                     f"unknown type '{ctype}' (C3)")


@dataclass(frozen=True)
class Event:
    """An event INSTANCE (C8): a first-class, replayable entity with a stable
    id, a logical clock, a source and a canonical payload."""
    id: str
    event_type: str
    clock: int
    source: str
    payload: dict

    def to_canonical(self):
        return {"id": self.id, "event_type": self.event_type, "clock": self.clock,
                "source": self.source, "payload": self.payload}

    @classmethod
    def from_canonical(cls, value):
        v = _strict(value, ("id!", "event_type!", "clock!", "source!", "payload!"), "event")
        return cls(v["id"], v["event_type"], v["clock"], v["source"], v["payload"])

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id:
            raise CanonicalError("event id must be a non-empty string (C8)")
        if isinstance(self.clock, bool) or not isinstance(self.clock, int) or self.clock < 0:
            raise CanonicalError("event clock must be a non-negative int (C8)")
        if not isinstance(self.source, str) or not self.source:
            raise CanonicalError("event source must be a non-empty string (C8)")
        validate_value(self.payload)


EXECUTION_STATUSES = ("running", "completed", "failed", "canceled")


@dataclass(frozen=True)
class ExecutionRecord:
    """A node execution outcome in the log (C10/C16). computed statuses
    (ready/blocked/speculative) are never logged — only executed ones."""
    node: str
    status: str
    error: "IRError | None" = None

    @property
    def reason(self) -> str:
        """Agent-Platform view of the failure code (see vendor/PATCHES.md)."""
        return self.error.error_type if self.error is not None else ""

    @property
    def message(self) -> str:
        """Agent-Platform view of the failure cause (see vendor/PATCHES.md)."""
        return self.error.cause if self.error is not None else ""

    def to_canonical(self):
        record = {"node": self.node, "status": self.status}
        if self.error is not None:
            record["error"] = self.error.to_canonical()
        return record

    @classmethod
    def from_canonical(cls, value):
        v = _strict(value, ("node!", "status!", "error"), "execution record")
        error = v.get("error")
        return cls(v["node"], v["status"],
                   IRError.from_canonical(error) if error is not None else None)

    def __post_init__(self):
        if self.status not in EXECUTION_STATUSES:
            raise CanonicalError(f"execution status '{self.status}' must be one of "
                                 f"{EXECUTION_STATUSES} (C10)")
        if self.status == "failed" and self.error is None:
            raise CanonicalError("a failed execution must carry its structured "
                                 "error (C15)")


@dataclass(frozen=True)
class IRError(Exception):
    """A canonical structured error (C15). Also the handler-raised error
    class consumed by runtime._execute_node (see vendor/PATCHES.md)."""
    node: str
    operation: str
    error_type: str
    cause: str
    recoverability: str         # "transient" | "permanent"
    retryable: bool

    def to_canonical(self):
        return {"node": self.node, "operation": self.operation,
                "error_type": self.error_type, "cause": self.cause,
                "recoverability": self.recoverability, "retryable": self.retryable}

    @classmethod
    def from_canonical(cls, value):
        v = _strict(value, ("node!", "operation!", "error_type!", "cause!",
                            "recoverability!", "retryable!"), "error")
        return cls(v["node"], v["operation"], v["error_type"], v["cause"],
                   v["recoverability"], v["retryable"])

    def __post_init__(self):
        if self.recoverability not in ("transient", "permanent"):
            raise CanonicalError("recoverability must be 'transient' or 'permanent' (C15)")
        if not isinstance(self.retryable, bool):
            raise CanonicalError("retryable must be a bool (C15)")
        Exception.__init__(self, f"{self.error_type}: {self.cause}")


@dataclass(frozen=True)
class Capability:
    """An abstract grant to use a class of external resources (C14/C26).
    The IR never names concrete paths, processes or vendor APIs."""
    name: str
    params: tuple = ()

    def to_canonical(self):
        return {"name": self.name, "params": {k: v for k, v in self.params}}

    @classmethod
    def from_canonical(cls, value):
        v = _strict(value, ("name!", "params"), "capability")
        return cls(v["name"], tuple(sorted(v.get("params", {}).items())))

    def __post_init__(self):
        if self.name not in EFFECT_KINDS:
            raise CanonicalError(f"capability '{self.name}' must be an effect kind "
                                 f"(C14): {EFFECT_KINDS}")


@dataclass(frozen=True)
class Commit:
    """An explicit commit boundary (C18): the state address after a node's
    committed execution and the artifacts it produced (content-addressed)."""
    execution_id: str
    node: str
    state: str                  # "sha256:<64 hex>"
    artifacts: tuple = ()       # tuple of ref maps
    provenance: "Provenance | None" = None   # C20: how this result came to be

    def to_canonical(self):
        value = {"execution_id": self.execution_id, "node": self.node,
                 "state": self.state, "artifacts": list(self.artifacts)}
        if self.provenance is not None:
            value["provenance"] = self.provenance.to_canonical()
        return value

    @classmethod
    def from_canonical(cls, value):
        v = _strict(value, ("execution_id!", "node!", "state!", "artifacts",
                            "provenance"), "commit")
        provenance = v.get("provenance")
        return cls(v["execution_id"], v["node"], v["state"],
                   tuple(v.get("artifacts", ())),
                   Provenance.from_canonical(provenance)
                   if provenance is not None else None)

    def __post_init__(self):
        if not self.state.startswith("sha256:") or not _HEX64.fullmatch(self.state[7:]):
            raise CanonicalError("commit state must be 'sha256:<64 hex>' (C18/C21)")
        for a in self.artifacts:
            if check_value(a, "ref"):
                raise CanonicalError("commit artifacts must be refs (C18/C21)")
        if self.provenance is not None and not isinstance(self.provenance, Provenance):
            raise CanonicalError("commit provenance must be a Provenance (C20)")


@dataclass(frozen=True)
class Provenance:
    """Where a produced value came from (C20)."""
    producer: str
    operation: str
    version: str
    execution_id: str
    inputs: tuple = ()          # refs

    def to_canonical(self):
        return {"producer": self.producer, "inputs": list(self.inputs),
                "operation": self.operation, "version": self.version,
                "execution_id": self.execution_id}

    @classmethod
    def from_canonical(cls, value):
        v = _strict(value, ("producer!", "operation!", "version!",
                            "execution_id!", "inputs"), "provenance")
        return cls(v["producer"], v["operation"], v["version"],
                   v["execution_id"], tuple(v.get("inputs", ())))

    def __post_init__(self):
        for r in self.inputs:
            if check_value(r, "ref"):
                raise CanonicalError("provenance inputs must be refs (C20/C21)")


# ───────────────────────── the document ─────────────────────────

@dataclass(frozen=True)
class IRDocument:
    """A complete, self-contained canonical program (C1/C2/C26): intent,
    declared events, executable nodes, initial state and namespaces."""
    ir_version: str = IR_VERSION
    schema_version: str = SCHEMA_VERSION
    compatibility_version: str = IR_VERSION
    namespaces: tuple = ()      # tuple[(prefix, uri)]
    intent: Intent = field(default_factory=lambda: Intent("unnamed"))
    nodes: tuple = ()           # tuple[Node]
    events: tuple = ()          # tuple[EventDecl]
    initial: tuple = ()         # tuple[(k, v)] — canonical initial state

    def to_canonical(self) -> dict:
        return {"ir_version": self.ir_version,
                "schema_version": self.schema_version,
                "compatibility_version": self.compatibility_version,
                "namespaces": {k: v for k, v in self.namespaces},
                "intent": self.intent.to_canonical(),
                "nodes": [n.to_canonical() for n in self.nodes],
                "events": [e.to_canonical() for e in self.events],
                "initial": {k: v for k, v in self.initial}}

    @classmethod
    def from_canonical(cls, value) -> "IRDocument":
        """Mandatory validation of untrusted input (C26): structure, versions,
        identities, dependencies, types, effects, expressions, namespaces."""
        v = _strict(value, ("ir_version!", "schema_version!", "compatibility_version!",
                            "namespaces", "intent", "nodes", "events", "initial"),
                    "document")
        doc = cls(
            ir_version=v.get("ir_version", IR_VERSION),
            schema_version=v.get("schema_version", SCHEMA_VERSION),
            compatibility_version=v.get("compatibility_version", IR_VERSION),
            namespaces=tuple(sorted(v.get("namespaces", {}).items())),
            intent=Intent.from_canonical(v.get("intent", {"goal": "unnamed"})),
            nodes=tuple(Node.from_canonical(n) for n in v.get("nodes", ())),
            events=tuple(EventDecl.from_canonical(e) for e in v.get("events", ())),
            initial=tuple(sorted(v.get("initial", {}).items())),
        )
        doc.validate()
        return doc

    @property
    def addr(self) -> str:
        """Content address of the document (C1/C21): stable, deterministic,
        independent of the language that serialized it."""
        return digest(self.to_canonical())

    def validate(self) -> None:
        # C2: versions
        for name in ("ir_version", "schema_version", "compatibility_version"):
            version = getattr(self, name)
            if not isinstance(version, str) or not _SEMVER.fullmatch(version):
                raise CanonicalError(f"{name} must be a semver string (C2)")
        try:
            major = int(self.compatibility_version.lstrip("v").split(".")[0])
        except ValueError:
            major = -1
        if major != COMPATIBILITY_MAJOR:
            raise CanonicalError(
                f"compatibility_version {self.compatibility_version} is not compatible "
                f"with this kernel (major {COMPATIBILITY_MAJOR} required, C2)")
        # C27: namespaces
        declared = dict(self.namespaces)
        for prefix in declared:
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", prefix):
                raise CanonicalError(f"namespace prefix '{prefix}' is malformed (C27)")
        # C1: unique identities
        node_ids = [n.id for n in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise CanonicalError("duplicate node ids (C1)")
        event_types = [e.event_type for e in self.events]
        if len(event_types) != len(set(event_types)):
            raise CanonicalError("duplicate event types (C1)")
        # C27: namespaced symbols resolve
        for n in self.nodes:
            _check_symbol(n.operation, declared, f"node '{n.id}' operation")
        for c in self.intent.constraints:
            pass                                   # constraint ids are local
        # C6: every requirement resolves to a node id or an event type
        resolvable = set(node_ids) | set(event_types)
        for n in self.nodes:
            for req in n.requirements:
                if req not in resolvable:
                    raise CanonicalError(f"node '{n.id}' requires unknown "
                                         f"'{req}' (C6)")
        for req in self.intent.requires:
            if req not in resolvable:
                raise CanonicalError(f"intent requires unknown '{req}' (C6)")
        # C4: initial state is canonical
        for _, value in self.initial:
            validate_value(value)


def _check_symbol(symbol: str, declared: dict, context: str) -> None:
    """A dotted symbol's prefix must be a declared (or built-in) namespace."""
    if "." in symbol:
        prefix = symbol.split(".")[0]
        if prefix not in declared and prefix not in IMPLICIT_NAMESPACES:
            raise CanonicalError(f"{context}: '{symbol}' uses undeclared namespace "
                                 f"'{prefix}' (C27)")


# ───────────────────────── C10/C11: the execution frontier ─────────────────────────

FRONTIER_STATUSES = ("ready", "blocked", "running", "completed",
                     "failed", "canceled", "speculative")
_TERMINAL = ("completed", "failed", "canceled")

# The logged-status transition matrix (C10/C16). Computed statuses (blocked,
# ready, speculative) are DERIVED by compute_frontier and may never be
# logged. A node's FIRST record may be any logged status (an execution may
# first be observed at its end). After that: a running node may keep running
# (heartbeats) or finish as completed/failed/canceled; a TERMINAL status
# never changes except an exact duplicate of itself (idempotent re-append).
# Every other transition is a canonical error — exhaustively machine-checked
# in the conformance suite (C10).
LOGGED_TRANSITIONS = MappingProxyType({      # read-only (C25 audit)
    ("running", "running"): True,          # heartbeat / progress
    ("running", "completed"): True,
    ("running", "failed"): True,
    ("running", "canceled"): True,
})


def _legal_status_change(previous, nxt) -> bool:
    """The matrix verdict for one log record: first records are free; after
    that only LOGGED_TRANSITIONS and idempotent terminal re-appends pass."""
    if previous is None:
        return True
    if previous in _TERMINAL:
        return nxt == previous
    return LOGGED_TRANSITIONS.get((previous, nxt), False)


def compute_frontier(document: IRDocument, log) -> dict:
    """The pure frontier computation (C10/C11): given a document and an
    ordered log of Events/ExecutionRecords, classify every node. Independent
    nodes are ready simultaneously — whether they RUN concurrently is a
    runtime decision the IR does not assume (C25)."""
    happened, executions = set(), {}
    for record in log:
        if isinstance(record, Event):
            if record.event_type not in {e.event_type for e in document.events}:
                raise CanonicalError(f"event '{record.event_type}' is not declared "
                                     "(C29: no hidden semantics)")
            happened.add(record.event_type)
        elif isinstance(record, ExecutionRecord):
            node = record.node
            if node not in {n.id for n in document.nodes}:
                raise CanonicalError(f"execution of unknown node '{node}' (C6)")
            previous = executions.get(node)
            if not _legal_status_change(previous, record.status):
                raise CanonicalError(
                    f"node '{node}' is {previous} — a terminal status cannot change "
                    "to " + record.status + " (C16)")
            executions[node] = record.status
        elif isinstance(record, Commit):
            if record.node not in {n.id for n in document.nodes}:
                raise CanonicalError(f"commit of unknown node '{record.node}' (C6)")
            # a commit never changes a status — it fixes artifacts (C18)
        else:
            raise CanonicalError(f"unknown log record {record!r} (C29)")

    completed = {n for n, s in executions.items() if s == "completed"}
    frontier = {}
    for node in document.nodes:
        if node.id in executions:
            frontier[node.id] = executions[node.id]
            continue
        met = all(req in happened or req in completed for req in node.requirements)
        if not met:
            frontier[node.id] = "speculative" if node.speculative else "blocked"
        else:
            frontier[node.id] = "ready"
    return frontier


# ───────────────────────── C12/C19: reduce / replay ─────────────────────────

def reduce(document: IRDocument, log) -> dict:
    """The reference reducer (C9/C12/C19): fold the ordered log through the
    document — events are validated against their declarations (the reed)
    before being applied; the resulting canonical state is deterministic and
    byte-comparable across languages."""
    happened: dict[str, list] = {}
    prior: dict[str, dict] = {}     # event_type -> latest earlier payload
    executions: dict[str, str] = {}
    commits: dict[str, dict] = {}
    initial = dict(document.initial)

    declarations = {e.event_type: e for e in document.events}
    for record in log:
        if isinstance(record, Event):
            decl = declarations.get(record.event_type)
            if decl is None:
                raise CanonicalError(f"event '{record.event_type}' is not declared "
                                     "(C29)")
            payload = record.payload
            # C29: an event carries exactly its declared facts
            unknown = set(payload) - {name for name, _ in decl.facts}
            if unknown:
                raise CanonicalError(f"event '{record.event_type}' carries unknown "
                                     f"facts {sorted(unknown)} (C29)")
            for name, ctype in decl.facts:
                if name not in payload:
                    raise CanonicalError(f"event '{record.event_type}' is missing "
                                         f"fact '{name}' (C8)")
                violation = check_value(payload[name], ctype)
                if violation:
                    raise CanonicalError(f"event '{record.event_type}' fact '{name}': "
                                         f"{violation}")
            scope = {"f": payload, "doc": initial, "prior": prior}
            for constraint in decl.constraints:
                if not evaluate(constraint.expr, scope):
                    raise CanonicalError(f"event '{record.event_type}' breaks "
                                         f"constraint '{constraint.id}': "
                                         f"{constraint.message}")
            prior[record.event_type] = payload
            happened.setdefault(record.event_type, []).append(payload)
        elif isinstance(record, ExecutionRecord):
            if record.node not in {n.id for n in document.nodes}:
                raise CanonicalError(f"execution of unknown node '{record.node}' (C6)")
            previous = executions.get(record.node)
            if not _legal_status_change(previous, record.status):
                raise CanonicalError(f"node '{record.node}' is {previous} — terminal "
                                     f"statuses do not change (C16)")
            executions[record.node] = record.status
        elif isinstance(record, Commit):
            if record.node not in {n.id for n in document.nodes}:
                raise CanonicalError(f"commit of unknown node '{record.node}' (C6)")
            if record.node in commits:
                raise CanonicalError(f"duplicate commit of node '{record.node}' — "
                                     "a commit is atomic and final (C18)")
            commits[record.node] = record.to_canonical()

    # C18: a commit is only valid against a completed execution of its node
    for node in commits:
        if executions.get(node) != "completed":
            raise CanonicalError(f"node '{node}' committed without a completed "
                                 "execution (C18)")

    state = {"happened": happened, "executions": executions, "commits": commits,
             "frontier": compute_frontier(document, log)}
    validate_value(state)                     # the state itself is canonical
    return state


# ───────────────────────── C14/C26: capabilities ─────────────────────────

def check_capabilities(granted, effects) -> list:
    """Which declared effects lack a capability grant (C14/C26)? Returns the
    missing capability names — empty list means the execution is permitted."""
    needed = {e.kind for e in effects if e.kind != "pure"}
    return sorted(needed - set(granted))


# ───────────────────────── C9: per-transition records ─────────────────────────

def transitions(document: IRDocument, log) -> list:
    """Per-transition state records (C9, full wording): fold the ordered log
    one record at a time, capturing each record's digest and the canonical
    state digest of the prefix after applying it — the event-sourcing chain.

    Prefix-completeness: every prefix of the log is itself a valid state
    when each Commit is preceded by its completed ExecutionRecord — the
    order the reference runtime writes, so all of its logs qualify. A log
    whose commit precedes its completion still reduces as a whole, but its
    mid-transaction prefixes are not valid states and raise here.

    Deliberately simple: derived from reduce() over prefixes (O(n²)) — at
    v1 scale (logs of hundreds of records) this folds in milliseconds; an
    incremental folder is a Phase 13 optimization, not a semantic need.
    """
    chain = []
    for index in range(1, len(log) + 1):
        prefix = list(log[:index])
        chain.append({"index": index - 1,
                      "record_digest": digest(prefix[-1].to_canonical()),
                      "state_digest": digest(reduce(document, prefix))})
    return chain


# ───────────────────────── the minimal working example ─────────────────────────

def example_document() -> IRDocument:
    """A miniature release pipeline as Canonical IR — three nodes, three
    events, constraints. This is the smallest document that exercises every
    kernel concept; the full Release pattern arrives with Phase 8's
    pattern→IR adapter. No I/O — the conformance suite reuses it."""
    return IRDocument.from_canonical({
        "ir_version": IR_VERSION,
        "schema_version": SCHEMA_VERSION,
        "compatibility_version": IR_VERSION,
        "namespaces": {},
        "initial": {"release": "v2.0.0", "registry": "ghcr.io/jotalbot/prolepsis"},
        "intent": {
            "goal": "publish_release",
            "requires": ["release_notes", "test_report", "announcement"],
            "constraints": [{
                "id": "green_only",
                "expr": {"eq": [{"get": ["f", "failed"]}, {"lit": 0}]},
                "message": "no failing tests may pass the reed"}],
            "prefer": ["parallel_execution", "cached_artifacts"],
            "effects": [{"kind": "network", "params": {"why": "registry push"}}],
        },
        "nodes": [
            {"id": "release_notes", "operation": "weave.artifact",
             "inputs": [["version", "semver"], ["date", "date"]],
             "outputs": [["notes", "ref"]],
             "requirements": ["TAG_CUT"],
             "effects": [{"kind": "pure"}]},
            {"id": "test_report", "operation": "weave.artifact",
             "inputs": [["date", "date"], ["passed", "int"], ["failed", "int"]],
             "outputs": [["report", "ref"]],
             "requirements": ["TAG_CUT", "CI_GREEN"],
             "constraints": [{"id": "no_failures",
                              "expr": {"eq": [{"get": ["f", "failed"]}, {"lit": 0}]},
                              "message": "no failing tests"}],
             "effects": [{"kind": "pure"}]},
            {"id": "announcement", "operation": "weave.artifact",
             "inputs": [["date", "date"]],
             "outputs": [["post", "ref"]],
             "requirements": ["PUBLISHED"],
             "effects": [{"kind": "external_service"}],
             "speculative": True,
             "hints": {"warping": "overspun"}},
        ],
        "events": [
            {"event_type": "TAG_CUT",
             "facts": [["version", "semver"], ["date", "date"]],
             "constraints": [{"id": "tag_matches",
                              "expr": {"eq": [{"get": ["f", "version"]},
                                              {"get": ["doc", "release"]}]},
                              "message": "the tag must match the declared release"}]},
            {"event_type": "CI_GREEN",
             "facts": [["date", "date"], ["passed", "int"], ["failed", "int"]],
             "constraints": [{"id": "no_failures",
                              "expr": {"eq": [{"get": ["f", "failed"]}, {"lit": 0}]},
                              "message": "no failing tests may pass the reed"}]},
            {"event_type": "PUBLISHED", "facts": [["date", "date"]]},
        ],
    })


def example():
    """Print the minimal working example: a document, a log, the frontier,
    and a byte-equal replay."""
    document = example_document()
    log = [
        Event("e1", "TAG_CUT", 1, "release-bot",
              {"version": "v2.0.0", "date": "2026-11-01"}),
        Event("e2", "CI_GREEN", 2, "ci", {"date": "2026-11-01", "passed": 612, "failed": 0}),
        ExecutionRecord("release_notes", "completed"),
        Commit("release_notes#1", "release_notes", "sha256:" + "a" * 64,
               ({"$cas": "sha256:" + "b" * 64},)),
    ]
    state = reduce(document, log)
    replay = reduce(document, list(log))
    print("CANONICAL WEAVE IR v1 — minimal working example")
    print(f"  document address: {document.addr}")
    print(f"  frontier: {dumps(state['frontier'])}")
    print(f"  state digest:    {digest(state)}")
    print(f"  replay digest:   {digest(replay)}")
    print(f"  byte-equal replay: {digest(state) == digest(replay)}")
    print(f"  capabilities needed by 'announcement': "
          f"{check_capabilities([], document.nodes[2].effects)}")


if __name__ == "__main__":
    example()
