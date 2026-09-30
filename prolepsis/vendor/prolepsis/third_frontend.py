#!/usr/bin/env python3
"""PROLEPSIS-SCRIPT frontend: a small parsed DSL lowered through C23.

This frontend reads the line-oriented, non-JSON/non-YAML language in
`sources/release.prolepsis`. It parses declarations and a restricted
expression grammar; it never imports or calls the Jacquard or Workflow
lowerers. `main()` is the conformance gate, not part of lowering.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from . import canonical as ir
from .adapter_abi import AdapterResult, kernel_gate

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "sources" / "release.prolepsis"
NS = "https://prolepsis.dev/ns/jacquard"
COMPARE = {
    ast.Eq: "eq", ast.NotEq: "ne", ast.Gt: "gt",
    ast.GtE: "ge", ast.Lt: "lt", ast.LtE: "le",
}


class SourceError(ValueError):
    """Invalid PROLEPSIS-SCRIPT source with a stable, local diagnostic."""


def _node_id(name: str) -> str:
    snake = name.lower() if name == name.upper() else re.sub(
        r"(?<!^)(?=[A-Z])", "_", name).lower()
    return re.sub(r"[^a-z0-9_-]", "", snake) or "node"


def _string(text: str, line: int) -> str:
    try:
        value = ast.literal_eval(text)
    except (SyntaxError, ValueError) as exc:
        raise SourceError(f"line {line}: expected a quoted string") from exc
    if not isinstance(value, str):
        raise SourceError(f"line {line}: expected a quoted string")
    return value


def _literal(node: ast.AST):
    if isinstance(node, ast.Constant) and type(node.value) in (str, int):
        return {"lit": node.value}
    raise SourceError("expression literal must be a string or integer")


def _operand(node: ast.AST) -> dict:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id in {"fact", "doc"}:
            scope = "f" if node.value.id == "fact" else "doc"
            return {"get": [scope, node.attr]}
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "total" and len(node.args) == 2:
            target = _operand(node.args[0])
            key = node.args[1]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                return {"sum": [target, key.value]}
        if node.func.id == "prior" and len(node.args) == 2:
            event, field = node.args
            if (isinstance(event, ast.Constant) and isinstance(event.value, str)
                    and isinstance(field, ast.Constant)
                    and isinstance(field.value, str)):
                return {"get": ["prior", event.value, field.value]}
    if isinstance(node, ast.Constant):
        return _literal(node)
    raise SourceError(f"unsupported operand syntax: {ast.dump(node, include_attributes=False)}")


def parse_expression(source: str) -> dict:
    """Parse only the deterministic comparison subset used by this DSL."""
    try:
        tree = ast.parse(source, mode="eval").body
    except SyntaxError as exc:
        raise SourceError(f"invalid guard expression: {source}") from exc
    if not isinstance(tree, ast.Compare) or len(tree.ops) != 1 or len(tree.comparators) != 1:
        raise SourceError("a guard must contain exactly one comparison")
    op = COMPARE.get(type(tree.ops[0]))
    if op is None:
        raise SourceError("unsupported comparison operator")
    return {op: [_operand(tree.left), _operand(tree.comparators[0])]}


def parse(source: str) -> dict:
    """Parse PROLEPSIS-SCRIPT v1 into frontend-local declarations."""
    data: dict = {}
    artifacts: list[dict] = []
    warps: list[list[str]] = []
    events: list[dict] = []
    current: dict | None = None
    header_seen = False

    for line_no, raw in enumerate(source.splitlines(), 1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        if not header_seen:
            if text != "PROLEPSIS-SCRIPT 1":
                raise SourceError(f"line {line_no}: expected PROLEPSIS-SCRIPT 1")
            header_seen = True
            continue

        if current is not None:
            if text == "end":
                if not current["facts"]:
                    raise SourceError(f"line {line_no}: event has no facts")
                events.append(current)
                current = None
                continue
            match = re.fullmatch(r"fact ([A-Za-z_][A-Za-z0-9_]*) ([a-z]+)", text)
            if match:
                current["facts"].append([match.group(1), match.group(2)])
                continue
            if text.startswith("guard "):
                pieces = text[6:].split("|", 2)
                if len(pieces) != 3:
                    raise SourceError(f"line {line_no}: guard syntax is `guard ID | \"message\" | expression`")
                guard_id, message, expression = (part.strip() for part in pieces)
                if not re.fullmatch(r"[a-z][a-z0-9_]*", guard_id):
                    raise SourceError(f"line {line_no}: invalid guard identifier")
                current["constraints"].append({
                    "id": guard_id, "expr": parse_expression(expression),
                    "message": _string(message, line_no),
                })
                continue
            match = re.fullmatch(r"produce ([A-Za-z][A-Za-z0-9_]*)", text)
            if match:
                current["produces"].append(match.group(1))
                continue
            match = re.fullmatch(
                r"unravel ([A-Za-z][A-Za-z0-9_]*) uses ([a-z][A-Za-z0-9_, -]*)", text)
            if match:
                current["unravels"] = {"after": match.group(1)}
                current["compensation_uses"] = [
                    part.strip() for part in match.group(2).split(",")]
                continue
            raise SourceError(f"line {line_no}: unsupported event declaration")

        match = re.fullmatch(r"state ([a-z][a-z0-9_]*) = (.+)", text)
        if match:
            key, raw_value = match.groups()
            try:
                value = ast.literal_eval(raw_value)
            except (SyntaxError, ValueError) as exc:
                raise SourceError(f"line {line_no}: invalid state literal") from exc
            if type(value) not in (str, int):
                raise SourceError(f"line {line_no}: state scalar must be string or integer")
            if key in data:
                raise SourceError(f"line {line_no}: duplicate state key {key}")
            data[key] = value
            continue
        match = re.fullmatch(r"state (highlight|contributor|dist) \+= (.+)", text)
        if match:
            field, raw_value = match.groups()
            if field == "highlight":
                data.setdefault("highlights", []).append(_string(raw_value, line_no))
            elif field == "contributor":
                row = re.fullmatch(r'("(?:[^"\\]|\\.)*") commits ([0-9]+)', raw_value)
                if not row:
                    raise SourceError(f"line {line_no}: contributor syntax is `\"name\" commits INTEGER`")
                data.setdefault("contributors", []).append({
                    "name": _string(row.group(1), line_no),
                    "commits": int(row.group(2)),
                })
            else:
                row = re.fullmatch(r'("(?:[^"\\]|\\.)*") size ([0-9]+)', raw_value)
                if not row:
                    raise SourceError(f"line {line_no}: dist syntax is `\"file\" size INTEGER`")
                data.setdefault("dist", []).append({
                    "file": _string(row.group(1), line_no),
                    "size": int(row.group(2)),
                })
            continue
        match = re.fullmatch(
            r"artifact ([A-Za-z][A-Za-z0-9_]*) (hard|soft) uses ([a-z][a-z0-9_, -]*|none)", text)
        if match:
            name, warping, uses = match.groups()
            artifacts.append({"name": name, "warping": warping,
                              "uses": [] if uses == "none" else
                              [part.strip() for part in uses.split(",")]})
            continue
        match = re.fullmatch(r"warp fans ([A-Za-z][A-Za-z0-9_, -]*)", text)
        if match:
            warps.append([part.strip() for part in match.group(1).split(",")])
            continue
        match = re.fullmatch(
            r"event ([A-Z][A-Z0-9_]*) requires (-|[A-Z][A-Z0-9_]*(?:,[A-Z][A-Z0-9_]*)*)", text)
        if match:
            name, requirements = match.groups()
            current = {"name": name,
                       "requires": [] if requirements == "-" else
                       requirements.split(","),
                       "facts": [], "constraints": [], "produces": []}
            continue
        raise SourceError(f"line {line_no}: unsupported top-level declaration")

    if not header_seen:
        raise SourceError("empty source")
    if current is not None:
        raise SourceError(f"event {current['name']} is missing `end`")
    if not artifacts or not events:
        raise SourceError("source must declare artifacts and events")
    return {"data": data, "artifacts": artifacts, "shared_warps": warps,
            "events": events, "source_version": "PROLEPSIS-SCRIPT 1"}


def _closure(name: str, events: dict[str, dict]) -> list[str]:
    seen, stack = [], [name]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        if current not in events:
            raise SourceError(f"event requires unknown event {current}")
        seen.append(current)
        stack.extend(events[current].get("requires") or [])
    return list(dict.fromkeys(seen))


def lower(source: dict) -> AdapterResult:
    """Deterministically lower parsed DSL declarations, then apply C23 gate."""
    nodes, events_out = [], []
    provenance = {"source": "sources/release.prolepsis",
                  "source_version": source["source_version"],
                  "events": {}, "nodes": {}}
    events = {event["name"]: event for event in source["events"]}

    for index, fans in enumerate(source["shared_warps"], 1):
        nid = f"warp_{index}"
        nodes.append({"id": nid, "operation": "jacquard.warp", "inputs": [],
                      "outputs": [[nid, "ref"]], "requirements": [],
                      "constraints": [], "effects": [{"kind": "pure"}],
                      "speculative": False,
                      "hints": {"jacquard_warping": "hard"}})
        provenance["nodes"][nid] = {"source": f"warp[{index}]",
                                     "kind": "shared_warp"}

    woven: dict[str, str] = {}
    fan_of: dict[str, list[str]] = {}
    for index, fans in enumerate(source["shared_warps"], 1):
        for target in fans:
            fan_of.setdefault(target, []).append(f"warp_{index}")

    for event in source["events"]:
        events_out.append({"event_type": event["name"],
                           "facts": event["facts"],
                           "constraints": event["constraints"]})
        provenance["events"][event["name"]] = {
            "source": f"event {event['name']}", "kind": "event"}
        for artifact in event["produces"]:
            if artifact in woven:
                raise SourceError(f"artifact {artifact} is produced more than once")
            woven[artifact] = event["name"]
        if "unravels" in event:
            anchor = event["unravels"]["after"]
            nid = f"unravel_{_node_id(event['name'])}"
            used = set(event["compensation_uses"])
            known = {name for name, _ in event["facts"]}
            if not used <= known:
                raise SourceError(f"unravel uses undeclared facts: {sorted(used - known)}")
            nodes.append({"id": nid, "operation": "jacquard.unravel",
                          "inputs": [[name, kind] for name, kind in event["facts"]
                                     if name in used],
                          "outputs": [["compensation", "ref"]],
                          "requirements": _closure(event["name"], events),
                          "constraints": [], "effects": [{"kind": "pure"}],
                          "speculative": False,
                          "hints": {"jacquard_unravel_after": _node_id(anchor)}})
            provenance["nodes"][nid] = {
                "source": f"event {event['name']} unravel",
                "kind": "unravel", "anchor": _node_id(anchor)}

    for artifact in source["artifacts"]:
        name = artifact["name"]
        if name not in woven:
            if artifact["warping"] == "soft":
                continue
            raise SourceError(f"hard artifact {name} is not produced")
        event_name = woven[name]
        event = events[event_name]
        req = _closure(event_name, events)
        for prior, producer in woven.items():
            if producer == event_name:
                if prior == name:
                    break
                dep = _node_id(prior)
                if dep not in req:
                    req.append(dep)
        for ancestor in _closure(event_name, events):
            if ancestor == event_name:
                continue
            for ancestor_artifact, producer in woven.items():
                if producer == ancestor:
                    dep = _node_id(ancestor_artifact)
                    if dep not in req:
                        req.append(dep)
        req += [warp for warp in fan_of.get(name, []) if warp not in req]
        used = set(artifact["uses"])
        inputs = [[fact, kind] for fact, kind in event["facts"] if fact in used]
        inputs += [[warp, "ref"] for warp in fan_of.get(name, [])]
        nid = _node_id(name)
        nodes.append({"id": nid, "operation": "jacquard.render",
                      "inputs": inputs, "outputs": [[nid, "ref"]],
                      "requirements": req, "constraints": [],
                      "effects": [{"kind": "pure"}], "speculative": False,
                      "hints": {"jacquard_warping": artifact["warping"]}})
        provenance["nodes"][nid] = {
            "source": f"artifact {name}", "kind": "render",
            "event": event_name}

    document = ir.IRDocument.from_canonical({
        "ir_version": ir.IR_VERSION, "schema_version": "1.0.0",
        "compatibility_version": ir.IR_VERSION,
        "namespaces": {"jacquard": NS}, "initial": source["data"],
        "intent": {"goal": "weave_cloth",
                   "requires": [node["id"] for node in nodes
                                if node["operation"] != "jacquard.warp"],
                   "constraints": [], "prefer": []},
        "nodes": nodes, "events": events_out})
    checked = kernel_gate(document)
    provenance["abi_version"] = "1.0.0"
    provenance["document_addr"] = checked.addr
    return AdapterResult(checked, provenance, (), "1.0.0")


def main() -> int:
    source = parse(SOURCE.read_text(encoding="utf-8"))
    result = lower(source)
    print("PROLEPSIS-SCRIPT v1: parsed and lowered through C23")
    print(f"document: {result.document.addr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
