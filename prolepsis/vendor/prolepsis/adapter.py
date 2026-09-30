# -*- coding: utf-8 -*-
"""
JACQUARD → CANONICAL IR — the first real language adapter (C23, STEP H)
========================================================================
Directive v2 §11/§12: prove that a source language compiles into the
Canonical IR without losing or inventing semantics, then execute the SAME
meaning through the reference runtime.

  patterns/release.yaml  (JACQUARD — the WEAVE pattern language)
        ↓  parse (jacquard.load_pattern: YAML + JSON Schema + the reed)
        ↓  validate (source shape, canonical data)
        ↓  lower   (selvedges → operator trees; artifacts/events → nodes)
        ↓  canonicalize (deterministic: same source → same document address)
  AdapterBundle = IRDocument + template registry + diagnostics

Lowering map (the contract of THIS adapter):

  yarn + data          →  document.initial ("one pattern — many cloths":
                          the release data IS the initial state)
  artifacts            →  nodes (operation jacquard.render), requirements =
                          the transitive closure of the weaving event
  warping.shared fans  →  jacquard.warp nodes (woven ONCE, fanned out as
                          ref inputs — CAS makes the block physically one)
  events               →  EventDecls (facts + canonical types)
  selvedge checks      →  constraints as operator trees:
                          f.x → get(f,x) · pattern.y → get(doc,y)
                          total(a,"k") → sum(a,"k") (kernel v1.1+)
                          fact("E").x → get(prior,E,x) (kernel v1.4+)
  warping levels       →  Node.hints — POLICY, not semantics (Phase 0 freeze)
  weft/title templates →  the template REGISTRY (source concern, not IR:
                          the IR carries the graph, the adapter ships the
                          bodies for the runtime's handlers)
  unravels             →  R10 cancellation anchor + compensation node.
       The adapter emits artifact→artifact requirements so the canonical
       runtime's transitive unraveling policy can cancel pending dependents.
  Note: event.requires (inter-event order) lowers to transitive node
       requirements; the canonical log itself stays world-facts-only — a
       deliberate model difference, documented in docs/canonical-ir.md.

Run:   python3 adapter.py     (self-check, then the end-to-end proof)
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from . import canonical as ir
from . import jacquard as j

ADAPTER_VERSION = "1.0.0"
JACQUARD_NS = "https://prolepsis.dev/ns/jacquard"

# Jacquard fact types → canonical types (C3; 'number' is int-only: C22)
FACT_TYPE_MAP = {"semver": "semver", "date": "date", "string": "string",
                 "number": "int", "int": "int", "list": "list"}



class AdapterError(Exception):
    """The source cannot be lowered — reported with a code, never silent."""


class Diagnostic:
    """A structured adapter finding (warnings for honest gaps)."""

    def __init__(self, severity: str, code: str, message: str,
                 source: str | None = None):
        self.severity, self.code, self.message, self.source = (
            severity, code, message, source)

    def to_canonical(self):
        value = {"severity": self.severity, "code": self.code,
                 "message": self.message}
        if self.source is not None:
            value["source"] = self.source
        return value

    @classmethod
    def from_canonical(cls, value):
        return cls(value["severity"], value["code"], value["message"],
                   value.get("source"))

    def __repr__(self):
        return f"[{self.severity}] {self.code}: {self.message}"


# ─────────────────── selvedge expressions → operator trees ───────────────────

_TOKEN = re.compile(r'''
    (?P<ws>\s+)
  | (?P<op>==|!=|>=|<=|>|<)
  | (?P<str>"[^"]*")
  | (?P<num>-?\d+)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<punct>[().,])
''', re.X)


def _tokens(text: str):
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise AdapterError(f"selvedge syntax: cannot tokenize at "
                               f"'{text[pos:]}' in: {text}")
        pos = m.end()
        if m.lastgroup != "ws":
            out.append((m.lastgroup, m.group(m.lastgroup)))
    return out


class _Parser:
    """Recursive descent over the Jacquard selvedge subset:

        check := operand (==|!=|>=|<=|>|<) operand
        operand := f.name | pattern.name | total(operand, "field")
                 | fact("EVENT").name | number | "string"
    """

    def __init__(self, tokens, source: str):
        self.tokens, self.pos, self.source = tokens, 0, source

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self, kind=None, value=None):
        item = self.peek()
        if item is None or (kind and item[0] != kind) or \
                (value and item[1] != value):
            raise AdapterError(f"selvedge syntax: expected {kind or value} "
                                f"at token {item} in: {self.source}")
        self.pos += 1
        return item

    def parse(self):
        left = self.operand()
        kind, symbol = self.take("op")
        right = self.operand()
        if self.peek() is not None:
            raise AdapterError(f"selvedge syntax: trailing tokens in: "
                               f"{self.source}")
        op = {"==": "eq", "!=": "ne", ">=": "ge",
              "<=": "le", ">": "gt", "<": "lt"}[symbol]
        return {op: [left, right]}

    def operand(self):
        kind, value = self.take()
        if kind == "num":
            return {"lit": int(value)}
        if kind == "str":
            return {"lit": value[1:-1]}
        if kind != "name":
            raise AdapterError(f"selvedge syntax: unexpected '{value}' in: "
                               f"{self.source}")
        if value == "f":
            field = self.take("punct", ".")[1] if False else None
            self.take("punct", ".")
            name = self.take("name")[1]
            return {"get": ["f", name]}
        if value == "pattern":
            self.take("punct", ".")
            name = self.take("name")[1]
            return {"get": ["doc", name]}
        if value == "total":
            self.take("punct", "(")
            inner = self.operand()
            self.take("punct", ",")
            field = self.take("str")[1][1:-1]
            self.take("punct", ")")
            return {"sum": [inner, field]}
        if value == "fact":
            self.take("punct", "(")
            event = self.take("str")[1][1:-1]
            self.take("punct", ")")
            self.take("punct", ".")
            name = self.take("name")[1]
            return {"get": ["prior", event, name]}
        raise AdapterError(f"selvedge syntax: unknown operand '{value}' in: "
                           f"{self.source} (adapter v{ADAPTER_VERSION} lowers "
                           "f./pattern./total()/fact() only)")


def parse_selvedge(text: str) -> dict:
    return _Parser(_tokens(text), text).parse()


# ───────────────────────────── the adapter ─────────────────────────────

def _node_id(name: str) -> str:
    """ReleaseNotes → release_notes · RELEASE_CANCELED → release_canceled
    (C1 node grammar)."""
    if name == name.upper():                    # an ALL_CAPS event name
        snake = name.lower()
    else:                                       # a CamelCase artifact name
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return re.sub(r"[^a-z0-9_-]", "", snake) or "node"


def _slug(rule: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", rule.strip().lower()).strip("_")
    return slug or "selvedge"


def _closure(event: str, events: dict) -> list:
    """The event and all its transitive ancestors (Jacquard requires)."""
    seen, stack = [], [event]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.append(current)
        stack.extend((events[current].get("requires") or []))
    return list(dict.fromkeys(seen))            # stable order, no duplicates


def _used_facts(*templates: str) -> list:
    names = set()
    for template in templates:
        names.update(re.findall(r"\bf\.(\w+)", template))
    return sorted(names)


def adapt(pattern: dict, source_name: str = "pattern.yaml") -> "AdapterBundle":
    """Lower a loaded Jacquard pattern into a Canonical IR document.
    Deterministic: the same source always yields the same document address."""
    diagnostics: list[Diagnostic] = []
    nodes: list[dict] = []
    templates: dict[str, dict] = {}
    provenance = {"source": source_name, "source_version": f"JACQUARD {j.__name__} v0.4",
                  "events": {}, "nodes": {}}

    # 1. initial state: yarn + data are the cloth's constants (canonical)
    initial = dict(pattern["data"])
    try:
        ir.validate_value(initial)
    except ir.CanonicalError as e:
        raise AdapterError(f"pattern data is not canonical: {e}")

    # 2. shared warp blocks → jacquard.warp nodes, woven once, fanned out
    fan_of: dict[str, list[str]] = {}            # artifact → warp node ids
    for index, shared in enumerate(pattern["shared"], 1):
        node = f"warp_{index}"
        nodes.append({"id": node, "operation": "jacquard.warp",
                      "inputs": [], "outputs": [[node, "ref"]],
                      "requirements": [], "constraints": [],
                      "effects": [{"kind": "pure"}],
                      "speculative": False,
                      "hints": {"jacquard_warping": "hard"}})
        templates[node] = {"kind": "warp", "block": shared["block"]}
        provenance["nodes"][node] = {
            "source": f"warping.shared[{index}]",
            "kind": "shared_warp",
        }
        for target in shared.get("into", ()):
            fan_of.setdefault(target, []).append(node)

    # 3. events → EventDecls; weave/unravel structure → artifact nodes
    events: list[dict] = []
    woven: dict[str, tuple] = {}                 # artifact → (event, template)
    for event_name, spec in pattern["events"].items():
        fact_types = spec.get("fact_types") or {}
        facts = []
        for fact_name in spec.get("facts", ()):
            kind = fact_types.get(fact_name)
            if kind not in FACT_TYPE_MAP:
                raise AdapterError(
                    f"event '{event_name}': fact '{fact_name}' has unknown "
                    f"type '{kind}' (supported: {sorted(FACT_TYPE_MAP)})")
            facts.append([fact_name, FACT_TYPE_MAP[kind]])
        constraints = []
        for selvedge in spec.get("selvedges", ()):
            constraints.append({
                "id": _slug(selvedge["rule"]),
                "expr": parse_selvedge(selvedge["check"]),
                "message": selvedge["rule"]})
        events.append({"event_type": event_name, "facts": facts,
                       "constraints": constraints})
        provenance["events"][event_name] = {
            "source": f"events.{event_name}",
            "kind": "event",
        }

        for artifact, template in (spec.get("weaves") or {}).items():
            if artifact in woven:
                raise AdapterError(
                    f"artifact '{artifact}' is woven by two events "
                    f"('{woven[artifact][0]}' and '{event_name}')")
            woven[artifact] = (event_name, template)
        if "unravels" in spec:
            unravel = spec["unravels"]
            after = unravel.get("after")
            if not after or after not in pattern["artifacts"]:
                raise AdapterError(
                    f"event '{event_name}' unravels after unknown artifact "
                    f"'{after}'")
            node = f"unravel_{_node_id(event_name)}"
            compensation = unravel.get("compensation", "")
            used = _used_facts(compensation)
            known = set(spec.get("facts", ()))
            unknown = [f for f in used if f not in known]
            if unknown:
                raise AdapterError(f"compensation template of '{event_name}' "
                                   f"uses unknown facts {unknown}")
            # The compensation requires the unraveler EVENT, not its anchor.
            # If it depended on the anchor node it would itself be canceled by
            # R10 before it could compensate. Downstream artifacts carry the
            # actual anchor dependency below.
            requirements = _closure(event_name, pattern["events"])
            nodes.append({"id": node, "operation": "jacquard.unravel",
                          "inputs": [[f, FACT_TYPE_MAP[fact_types[f]]]
                                     for f in spec["facts"] if f in used],
                          "outputs": [["compensation", "ref"]],
                          "requirements": requirements,
                          "constraints": [],
                          "effects": [{"kind": "pure"}],
                          "speculative": False,
                          "hints": {"jacquard_unravel_after":
                                    _node_id(after or "")}})
            templates[node] = {"kind": "unravel", "weft": compensation}
            provenance["nodes"][node] = {
                "source": f"events.{event_name}.unravels",
                "kind": "unravel",
                "anchor": _node_id(after),
            }

    # 4. artifacts → jacquard.render nodes
    for artifact in pattern["artifacts"]:        # YAML order is meaningful
        if artifact not in woven:
            if pattern["artifacts"][artifact].get("warping") == "soft":
                diagnostics.append(Diagnostic(
                    "warning", "ARTIFACT_UNWOVEN",
                    f"soft artifact '{artifact}' is declared but no event "
                    "weaves it — no node is created"))
                continue
            raise AdapterError(f"artifact '{artifact}' is not woven by any "
                               "event")
        event_name, template = woven[artifact]
        title = pattern["artifacts"][artifact]["title"]
        warping = pattern["artifacts"][artifact].get("warping", "hard")
        used = _used_facts(title, template["weft"])
        spec = pattern["events"][event_name]
        fact_types = spec.get("fact_types") or {}
        known = set(spec.get("facts", ()))
        unknown = [f for f in used if f not in known]
        if unknown:
            raise AdapterError(f"weft of '{artifact}' uses unknown facts "
                               f"{unknown} (event '{event_name}' carries "
                               f"{sorted(known)})")
        requirements = _closure(event_name, pattern["events"])
        # C23 semantic lowering: make source-ordered outputs of one event
        # explicit dependencies, then connect descendants to artifacts from
        # their ancestor events. Unique artifact output ports (the node IDs)
        # keep these ordering/R10 edges from aliasing the generic `artifact`
        # port when multiple required nodes have different CAS refs.
        for prior_artifact, (producer_event, _) in woven.items():
            if producer_event == event_name:
                if prior_artifact == artifact:
                    break
                dep = _node_id(prior_artifact)
                if dep not in requirements:
                    requirements.append(dep)
        for ancestor_event in _closure(event_name, pattern["events"]):
            if ancestor_event == event_name:
                continue
            for ancestor_artifact, (woven_event, _) in woven.items():
                if woven_event == ancestor_event:
                    dep = _node_id(ancestor_artifact)
                    if dep not in requirements:
                        requirements.append(dep)
        requirements += [w for w in fan_of.get(artifact, [])
                         if w not in requirements]
        inputs = [[f, FACT_TYPE_MAP[fact_types[f]]]
                  for f in spec["facts"] if f in used]
        inputs += [[w, "ref"] for w in fan_of.get(artifact, [])]
        node_id = _node_id(artifact)
        nodes.append({"id": node_id, "operation": "jacquard.render",
                      "inputs": inputs, "outputs": [[node_id, "ref"]],
                      "requirements": requirements, "constraints": [],
                      "effects": [{"kind": "pure"}],
                      "speculative": False,
                      "hints": {"jacquard_warping": warping}})
        node_id = _node_id(artifact)
        templates[node_id] = {"kind": "render", "title": title,
                              "weft": template["weft"]}
        provenance["nodes"][node_id] = {
            "source": f"artifacts.{artifact}",
            "kind": "render",
            "event": event_name,
        }

    # 5. the document
    document = ir.IRDocument.from_canonical({
        "ir_version": ir.IR_VERSION, "schema_version": "1.0.0",
        "compatibility_version": ir.IR_VERSION,
        "namespaces": {"jacquard": JACQUARD_NS},
        "initial": initial,
        "intent": {
            "goal": "weave_cloth",
            "requires": [n["id"] for n in nodes
                         if n["operation"] != "jacquard.warp"],
            "constraints": [], "prefer": []},
        "nodes": nodes, "events": events})

    return AdapterBundle(document, templates, diagnostics, source_name,
                         f"JACQUARD {j.__name__} v0.4", provenance)


class AdapterBundle:
    """The adapter's output: the canonical document plus the template
    registry (source bodies for runtime handlers) and diagnostics."""

    def __init__(self, document: ir.IRDocument, templates: dict,
                 diagnostics: list, source_name: str, source_version: str,
                 provenance: dict | None = None):
        self.document = document
        self.templates = templates
        self.diagnostics = diagnostics
        self.source_name = source_name
        self.source_version = source_version
        self.provenance = provenance or {
            "source": source_name, "source_version": source_version,
            "events": {}, "nodes": {},
        }

    @property
    def addr(self) -> str:
        return self.document.addr

    def to_canonical(self) -> dict:
        return {"adapter_version": ADAPTER_VERSION,
                "source_name": self.source_name,
                "source_version": self.source_version,
                "ir_version": ir.IR_VERSION,
                "document": self.document.to_canonical(),
                "templates": self.templates,
                "diagnostics": [d.to_canonical() for d in self.diagnostics],
                "provenance": self.provenance}

    @classmethod
    def from_canonical(cls, value: dict) -> "AdapterBundle":
        return cls(ir.IRDocument.from_canonical(value["document"]),
                   value["templates"],
                   [Diagnostic.from_canonical(d)
                    for d in value["diagnostics"]],
                   value["source_name"], value["source_version"],
                   value.get("provenance"))


# ───────────────────────── the release facts (demo) ─────────────────────────

RELEASE_FACTS = [
    ("e1", "TAG_CUT", 1, "release-bot",
     {"version": "v1.4.2", "date": "2026-09-27"}),
    ("e2", "CI_GREEN", 2, "ci",
     {"date": "2026-09-27", "passed": 612, "failed": 0}),
    ("e3", "ARTIFACTS_BUILT", 3, "buildbot",
     {"date": "2026-09-27",
      "files": [{"file": "weave-1.4.2.tar.gz", "size": 148},
                {"file": "weave-1.4.2-py3-none-any.whl", "size": 152}]}),
    ("e4", "PUBLISHED", 4, "bot",
     {"date": "2026-09-27", "registry": "ghcr.io/jotalbot/prolepsis"}),
    ("e5", "RELEASE_SIGNED", 5, "maintainer",
     {"date": "2026-09-27", "signer": "JoTalbot"}),
]


def release_log():
    return [ir.Event(*facts) for facts in RELEASE_FACTS]


# ───────────────────────── the self-check (C23 gate) ─────────────────────────

def self_check() -> None:
    pattern = j.load_pattern("patterns/release.yaml")
    bundle = adapt(pattern)

    # ABI: deterministic — same source, same address; round-trip is stable
    again = adapt(j.load_pattern("patterns/release.yaml"))
    assert bundle.addr == again.addr, "adapter is not deterministic"
    restored = AdapterBundle.from_canonical(bundle.to_canonical())
    assert restored.addr == bundle.addr and \
        restored.templates == bundle.templates
    assert restored.provenance == bundle.provenance

    # lowering shape: 7 artifacts + 2 warp fans + 1 unravel compensation
    operations = {}
    for node in bundle.document.nodes:
        operations[node.operation] = operations.get(node.operation, 0) + 1
    assert operations == {"jacquard.warp": 2, "jacquard.render": 7,
                          "jacquard.unravel": 1}, operations
    assert len(bundle.document.events) == 6
    constraints = sum(len(e.constraints) for e in bundle.document.events)
    assert constraints == 5, "five selvedges must lower to five constraints"
    assert not any(d.severity == "error" for d in bundle.diagnostics)
    render_nodes = [n for n in bundle.document.nodes
                    if n.operation == "jacquard.render"]
    assert all(n.outputs == ((n.id, "ref"),) for n in render_nodes), \
        "render outputs must have unique canonical dataflow names"
    release_notes = next(n for n in bundle.document.nodes
                         if n.id == "release_notes")
    test_report = next(n for n in bundle.document.nodes
                       if n.id == "test_report")
    build_manifest = next(n for n in bundle.document.nodes
                          if n.id == "build_manifest")
    checksums = next(n for n in bundle.document.nodes
                     if n.id == "checksums")
    docker_notes = next(n for n in bundle.document.nodes
                        if n.id == "docker_notes")
    announcement = next(n for n in bundle.document.nodes
                        if n.id == "announcement")
    unravel = next(n for n in bundle.document.nodes
                   if n.operation == "jacquard.unravel")
    assert release_notes.id in test_report.requirements
    assert release_notes.id in build_manifest.requirements
    # The Jacquard event lists TestReport before BuildManifest; the lowering
    # must preserve that producer ordering as an explicit node dependency.
    assert test_report.id in build_manifest.requirements
    assert release_notes.id not in unravel.requirements
    assert "RELEASE_CANCELED" in unravel.requirements
    assert checksums.id in announcement.requirements
    assert docker_notes.id in announcement.requirements
    assert bundle.provenance["nodes"]["release_notes"]["source"] ==         "artifacts.ReleaseNotes"

    # every selvedge rejects its bad facts through the KERNEL (not jacquard)
    def rejects_event(bad_event, needle):
        log = release_log()
        log = [bad_event if r.event_type == bad_event.event_type else r
               for r in log]          # replace IN PLACE, keep the world order
        try:
            ir.reduce(bundle.document, log)
        except ir.CanonicalError as e:
            assert needle in str(e), f"wrong rejection: {e}"
            return
        raise AssertionError(f"{bad_event.event_type} was not rejected")

    rejects_event(ir.Event("x1", "TAG_CUT", 1, "bot",
                           {"version": "v9.9.9", "date": "2026-09-27"}),
                  "the tag must match")
    rejects_event(ir.Event("x2", "CI_GREEN", 2, "ci",
                           {"date": "2026-09-27", "passed": 600, "failed": 12}),
                  "no failing tests")
    rejects_event(ir.Event("x3", "ARTIFACTS_BUILT", 3, "buildbot",
                           {"date": "2026-09-27",
                            "files": [{"file": "a.tar.gz", "size": 1}]}),
                  "the build matches")
    rejects_event(ir.Event("x4", "PUBLISHED", 4, "bot",
                           {"date": "2026-09-27", "registry": "evil.example"}),
                  "declared registry")
    # a sign-off dated BEFORE the tag breaks the back-looking selvedge
    # (an event that references a not-yet-happened event is already covered
    # by the kernel conformance suite: unknown prior port)
    rejects_event(ir.Event("x5", "RELEASE_SIGNED", 5, "x",
                           {"date": "2026-09-01", "signer": "x"}),
                  "no earlier than the tag")

    # the happy log folds: with every node completed, digest is stable
    happy = release_log() + [ir.ExecutionRecord(n.id, "completed")
                             for n in bundle.document.nodes]
    state = ir.reduce(bundle.document, happy)
    assert all(s == "completed" for s in state["frontier"].values()), state
    assert ir.digest(state) == ir.digest(
        ir.reduce(bundle.document, list(happy)))

    # adapter negatives: structured rejections, never silence
    broken = j.load_pattern("patterns/release.yaml")
    broken["data"]["bad"] = 1.5
    try:
        adapt(broken)
        raise AssertionError("float data was accepted")
    except (AdapterError, ir.CanonicalError):
        pass
    try:
        parse_selvedge("f.a && f.b")
        raise AssertionError("unsupported syntax was accepted")
    except AdapterError:
        pass
    double = j.load_pattern("patterns/release.yaml")
    double["events"]["PUBLISHED"]["weaves"]["TestReport"] = \
        double["events"]["CI_GREEN"]["weaves"]["TestReport"]
    try:
        adapt(double)
        raise AssertionError("an artifact woven twice was accepted")
    except AdapterError:
        pass

    total = len(bundle.document.nodes)
    print(f"SELF-CHECK: adapter deterministic · {total} nodes · "
          f"{len(bundle.document.events)} events · 5 selvedges canonical · "
          "5 kernel rejections · R10 dependency lowering verified")


def _loom_equivalent_weft(bundle, event_payloads):
    """What loom.py would render for the same facts (same renderer, same
    context shape) — the old WEAVE meaning of this pattern."""
    def direct(node_id, payload):
        template = bundle.templates[node_id]
        ctx = {"f": payload, "pattern": dict(bundle.document.initial),
               **j.YARN}
        return {"title": j.render(template["title"], ctx),
                "weft": j.render(template["weft"], ctx)}
    return direct


def demo() -> None:
    from . import runtime as rt

    print("JACQUARD → CANONICAL IR — the first adapter (C23, STEP H)")
    started = time.perf_counter()
    pattern = j.load_pattern("patterns/release.yaml")
    bundle = adapt(pattern)
    elapsed = (time.perf_counter() - started) * 1000

    by_op = {}
    for node in bundle.document.nodes:
        by_op.setdefault(node.operation, []).append(node.id)
    print(f"\n  [1] pattern lowered in {elapsed:.1f} ms")
    print(f"      document address: {bundle.addr}")
    for operation, ids in sorted(by_op.items()):
        print(f"      {operation:<18} {len(ids)}: {', '.join(ids)}")
    print(f"      events: {len(bundle.document.events)} · "
          f"selvedges→constraints: "
          f"{sum(len(e.constraints) for e in bundle.document.events)} · "
          f"facts: {sum(len(e.facts) for e in bundle.document.events)}")
    for diagnostic in bundle.diagnostics:
        print(f"      ⚠ {diagnostic}")

    # ── execute the SAME meaning through the canonical runtime ──
    handlers = {k: fn for k, fn in
                (("jacquard.render", _render_handler(bundle)),
                 ("jacquard.warp", _warp_handler(bundle)),
                 ("jacquard.unravel", _unravel_handler(bundle)))}
    result = rt.execute(bundle.document, handlers, log=release_log())
    frontier = result.state["frontier"]
    waiting = [k for k, s in frontier.items() if s != "completed"]
    print(f"\n  [2] canonical runtime: {result.waves} waves · "
          f"{result.executed} executions · "
          f"{sum(1 for s in frontier.values() if s == 'completed')}/"
          f"{len(frontier)} completed")
    if waiting:
        print(f"      waiting (no such event in this world): {waiting}")
    print(f"      state digest: {result.digest}")
    print(f"      artifacts: {len(result.store)} CAS blocks · "
          f"{result.store.bytes()} bytes "
          f"(2 fan blocks woven once, fanned to 3+2 manifests)")

    # ── the proof: old WEAVE meaning == canonical result ──
    payloads = {r.event_type: r.payload for r in release_log()}
    direct = _loom_equivalent_weft(bundle, payloads)
    equal = 0
    print(f"\n  [3] equivalence: canonical artifacts vs loom.py's renderer")
    for node in bundle.document.nodes:
        if node.operation != "jacquard.render":
            continue
        payload = {}
        for requirement in node.requirements:
            payload.update(payloads.get(requirement, {}))
        want = direct(node.id, payload)
        ref = result.state["commits"][node.id]["artifacts"][0]
        got = ir.loads(result.store.get(ref))
        assert got == want, f"{node.id}: canonical != direct render"
        equal += 1
        print(f"      {node.id:<16} byte-equal ✓  "
              f"({len(result.store.get(ref))} bytes)")
    print(f"      {equal}/{equal} artifacts identical to the old renderer")

    # ── R10: the adapter now wires the canonical cancellation graph ──
    print("\n  [4] R10 unraveling: Jacquard dependency lowering:")
    canceled_log = [ir.Event("c1", "TAG_CUT", 1, "release-bot",
                             {"version": "v1.4.2", "date": "2026-09-27"}),
                    ir.Event("c2", "RELEASE_CANCELED", 2, "maintainer",
                             {"reason": "regression found in smoke tests"})]
    # Phase 7 SHIPPED the runtime policy (runtime.py R10): an unraveler
    # event cancels every PENDING node transitively dependent on its anchor
    policy = {"RELEASE_CANCELED": "release_notes"}
    gap_result = rt.execute(bundle.document, handlers, log=canceled_log,
                            unravel=policy)
    frontier = gap_result.state["frontier"]
    print(f"      compensation woven: "
          f"{'unravel_release_canceled' in gap_result.state['commits']}")
    canceled = [k for k, s in frontier.items() if s == "canceled"]
    print(f"      canceled by the policy: {sorted(canceled) or 'none'}")
    print("      → canonical R10 canceled pending downstream nodes through")
    print("        the lowered artifact→artifact requirements; compensation")
    print("        remains an ordinary node requiring RELEASE_CANCELED")
    assert "unravel_release_canceled" not in canceled
    assert "test_report" in canceled and "build_manifest" in canceled


def _render_handler(bundle):
    def handler(node, inputs, ctx):
        template = bundle.templates[node.id]
        context = {"f": inputs, "pattern": dict(ctx.document.initial),
                   **j.YARN}
        return {node.id: ctx.store.put({
            "title": j.render(template["title"], context),
            "weft": j.render(template["weft"], context)})}
    return handler


def _warp_handler(bundle):
    def handler(node, inputs, ctx):
        context = {"f": {}, "pattern": dict(ctx.document.initial), **j.YARN}
        return {node.id: ctx.store.put(
            j.render(bundle.templates[node.id]["block"], context))}
    return handler


def _unravel_handler(bundle):
    def handler(node, inputs, ctx):
        context = {"f": inputs, "pattern": dict(ctx.document.initial),
                   **j.YARN}
        return {"compensation": ctx.store.put(
            j.render(bundle.templates[node.id]["weft"], context))}
    return handler


if __name__ == "__main__":
    try:
        self_check()
        demo()
    except (AdapterError, ir.CanonicalError, AssertionError) as e:
        print(f"ADAPTER FAILURE: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
