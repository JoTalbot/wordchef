# -*- coding: utf-8 -*-
"""
JACQUARD v0.4 — the pattern language of the WEAVE paradigm
===========================================================
Serialization: YAML (patterns/release.yaml). Schema: patterns/release.schema.json
(JSON Schema draft 2020-12, picked up by editors via yaml-language-server).
Runtime: LOOM.

In 1804 Joseph-Marie Jacquard put a head that reads punched cards onto a
weaving loom: the card described the whole pattern and the loom wove every
repetition itself. The punched card — the first code in history — was
invented for a loom, not for counting. This language returns the favor.

A pattern is a DECLARATION: artifacts (the entire future cloth, each with a
warping level — hard / soft), shared warp blocks (computed once, fanned out),
events (shed dependencies, fact shape, type guards, weft templates,
selvedges) and unraveling events.

Weft templates:
    {{ expression }}                          substitution
    {% for i, item in list %}…{% endfor %}    weft loop (i counts from 1)

Expressions see:
    f                facts of the current event (payload)
    pattern          cloth data (yarn + data merged from YAML)
    fact("NAME")     facts of an already-woven pick
    yarn functions:  human_date, fmt_num, total, listing

A selvedge is an expression the reed checks BEFORE the weft is beaten in:
    check: f.failed == 0

Type guards check payload facts before any computation:
    fact_types: { version: semver, date: date, failed: number }

Security: expressions are parsed with ast and evaluated in a sandbox —
only dict paths, the yarn allowlist and comparisons are permitted.
"""

from __future__ import annotations

import ast
import json
import operator
import re
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "patterns" / "release.schema.json"

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class JacquardError(Exception):
    """A language error: a template or an expression does not weave."""


class PatternError(JacquardError):
    """The pattern is contradictory — the reed refused to dress it."""


def plural(n: int, one: str, many: str | None = None) -> str:
    """1 fan · 2 fans · 1 swatch · 2 swatches."""
    if many is None:
        many = one + ("es" if one.endswith(("s", "x", "ch", "sh")) else "s")
    return f"{n} {one if n == 1 else many}"


# ───────────────────────── yarn (built-in functions) ─────────────────────────

def yarn(fn):
    """Mark a function as available in Jacquard templates and selvedges."""
    fn._yarn = True
    return fn


@yarn
def human_date(iso: str) -> str:
    """'2026-09-27' → '27 Sep 2026'."""
    y, m, d = str(iso).split("-")
    return f"{int(d)} {MONTHS[int(m) - 1]} {y}"


@yarn
def fmt_num(x: float) -> str:
    """125000.0 → '125,000'."""
    return f"{x:,.0f}".replace(",", " ")


@yarn
def total(items: list, field: str):
    """Sum a field over a list: total(files, 'size')."""
    return sum(item[field] for item in items)


@yarn
def listing(items: list, fmt: str, sep: str = "; ") -> str:
    """Render a list on one line: listing(highlights, '{h}')."""
    parts = []
    for item in items:
        parts.append(fmt.format(**item) if isinstance(item, dict) else fmt.format(item))
    return sep.join(parts)


YARN = {name: fn for name, fn in list(globals().items())
        if callable(fn) and getattr(fn, "_yarn", False)}


# ───────────────────────── type guards for facts ─────────────────────────

_SEMVER = re.compile(r"^v?\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")

FACT_TYPES = {
    "date": ("an ISO date YYYY-MM-DD",
             lambda v: isinstance(v, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v))),
    "string": ("a non-empty string",
               lambda v: isinstance(v, str) and bool(v.strip())),
    "number": ("a number",
               lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)),
    "list": ("a non-empty list",
             lambda v: isinstance(v, list) and len(v) > 0),
    "semver": ("a semver tag like v1.4.2",
               lambda v: isinstance(v, str) and bool(_SEMVER.fullmatch(v))),
}


