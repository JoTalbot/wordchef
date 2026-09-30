# Vendored Prolepsis — local patches

Prolepsis v0.39.0 (WEAVE) is vendored verbatim from
`github.com/JoTalbot/prolepsis` tag `v0.39.0`, except the two minimal
compatibility fixes below. Both restore the behavior the upstream
documentation already promises (`runtime._execute_node` docstring:
*"A handler may raise IRError"*).

## 1. `canonical.IRError` must be an exception

Upstream defines `class IRError:` as a plain dataclass, but `runtime.py`
does `except IRError as e:` around handler calls — which raises
`TypeError: catching classes that do not inherit from BaseException is not
allowed` the moment a handler signals an error. Patch: inherit from
`Exception`.

## 2. `ExecutionRecord.reason` / `.message` compatibility properties

`agent_platform.AgentGateway._run` inspects failed nodes via `f.reason` and
`f.message` when building the structured execution error. Upstream failures
are `ExecutionRecord(node, "failed", IRError(...))` with the detail inside
`.error`. Patch: two read-only properties mapping `reason → error.error_type`
and `message → error.cause` so the gateway reports the real failure cause.

No other behavior is changed. Tests in `tests/prolepsis/` cover both patches.
