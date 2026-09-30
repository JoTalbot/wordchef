"""Default capability handlers used when AgentGateway is constructed without
an explicit handler map. Split out from the CLI shim so the server/platform
code can import them without pulling in argparse/CLI machinery."""
from __future__ import annotations


def generic_handler(node, inputs, ctx):
    """Default handler for weave.artifact / jacquard.{render,warp,unravel}:
    records a small payload CAS entry for each declared output."""
    payload = {
        "operation": node.operation,
        "node": node.id,
        "inputs": dict(inputs),
    }
    return {name: ctx.store.put(payload) for name, _ in node.outputs}


DEFAULT_HANDLERS = (
    ("weave.artifact", generic_handler),
    ("jacquard.render", generic_handler),
    ("jacquard.warp", generic_handler),
    ("jacquard.unravel", generic_handler),
)