def check_fact_type(kind: str, name: str, value) -> str | None:
    """A type guard: the reed checks the type of a fact before any
    computation or weaving. Returns the violation text, or None."""
    if kind not in FACT_TYPES:
        return f"unknown fact type '{kind}'"
    what, ok = FACT_TYPES[kind]
    if not ok(value):
        return f"fact '{name}' expects {what} (type '{kind}'), got {type(value).__name__}"
    return None


# ───────────────────────── sandboxed expression evaluator ─────────────────────────

_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub,
           ast.Mult: operator.mul, ast.Div: operator.truediv}
_CMPOPS = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
           ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}


def evaluate(expression: str, context: dict):
    """Evaluate a Jacquard expression in the sandbox."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise JacquardError(f"expression syntax '{expression}': {e}")
    return _node(tree.body, context)


def _node(node: ast.AST, ctx: dict):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in ctx:
            raise JacquardError(f"unknown name '{node.id}'")
        return ctx[node.id]
    if isinstance(node, ast.Attribute):
        obj = _node(node.value, ctx)
        if isinstance(obj, dict):
            if node.attr in obj:
                return obj[node.attr]
            raise JacquardError(f"no field '{node.attr}'")
        raise JacquardError("only data fields may be traversed")
    if isinstance(node, ast.Subscript):
        return _node(node.value, ctx)[_node(node.slice, ctx)]
    if isinstance(node, ast.Call):
        fn = _node(node.func, ctx)
        if not callable(fn) or not getattr(fn, "_yarn", False):
            raise JacquardError("only yarn functions may be called (allowlist)")
        if node.keywords:
            raise JacquardError("keyword arguments are not supported")
        return fn(*[_node(a, ctx) for a in node.args])
    if isinstance(node, ast.BoolOp):
        values = [_node(v, ctx) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.UnaryOp):
        v = _node(node.operand, ctx)
        return not v if isinstance(node.op, ast.Not) else -v
    if isinstance(node, ast.BinOp):
        return _BINOPS[type(node.op)](_node(node.left, ctx), _node(node.right, ctx))
    if isinstance(node, ast.Compare):
        left = _node(node.left, ctx)
        for op, comp in zip(node.ops, node.comparators):
            right = _node(comp, ctx)
            if not _CMPOPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_node(e, ctx) for e in node.elts]
    raise JacquardError(f"disallowed expression node: {type(node).__name__}")


# ───────────────────────── template rendering ─────────────────────────

_EXPR = re.compile(r"\{\{\s*(.+?)\s*\}\}")
_FOR = re.compile(r"^\{%\s*for\s+(.+?)\s+in\s+(.+?)\s*%\}(.*)$")
_ENDFOR = "{% endfor %}"


def render(template: str, context: dict) -> str:
    """Weave a template: {{ }} substitutions + {% for %} weft loops."""
    return "\n".join(_lines(template.split("\n"), context))


def _lines(lines: list, ctx: dict) -> list:
    out = []
    i = 0
    while i < len(lines):
        m = _FOR.match(lines[i].strip())
        if m:
            targets = [t.strip() for t in m.group(1).split(",") if t.strip()]
            body = [m.group(3)] if m.group(3) else []
            i += 1
            depth = 1
            while i < len(lines):
                s = lines[i].strip()
                if _FOR.match(s):
                    depth += 1
                elif s == _ENDFOR:
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                body.append(lines[i])
                i += 1
            else:
                raise JacquardError("a for loop without endfor")
            items = evaluate(m.group(2), ctx)
            for number, item in enumerate(items, 1):
                local = dict(ctx)
                if len(targets) == 2:        # {% for i, item in … %}
                    local[targets[0]] = number
                    local[targets[1]] = item
                else:                        # {% for item in … %}
                    local[targets[0]] = item
                out.extend(_lines(body, local))
        else:
            out.append(_substitute(lines[i], ctx))
            i += 1
    return out


def _substitute(line: str, ctx: dict) -> str:
    def repl(m):
        v = evaluate(m.group(1), ctx)
        return "" if v is None else str(v)
    return _EXPR.sub(repl, line)


# ───────────────────────── pattern loading and validation ─────────────────────────

def validate_with_schema(document) -> str:
    """Validate the YAML document against the JSON schema (if available)."""
    try:
        import jsonschema
    except ImportError:
        return "skipped (jsonschema not installed; editors use it via $schema)"
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(document, schema)
    return "validated ✓ (patterns/release.schema.json)"


def load_pattern(path, data: dict | None = None) -> dict:
    """Read a pattern from YAML; `data` replaces the 'data' block
    (one cut — many cloths). The reed validates the pattern on load."""
    try:
        import yaml
    except ImportError:
        raise JacquardError("PyYAML is required: pip install pyyaml")

    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise PatternError("the pattern is empty or not a mapping")
    schema_status = validate_with_schema(document)

    artifacts = {}
    for name, spec in (document.get("artifacts") or {}).items():
        if isinstance(spec, str):                   # short form: title only
            artifacts[name] = {"title": spec, "warping": "hard"}
        else:
            artifacts[name] = {"title": spec["title"],
                               "warping": spec.get("warping", "hard")}

    cloth = {**document.get("yarn", {}), **document.get("data", {})}
    if data:
        cloth.update(data)

    pattern = {
        "data": cloth,
        "artifacts": artifacts,
        "shared": document.get("warping", {}).get("shared", []),
        "events": document.get("events", {}),
        "_schema": schema_status,
        "_file": Path(path).name,
    }
    validate(pattern)
    return pattern


def validate(pattern: dict):
    """Dressing: the reed checks the pattern before the loom touches warp."""
    errors = []
    art = pattern["artifacts"]
    ev = pattern["events"]

    if not art:
        errors.append("no artifacts")
    for name, spec in art.items():
        if not isinstance(spec.get("title"), str) or not spec["title"].strip():
            errors.append(f"artifact '{name}': empty title")
        if spec.get("warping") not in ("hard", "soft"):
            errors.append(f"artifact '{name}': warping must be 'hard' or 'soft'")

    for shared in pattern["shared"]:
        if not str(shared.get("block", "")).strip():
            errors.append("a shared warp block is empty")
        for name in shared.get("into", ()):
            if name not in art:
                errors.append(f"shared block targets unknown artifact '{name}'")

    for name, spec in ev.items():
        for req in spec.get("requires", []):
            if req not in ev:
                errors.append(f"'{name}' requires unknown event '{req}'")
        for key, kind in (spec.get("fact_types") or {}).items():
            if kind not in FACT_TYPES:
                errors.append(f"'{name}': unknown fact type '{kind}' (for '{key}')")
            elif key not in spec.get("facts", ()):
                errors.append(f"'{name}': a type guard for unknown fact '{key}'")
        if "unravels" in spec:
            after = spec["unravels"].get("after")
            if after not in art:
                errors.append(f"'{name}' unravels after unknown artifact '{after}'")
        elif not spec.get("weaves"):
            errors.append(f"'{name}': neither weaves nor unravels")
        else:
            for a in spec["weaves"]:
                if a not in art:
                    errors.append(f"'{name}' weaves unknown artifact '{a}'")
                elif not str(spec["weaves"][a].get("weft", "")).strip():
                    errors.append(f"'{name}' → '{a}': empty weft template")

    if errors:
        raise PatternError("the pattern failed dressing: " + "; ".join(errors))


def stats(pattern: dict) -> dict:
    selvedges = sum(len(e.get("selvedges", ())) for e in pattern["events"].values())
    guards = sum(len(e.get("fact_types", {})) for e in pattern["events"].values())
    unravelers = sum(1 for e in pattern["events"].values() if "unravels" in e)
    soft = sum(1 for a in pattern["artifacts"].values() if a.get("warping") == "soft")
    return {"artifacts": len(pattern["artifacts"]),
            "soft": soft,
            "events": len(pattern["events"]),
            "selvedges": selvedges,
            "guards": guards,
            "unravelers": unravelers}
