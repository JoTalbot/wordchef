#!/usr/bin/env python3
"""PROLEPSIS-WORKFLOW v1 -> Canonical IR.

An independent source frontend for C23 equivalence testing.  It does not
parse Jacquard and does not call adapter.adapt(): the workflow source has its
own event/artifact vocabulary and constructs the Canonical IR directly.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from . import canonical as ir
from .adapter_abi import AdapterResult, kernel_gate

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "sources" / "release.workflow.json"
NS = "https://prolepsis.dev/ns/jacquard"
TYPE_MAP = {"semver":"semver","date":"date","string":"string","int":"int","list":"list"}

def node_id(name: str) -> str:
    snake = name.lower() if name == name.upper() else re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return re.sub(r"[^a-z0-9_-]", "", snake) or "node"

def closure(name, events):
    seen, stack = [], [name]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.append(cur)
        stack.extend(events[cur].get("requires") or [])
    return list(dict.fromkeys(seen))

def lower(source: dict) -> AdapterResult:
    nodes, events_out = [], []
    events = {e["name"]: e for e in source["events"]}
    provenance = {"source":"sources/release.workflow.json",
                  "source_version":source["source_version"],
                  "events":{}, "nodes":{}}

    for i, warp in enumerate(source["shared_warps"], 1):
        nid = f"warp_{i}"
        nodes.append({"id":nid,"operation":"jacquard.warp","inputs":[],
                      "outputs":[[nid,"ref"]],"requirements":[],"constraints":[],
                      "effects":[{"kind":"pure"}],"speculative":False,
                      "hints":{"jacquard_warping":"hard"}})
        provenance["nodes"][nid] = {"source":f"shared_warps[{i}]","kind":"shared_warp"}

    woven = {}
    fan_of = {}
    for i, warp in enumerate(source["shared_warps"], 1):
        for target in warp["fans"]:
            fan_of.setdefault(target, []).append(f"warp_{i}")

    for e in source["events"]:
        facts = [[name, TYPE_MAP[kind]] for name, kind in e["facts"]]
        events_out.append({"event_type":e["name"],"facts":facts,
                           "constraints":[{"id":c["id"],"expr":c["expr"],"message":c["message"]}
                                          for c in e["constraints"]]})
        provenance["events"][e["name"]]={"source":f"events.{e['name']}","kind":"event"}
        for artifact in e.get("produces",[]):
            if artifact in woven:
                raise ValueError(f"artifact '{artifact}' produced twice")
            woven[artifact] = e["name"]
        if "unravels" in e:
            anchor=e["unravels"]["after"]
            nid=f"unravel_{node_id(e['name'])}"
            nodes.append({"id":nid,"operation":"jacquard.unravel",
                          "inputs":[[f,TYPE_MAP[t]] for f,t in e["facts"]
                                    if f in e.get("compensation_uses",[])],
                          "outputs":[["compensation","ref"]],
                          "requirements":closure(e["name"],events),
                          "constraints":[],"effects":[{"kind":"pure"}],
                          "speculative":False,
                          "hints":{"jacquard_unravel_after":node_id(anchor)}})
            provenance["nodes"][nid]={"source":f"events.{e['name']}.unravels",
                                      "kind":"unravel","anchor":node_id(anchor)}

    for artifact in source["artifacts"]:
        name=artifact["name"]
        if name not in woven:
            if artifact["warping"]=="soft":
                continue
            raise ValueError(f"artifact '{name}' is not produced")
        event_name=woven[name]
        e=events[event_name]
        req=closure(event_name,events)
        # `produces` is an ordered list: earlier outputs of this event are
        # explicit requirements. Outputs from ancestor events are also
        # explicit so canonical R10 can follow the graph. Port names are
        # unique node IDs; different CAS refs must never alias `artifact`.
        for prior_artifact, producer in woven.items():
            if producer == event_name:
                if prior_artifact == name:
                    break
                dep=node_id(prior_artifact)
                if dep not in req:
                    req.append(dep)
        for ancestor in closure(event_name,events):
            if ancestor == event_name:
                continue
            for ancestor_artifact, producer in woven.items():
                if producer == ancestor:
                    dep=node_id(ancestor_artifact)
                    if dep not in req:
                        req.append(dep)
        req += [w for w in fan_of.get(name,[]) if w not in req]
        inputs=[[f,TYPE_MAP[t]] for f,t in e["facts"] if f in artifact["uses"]]
        inputs += [[w, "ref"] for w in fan_of.get(name, [])]
        nid = node_id(name)
        nodes.append({"id":nid,"operation":"jacquard.render",
                      "inputs":inputs,"outputs":[[nid,"ref"]],
                      "requirements":req,"constraints":[],"effects":[{"kind":"pure"}],
                      "speculative":False,
                      "hints":{"jacquard_warping":artifact["warping"]}})
        provenance["nodes"][node_id(name)]={"source":f"artifacts.{name}",
                                            "kind":"render","event":event_name}

    # The workflow vocabulary keeps its registry declaration outside `data`;
    # Jacquard's loader merges yarn constants into the canonical initial map.
    # Lower that source-specific field explicitly instead of dropping it.
    initial = dict(source["data"])
    initial["registry"] = source["doc_registry"]

    document=ir.IRDocument.from_canonical({
        "ir_version":ir.IR_VERSION,"schema_version":"1.0.0",
        "compatibility_version":ir.IR_VERSION,
        "namespaces":{"jacquard":NS},"initial":initial,
        "intent":{"goal":"weave_cloth",
                  "requires":[n["id"] for n in nodes if n["operation"]!="jacquard.warp"],
                  "constraints":[],"prefer":[]},
        "nodes":nodes,"events":events_out})
    checked=kernel_gate(document)
    provenance["abi_version"]="1.0.0"
    provenance["document_addr"]=checked.addr
    return AdapterResult(checked,provenance,(), "1.0.0")

def main():
    source=json.loads(SOURCE.read_text(encoding="utf-8"))
    result=lower(source)
    print("C23 cross-source frontend: workflow source accepted")
    print(f"document: {result.document.addr}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
