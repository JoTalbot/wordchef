# -*- coding: utf-8 -*-
"""
CONFORMANCE SUITE — the Canonical contracts C0–C31
===================================================
Phase 2 of the Canonical program (docs/canonical-ir.md).

Every contract has: a definition, an invariant, and — wherever it can be
checked by a machine — a POSITIVE test (the invariant holds) and a NEGATIVE
test (a violation is rejected). Contracts that cannot be machine-checked by
a single runtime yet are registered as DECLARED with an honest reason
(rule 15 of the Canonical program: implemented / prototype / planned are
never mixed, and no fake adapters are shipped as real ones).

Run:   python3 conformance.py     (exit 0 = every machine-checkable contract
                                   passes; exit 1 = at least one FAILED)
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from . import canonical as ir

CONTRACTS: list[tuple] = []       # (cid, title, kind, note, fn)


def machine(title: str, note: str = ""):
    def wrap(fn):
        CONTRACTS.append((fn.__name__.upper(), title, "MACHINE", note, fn))
        return fn
    return wrap


def declared(cid: str, title: str, note: str):
    CONTRACTS.append((cid, title, "DECLARED", note, None))


def rejects(fn, needle: str = ""):
    """The NEGATIVE half: fn must raise CanonicalError (with the needle)."""
    try:
        fn()
    except ir.CanonicalError as e:
        if needle and needle not in str(e):
            raise AssertionError(f"rejected, but for the wrong reason: {e}") from e
        return
    raise AssertionError("a CanonicalError was expected, but nothing was rejected")


def doc_value(document: ir.IRDocument, dotted: str):
    """Read a value out of a serialized document ('nodes.0.speculative')."""
    value = document.to_canonical()
    for part in dotted.split("."):
        value = value[int(part)] if part.isdigit() else value[part]
    return value


def with_(document: ir.IRDocument, dotted: str, new) -> dict:
    """A copy of the serialized document with one field replaced."""
    value = document.to_canonical()
    keys = dotted.split(".")
    target = value
    for k in keys[:-1]:
        target = target[int(k)] if k.isdigit() else target[k]
    last = keys[-1]
    last = int(last) if last.isdigit() else last
    if new is ir.CanonicalError:
        target.pop(last, None)
    else:
        target[last] = new
    return value


DOC = None      # built in run() — the shared minimal working example
LOG = None


def base_log():
    return [
        ir.Event("e1", "TAG_CUT", 1, "release-bot",
                 {"version": "v2.0.0", "date": "2026-11-01"}),
        ir.Event("e2", "CI_GREEN", 2, "ci",
                 {"date": "2026-11-01", "passed": 612, "failed": 0}),
    ]


# ───────────────────────── C0–C31 ─────────────────────────

@machine("Canonicality — the IR is language-neutral",
         "expressions are operator trees with a closed set; no host-language semantics")
def c0():
    assert ir.check_expression({"eq": [{"get": ["f", "x"]}, {"lit": 0}]}) is None
    rejects(lambda: ir.Constraint("bad", {"exec": ["rm -rf /"]}), "unknown operator 'exec'")
    rejects(lambda: ir.Constraint("bad", {"python": "f.failed == 0"}), "unknown operator 'python'")


@machine("Identity — stable, unique, immutable ids",
         "duplicate ids are rejected; the document address is content-derived")
def c1():
    bad = with_(DOC, "nodes.1.id", "release_notes")     # duplicate id
    rejects(lambda: ir.IRDocument.from_canonical(bad), "duplicate node ids")
    again = ir.IRDocument.from_canonical(DOC.to_canonical())
    assert again.addr == DOC.addr, "same content must give the same address"
    changed = with_(DOC, "initial.release", "v2.0.1")
    assert ir.IRDocument.from_canonical(changed).addr != DOC.addr


@machine("Versioning — ir/schema/compatibility in every document",
         "missing versions and major mismatches are rejected")
def c2():
    rejects(lambda: ir.IRDocument.from_canonical(with_(DOC, "ir_version", ir.CanonicalError)),
            "missing keys")
    rejects(lambda: ir.IRDocument.from_canonical(
        with_(DOC, "compatibility_version", "99.0.0")), "not compatible")


@machine("Types — canonical types at every execution boundary",
         "null/bool/int/string/list/map/ref/date/semver; unknown types rejected")
def c3():
    assert ir.check_value("2026-11-01", "date") is None
    assert ir.check_value("v1.4.2", "semver") is None
    assert ir.check_value({"$cas": "sha256:" + "a" * 64}, "ref") is None
    assert ir.check_value(1.5, "int") is not None
    assert ir.check_value(True, "int") is not None          # bool is not int
    assert ir.check_value(0, "float") is not None           # no such type
    rejects(lambda: ir.IRDocument.from_canonical(
        with_(DOC, "nodes.0.inputs.0.1", "float")), "unknown type 'float'")


@machine("Values — deterministic, serializable, content-addressed",
         "floats/sets/objects rejected; duplicates rejected on parse")
def c4():
    rejects(lambda: ir.dumps({"x": 1.5}), "not a canonical value")
    rejects(lambda: ir.dumps({1: "one"}), "map keys must be strings")
    rejects(lambda: ir.loads('{"a": 1, "a": 2}'), "duplicate map keys")
    rejects(lambda: ir.loads('{"a": 1.0}'), "not canonical")


@machine("Nodes — every executable operation is a node",
         "id, operation, ports, requirements, effects, constraints")
def c5():
    rejects(lambda: ir.IRDocument.from_canonical(
        with_(DOC, "nodes.0.id", ir.CanonicalError)), "missing keys")
    rejects(lambda: ir.Node("BadId", "weave.artifact", effects=(ir.Effect("pure"),)),
            "must match")
    node = ir.Node("good_node", "weave.artifact",
                   inputs=(("version", "semver"),), effects=(ir.Effect("pure"),))
    assert node.operation == "weave.artifact"


@machine("Dependencies — all semantic dependencies are explicit",
         "a requirement must resolve to a node id or an event type")
def c6():
    rejects(lambda: ir.IRDocument.from_canonical(
        with_(DOC, "nodes.0.requirements", ["NOWHERE"])), "unknown 'NOWHERE'")
    rejects(lambda: ir.IRDocument.from_canonical(
        with_(DOC, "intent.requires", ["NOWHERE"])), "unknown 'NOWHERE'")


@machine("Dataflow — implicit shared state is forbidden",
         "expressions see only the declared scope; unknown ports raise")
def c7():
    rejects(lambda: ir.evaluate({"get": ["f", "version"]}, {}),
            "unknown port")
    rejects(lambda: ir.evaluate({"get": ["os", "environ"]}, {"f": {}}), "unknown port")
    assert ir.evaluate({"get": ["f", "failed"]}, {"f": {"failed": 0}}) == 0


@machine("Events — first-class, replayable entities",
         "id, type, logical clock, source, canonical payload")
def c8():
    good = ir.Event("e9", "CI_GREEN", 9, "ci",
                    {"date": "2026-11-01", "passed": 1, "failed": 0})
    assert ir.digest(good.to_canonical()) == ir.digest(good.to_canonical())
    rejects(lambda: ir.Event("e9", "CI_GREEN", True, "ci", {}),
            "clock must be a non-negative int")
    rejects(lambda: ir.Event("", "CI_GREEN", 1, "ci", {}), "event id")
    rejects(lambda: ir.Event("e9", "CI_GREEN", 1, "ci", {"date": 1.5}),
            "not a canonical value")
    # v1.4: constraints may look BACK — the 'prior' port exposes the latest
    # payload of every event that already happened (fact() in Jacquard)
    backward = ir.IRDocument.from_canonical({
        "ir_version": ir.IR_VERSION, "schema_version": "1.0.0",
        "compatibility_version": ir.IR_VERSION, "namespaces": {},
        "initial": {},
        "intent": {"goal": "g", "requires": ["b"], "constraints": [],
                   "prefer": []},
        "nodes": [{"id": "b", "operation": "weave.op", "inputs": [],
                   "outputs": [], "requirements": ["SIGNED"],
                   "effects": [{"kind": "pure"}]}],
        "events": [
            {"event_type": "CUT", "facts": [["date", "date"]]},
            {"event_type": "SIGNED", "facts": [["date", "date"]],
             "constraints": [{
                 "id": "not_before_cut",
                 "expr": {"ge": [{"get": ["f", "date"]},
                                  {"get": ["prior", "CUT", "date"]}]},
                 "message": "sign-off no earlier than the tag"}]}]})
    ordered = [ir.Event("e1", "CUT", 1, "w", {"date": "2026-09-27"}),
               ir.Event("e2", "SIGNED", 2, "w", {"date": "2026-09-28"})]
    assert ir.reduce(backward, ordered)["frontier"]["b"] == "ready"
    rejects(lambda: ir.reduce(backward, [
        ir.Event("e2", "SIGNED", 1, "w", {"date": "2026-09-28"})]),
        "prior.CUT.date")


@machine("State — transitions are explicit and fold deterministically",
         "the reducer folds the ordered log into canonical state; "
         "v1.2 transitions() gives the per-transition event-sourcing chain")
def c9():
    state = ir.reduce(DOC, base_log())
    assert state["happened"]["TAG_CUT"][0]["version"] == "v2.0.0"
    assert ir.digest(state) == ir.digest(ir.reduce(DOC, base_log()))
    committed = base_log() + [
        ir.ExecutionRecord("release_notes", "completed"),
        ir.Commit("x1", "release_notes", "sha256:" + "b" * 64, ())]
    chain = ir.transitions(DOC, committed)
    assert len(chain) == 4
    assert chain[-1]["state_digest"] == ir.digest(ir.reduce(DOC, committed))
    assert ir.digest(chain) == ir.digest(ir.transitions(DOC, committed))


@machine("Execution Frontier — ready/blocked/running/completed/failed/canceled/speculative",
         "computed statuses are never logged; blocked flips to ready on the event")
def c10():
    empty = ir.compute_frontier(DOC, [])
    assert empty == {"release_notes": "blocked", "test_report": "blocked",
                     "announcement": "speculative"}
    after = ir.compute_frontier(DOC, base_log())
    assert after["release_notes"] == "ready" and after["test_report"] == "ready"
    rejects(lambda: ir.ExecutionRecord("release_notes", "ready"),
            "execution status 'ready'")
    # v1.3: the logged-status transition matrix, EXHAUSTIVELY checked —
    # every (previous, next) pair must match the kernel's LOGGED_TRANSITIONS
    def rec(status):
        return ir.ExecutionRecord(
            "release_notes", status,
            ir.IRError("release_notes", "weave.artifact", "probe", "matrix",
                       "transient", True) if status == "failed" else None)
    for prev in ir.EXECUTION_STATUSES:
        for nxt in ir.EXECUTION_STATUSES:
            legal = ir._legal_status_change(prev, nxt)
            if prev in ("completed", "failed", "canceled"):
                assert legal == (nxt == prev), "terminal statuses are frozen"
            else:
                assert legal, "a running node may finish or keep running"
            log = [rec(prev), rec(nxt)]
            if legal:
                ir.compute_frontier(DOC, log)          # must not raise
            else:
                rejects(lambda l=log: ir.compute_frontier(DOC, l), "terminal")
    # a node's first record may be any logged status
    for status in ir.EXECUTION_STATUSES:
        ir.compute_frontier(DOC, [rec(status)])


@machine("Concurrency — independent nodes are ready simultaneously",
         "whether they RUN concurrently is a runtime decision (C25)")
def c11():
    after = ir.compute_frontier(DOC, base_log())
    ready = [n for n, s in after.items() if s == "ready"]
    assert len(ready) >= 2, "independent nodes must be ready at once"
    conflict = with_(DOC, "nodes.1.requirements", ["release_notes"])
    frontier = ir.compute_frontier(ir.IRDocument.from_canonical(conflict), base_log())
    assert frontier["test_report"] == "blocked", \
        "an explicit dependency blocks until the node completes"
    # v1.3: WAVE SAFETY — on every prefix of every log, any two ready nodes
    # are mutually independent (a wave may run them in parallel, §9)
    by_id = {n.id: n for n in DOC.nodes}
    log = base_log()
    for i in range(len(log) + 1):
        frontier = ir.compute_frontier(DOC, log[:i])
        ready = [nid for nid, s in frontier.items() if s == "ready"]
        for a in ready:
            for b in ready:
                if a != b:
                    assert b not in by_id[a].requirements, \
                        f"wave conflict: ready '{a}' still requires '{b}'"


@machine("Determinism — same inputs, byte-equal canonical result",
         "IR + ordered log → identical state digests, run after run")
def c12():
    first = ir.digest(ir.reduce(DOC, base_log()))
    second = ir.digest(ir.reduce(DOC, base_log()))
    assert first == second
    assert ir.dumps({"b": 1, "a": 2}) == ir.dumps({"a": 2, "b": 1}) == '{"a":2,"b":1}'


@machine("Effects — every operation declares them; the set is closed",
         "undeclared (empty) effects are forbidden — 'pure' must be explicit")
def c13():
    assert ir.Effect("pure").kind == "pure"
    rejects(lambda: ir.Effect("gpu"), "unknown effect kind 'gpu'")
    rejects(lambda: ir.IRDocument.from_canonical(
        with_(DOC, "nodes.0.effects", [])), "undeclared effects")


@machine("Capabilities — external resources are grant-based",
         "execution is permitted only when every non-pure effect is granted")
def c14():
    effects = (ir.Effect("external_service"), ir.Effect("pure"))
    assert ir.check_capabilities(["external_service"], effects) == []
    assert ir.check_capabilities([], effects) == ["external_service"]
    rejects(lambda: ir.Capability("root"), "must be an effect kind")


@machine("Errors — canonical structured errors",
         "node, operation, error_type, cause, recoverability, retryability")
def c15():
    good = ir.IRError("test_report", "weave.artifact", "constraint_broken",
                      "selvedge", "transient", True)
    assert good.to_canonical()["retryable"] is True
    rejects(lambda: ir.IRError("n", "op", "t", "c", "maybe", True),
            "recoverability")
    failed = ir.ExecutionRecord(
        "test_report", "failed",
        ir.IRError("test_report", "weave.artifact", "constraint_broken",
                   "no failing tests", "permanent", False))
    restored = ir.ExecutionRecord.from_canonical(failed.to_canonical())
    assert restored.error.error_type == "constraint_broken"
    rejects(lambda: ir.ExecutionRecord("test_report", "failed"),
            "structured error")


@machine("Cancellation — first-class and terminal",
         "a canceled node never silently flips to another status")
def c16():
    log = base_log() + [ir.ExecutionRecord("release_notes", "canceled")]
    assert ir.compute_frontier(DOC, log)["release_notes"] == "canceled"
    flips = log + [ir.ExecutionRecord("release_notes", "completed")]
    rejects(lambda: ir.compute_frontier(DOC, flips), "terminal")


@machine("Speculation — explicit, never authoritative until commit",
         "a speculative node shows as 'speculative' until its trigger happens; "
         "mixing speculative results into authoritative state is Phase 7 runtime work")
def c17():
    before = ir.compute_frontier(DOC, base_log())
    assert before["announcement"] == "speculative"
    published = base_log() + [ir.Event("e3", "PUBLISHED", 3, "bot",
                                       {"date": "2026-11-02"})]
    after = ir.compute_frontier(DOC, published)
    assert after["announcement"] == "ready"


@machine("Commit — an explicit, atomic boundary",
         "state is a content address; artifacts are refs")
def c18():
    good = ir.Commit("x1", "release_notes", "sha256:" + "b" * 64, ())
    assert good.state.startswith("sha256:")
    rejects(lambda: ir.Commit("x1", "release_notes", "v1.0.0"), "commit state")
    rejects(lambda: ir.Commit("x1", "release_notes", "sha256:" + "b" * 64,
                              ["not-a-ref"]), "commit artifacts")
    good_commit = ir.Commit("x1", "release_notes", "sha256:" + "b" * 64,
                            ({"$cas": "sha256:" + "c" * 64},))
    log = base_log() + [ir.ExecutionRecord("release_notes", "completed"),
                        good_commit]
    state = ir.reduce(DOC, log)
    assert state["commits"]["release_notes"]["execution_id"] == "x1"
    rejects(lambda: ir.reduce(DOC, log + [good_commit]), "duplicate commit")
    rejects(lambda: ir.reduce(DOC, base_log() + [good_commit]),
            "without a completed")
    # stale: a commit against a FAILED execution is rejected the same way
    stale = base_log() + [
        ir.ExecutionRecord("release_notes", "failed",
                           ir.IRError("release_notes", "weave.artifact",
                                      "handler_crash", "boom", "transient", True)),
        good_commit]
    rejects(lambda: ir.reduce(DOC, stale), "without a completed")
    # abandoned: a running record with no outcome yet is a legal prefix —
    # the node is 'running', not ready, and no state is corrupted
    running = base_log() + [ir.ExecutionRecord("release_notes", "running")]
    state = ir.reduce(DOC, running)
    assert state["frontier"]["release_notes"] == "running"
    # idempotent: re-appending the same terminal record changes nothing
    twice = base_log() + [ir.ExecutionRecord("release_notes", "completed"),
                          ir.ExecutionRecord("release_notes", "completed")]
    assert ir.digest(ir.reduce(DOC, twice)) == \
        ir.digest(ir.reduce(DOC, base_log() +
                            [ir.ExecutionRecord("release_notes", "completed")]))


@machine("Replay — from IR + initial state + ordered log",
         "language-independent: the reducer is pure and byte-stable")
def c19():
    state = ir.reduce(DOC, base_log())
    assert ir.digest(state) == ir.digest(ir.reduce(DOC, base_log()))
    assert state["frontier"] == ir.compute_frontier(DOC, base_log())
    committed = base_log() + [
        ir.ExecutionRecord("release_notes", "completed"),
        ir.Commit("x1", "release_notes", "sha256:" + "b" * 64, ())]
    assert ir.digest(ir.reduce(DOC, committed)) == \
        ir.digest(ir.reduce(DOC, list(committed)))
    rejects(lambda: ir.reduce(DOC, [ir.Event("e9", "MYSTERY", 1, "x", {})]),
            "not declared")


@machine("Provenance — produced values carry their origin",
         "producer, operation, version, execution_id, input refs")
def c20():
    ref = {"$cas": "sha256:" + "c" * 64}
    good = ir.Provenance("release_notes", "weave.artifact", "1.0.0", "x1", (ref,))
    assert good.to_canonical()["execution_id"] == "x1"
    rejects(lambda: ir.Provenance("n", "op", "1.0.0", "x1", ("not-a-ref",)),
            "provenance inputs")
    provenance = ir.Provenance("release_notes", "weave.artifact", "1.3.0", "x1",
                               ({"$cas": "sha256:" + "c" * 64},))
    commit = ir.Commit("x1", "release_notes", "sha256:" + "b" * 64,
                       ({"$cas": "sha256:" + "d" * 64},), provenance)
    restored = ir.Commit.from_canonical(commit.to_canonical())
    assert restored.provenance.producer == "release_notes"
    state = ir.reduce(DOC, base_log() + [ir.ExecutionRecord("release_notes",
                                                            "completed"), commit])
    assert state["commits"]["release_notes"]["provenance"]["operation"] == \
        "weave.artifact"


@machine("Content addressing — immutable artifacts by their content",
         "document identity and commit states are sha256 addresses")
def c21():
    assert DOC.addr.startswith("sha256:") and len(DOC.addr) == 71
    assert ir.digest({"k": [1, {"z": True, "a": None}]}) == \
        ir.digest({"k": [1, {"a": None, "z": True}]})


@machine("Serialization — normative deterministic form",
         "sorted keys, no whitespace, UTF-8, ints; floats are not canonical in v1")
def c22():
    assert ir.dumps({"b": [1, 2], "a": "é"}) == '{"a":"é","b":[1,2]}'
    assert ir.loads(ir.dumps({"a": ["x", {"y": 3}]})) == {"a": ["x", {"y": 3}]}
    rejects(lambda: ir.dumps(float("nan")), "not a canonical value")


# honest DECLARED contract (rule 15 — never fake it):
declared("C23", "Language Adapter — source ⇄ Canonical IR",
         "FIRST REAL ADAPTER SHIPPED (adapter.py, STEP H): the Jacquard "
         "pattern → IR lowering is implemented and machine-checked there "
         "(determinism, kernel rejections, template registry, explicit "
         "gaps); the FULL bidirectional ABI (IR → source, further sources) "
         "remains declared — one adapter is not the whole contract")


@machine("Conformance — this suite is the shared gate",
         "every C0–C31 contract is registered exactly once")
def c24():
    ids = [c[0] for c in CONTRACTS]
    assert len(ids) == 32, f"32 contracts expected, {len(ids)} registered"
    assert len(set(ids)) == 32, "duplicate contract ids"
    expected = [f"C{i}" for i in range(32)]
    assert ids == expected, "contracts must register in order C0–C31"


@machine("Distribution — the kernel assumes no machine, process or language",
         "import audit: canonical.py imports no I/O or OS modules; "
         "no mutable module-level state")
def c25():
    source = Path("canonical.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    allowed = {"hashlib", "json", "re", "dataclasses", "types", "__future__"}
    forbidden = imported - allowed
    assert not forbidden, f"the kernel imports non-portable modules: {sorted(forbidden)}"
    mutable = {name: name for name, v in vars(ir).items()
               if not name.startswith("__") and isinstance(v, (list, dict, set))}
    assert not mutable, f"the kernel keeps mutable module state: {sorted(mutable)}"


@machine("Security — untrusted IR is validated mandatorily",
         "from_canonical is the only entrance and it rejects everything invalid")
def c26():
    rejects(lambda: ir.IRDocument.from_canonical(with_(DOC, "nodes.0", {"id": "x"})),
            "missing keys")
    rejects(lambda: ir.IRDocument.from_canonical(
        with_(DOC, "nodes.0.effects", [{"kind": "network"}, {"kind": "nonsense"}])),
        "unknown effect kind 'nonsense'")


@machine("Extensibility — extensions live in declared namespaces",
         "an undeclared dotted prefix is rejected; a declared one passes")
def c27():
    rejects(lambda: ir.IRDocument.from_canonical(
        with_(DOC, "nodes.0.operation", "ext.op")), "undeclared namespace")
    namespaced = with_(DOC, "namespaces", {"ext": "https://example.com/ns/ext"})
    namespaced = with_(ir.IRDocument.from_canonical(namespaced),
                       "nodes.0.operation", "ext.op")
    doc2 = ir.IRDocument.from_canonical(namespaced)
    assert doc2.nodes[0].operation == "ext.op"


# honest DECLARED contract (rule 15 — never fake it):
declared("C28", "Semantic Priority — canonical semantics over implementations",
         "a review policy, enforced from Phase 3 onward when the Python runtime "
         "is rebuilt on the IR; not machine-checkable by a single runtime")


@machine("No hidden semantics — behavior is representable in the IR",
         "unknown keys, unknown events, unknown payload facts are all rejected")
def c29():
    rejects(lambda: ir.IRDocument.from_canonical(with_(DOC, "nodes.0.goal", "x")),
            "unknown keys")
    rejects(lambda: ir.reduce(DOC, [ir.Event("e9", "CI_GREEN", 9, "ci",
                                             {"date": "2026-11-01", "passed": 1,
                                              "failed": 0, "extra": 1})]),
            "unknown facts")
    rejects(lambda: ir.reduce(DOC, [ir.Event("e9", "CI_GREEN", 9, "ci", {})]),
            "missing fact")


@machine("Minimal kernel — the concept inventory is fixed and audited",
         "every KERNEL_CONCEPTS entry exists; growing it is a deliberate act")
def c30():
    missing = [name for name in ir.KERNEL_CONCEPTS if not hasattr(ir, name)]
    assert not missing, f"kernel concepts missing: {missing}"
    assert len(ir.EXPRESSION_OPS) == 16 and len(ir.CANONICAL_TYPES) == 9


@machine("Intent / Constraint separation — WHAT, never HOW",
         "intent is a separate section; preferences never change the frontier "
         "(the runtime derives the plan, the IR does not prescribe it)")
def c31():
    rejects(lambda: ir.IRDocument.from_canonical(
        with_(DOC, "intent", {})), "intent: missing keys")
    without = DOC.to_canonical()
    without["intent"]["prefer"] = []
    with_pref = DOC.to_canonical()
    with_pref["intent"]["prefer"] = ["sequential_execution"]
    f1 = ir.compute_frontier(ir.IRDocument.from_canonical(without), base_log())
    f2 = ir.compute_frontier(ir.IRDocument.from_canonical(with_pref), base_log())
    assert f1 == f2, "preferences must not change semantics — only the plan"



# ───────────────────────── runner ─────────────────────────

def schema_gate() -> bool:
    """The JSON Schema is the first gate for untrusted documents (C26):
    a cheap structural check before the kernel's full semantic validation.
    Honest degradation (rule 15): if jsonschema is not installed the gate
    is reported NOT RUN, never silently passed."""
    try:
        import jsonschema
    except ImportError:
        return False
    schema_path = Path(__file__).resolve().parent / "canonical.schema.json"
    if not schema_path.exists():
        # Fallback: running from a source checkout where the package layout
        # differs (e.g. flat-module source tree) — look next to conformance.py.
        schema_path = Path(__file__).resolve().parent / "canonical.schema.json"
    schema = __import__("json").loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(DOC.to_canonical())
    return True


def run() -> int:
    global DOC, LOG
    DOC = ir.example_document()
    LOG = base_log()

    print("CONFORMANCE SUITE — Canonical WEAVE IR v1.2 (C0–C31)")
    print(f"  schema gate: {'PASS' if schema_gate() else 'NOT RUN (jsonschema absent)'}")
    print("─" * 74)
    failures = 0
    machine_checked = 0
    for cid, title, kind, note, fn in CONTRACTS:
        if kind == "DECLARED":
            print(f"  {cid:<4} {title[:52]:<52} DECLARED")
            continue
        machine_checked += 1
        try:
            fn()
            print(f"  {cid:<4} {title[:52]:<52} ✓")
        except Exception as e:
            failures += 1
            print(f"  {cid:<4} {title[:52]:<52} ✗ FAILED")
            print(f"       {type(e).__name__}: {e}")
    print("─" * 74)
    declared_count = sum(1 for c in CONTRACTS if c[2] == "DECLARED")
    print(f"  machine-checked: {machine_checked - failures}/{machine_checked} PASS · "
          f"declared: {declared_count} · FAILED: {failures}")
    for cid, title, kind, note, fn in CONTRACTS:
        if kind == "DECLARED":
            print(f"  ⚠ {cid} is DECLARED, not implemented: {note}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
