#!/usr/bin/env python3
"""Unified execution backends for the Canonical WEAVE runtime.

The backend layer is policy only. Canonical IR remains the semantic center:
every backend accepts the same IR document and canonical world, and returns
an execution report without introducing source-language semantics.

Backends:
  reference     runtime.execute() in one Python process/thread pool.
  distributed   shuttle.Coordinator with real worker processes.
  rust-fold     second-runtime/canond folds an already-produced canonical log;
                 it is a verifier/fold backend, not a handler executor.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from concurrent.futures import ThreadPoolExecutor
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from . import canonical
from . import runtime
from . import shuttle


@dataclass(frozen=True)
class ExecutionReport:
    backend: str
    document: canonical.IRDocument
    digest: str
    log: tuple
    state: dict | None
    metrics: dict
    verified: bool = False
    verification_digest: str | None = None
    verification_backend: str | None = None

    @property
    def canonical_log(self) -> list[dict]:
        return [record.to_canonical() for record in self.log]


@dataclass(frozen=True)
class BackendCapabilities:
    """Declarative policy contract advertised by one execution backend."""
    name: str
    features: frozenset[str]

    def supports(self, *required: str) -> bool:
        return set(required).issubset(self.features)


_REFERENCE_CAPABILITIES = BackendCapabilities(
    "reference",
    frozenset({"canonical-handler", "custom-handlers", "parallel",
               "speculation", "promotion", "unravel"}),
)
_DISTRIBUTED_CAPABILITIES = BackendCapabilities(
    "distributed",
    frozenset({"canonical-handler", "parallel", "distribution"}),
)
_RUST_FOLD_CAPABILITIES = BackendCapabilities(
    "rust-fold",
    frozenset({"canonical-fold", "verification"}),
)


@dataclass(frozen=True)
class BackendRegistration:
    """Public registration record for one execution backend."""
    capabilities: BackendCapabilities
    factory: type
    priority: int = 100

    def __post_init__(self):
        if not self.capabilities.name:
            raise ValueError("backend name must not be empty")
        if not callable(self.factory):
            raise TypeError("backend factory must be callable")


_BACKENDS: dict[str, BackendRegistration] = {}


def register_backend(name: str, factory, capabilities: BackendCapabilities,
                     *, priority: int = 100, replace: bool = False) -> None:
    """Register a backend factory and declarative capabilities.

    Registration is the only extension point required by the resolver:
    adding a backend does not require editing resolver policy.
    """
    if name != capabilities.name:
        raise ValueError(
            f"backend registration name {name!r} does not match "
            f"capability name {capabilities.name!r}")
    if not name or name == "auto":
        raise ValueError(f"invalid execution backend name: {name!r}")
    if not callable(factory):
        raise TypeError("backend factory must be callable")
    if not isinstance(priority, int):
        raise TypeError("backend priority must be an int")
    if name in _BACKENDS and not replace:
        raise canonical.CanonicalError(
            f"execution backend already registered: {name!r}")
    _BACKENDS[name] = BackendRegistration(
        capabilities=capabilities, factory=factory, priority=priority)


def backends() -> tuple[BackendRegistration, ...]:
    """Return immutable backend registrations in deterministic priority order."""
    return tuple(sorted(
        _BACKENDS.values(),
        key=lambda item: (item.priority, item.capabilities.name),
    ))


def capabilities(name: str) -> BackendCapabilities:
    """Return the immutable capability contract for a named backend."""
    try:
        return _BACKENDS[name].capabilities
    except KeyError as exc:
        raise canonical.CanonicalError(
            f"unknown execution backend: {name!r}") from exc


def _request_requirements(*, handlers=None, granted=(), workers=1,
                          predictions=None, branches=(), unravel=None):
    """Derive runtime policy requirements without changing Canonical IR."""
    required = {"parallel"} if workers > 1 else set()
    required.add("canonical-handler" if handlers is None else "custom-handlers")
    if predictions:
        required.add("speculation")
    if branches:
        required.add("promotion")
    if unravel:
        required.add("unravel")
    return frozenset(required)


@dataclass(frozen=True)
class ExecutionStage:
    """One immutable stage in a dependency-aware execution plan."""
    role: str
    backend: str
    required: frozenset[str]
    id: str = ""
    depends_on: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.id:
            object.__setattr__(self, "id", self.role)
        if not self.id:
            raise ValueError("execution stage id must not be empty")
        if self.id in self.depends_on:
            raise ValueError("execution stage cannot depend on itself")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("execution stage dependencies must be unique")
        if not self.capabilities.supports(*sorted(self.required)):
            missing = sorted(set(self.required) - self.capabilities.features)
            raise ValueError(
                f"execution stage {self.id!r} backend {self.backend!r} "
                f"lacks required capabilities: {missing}"
            )

    @property
    def capabilities(self) -> BackendCapabilities:
        return capabilities(self.backend)


@dataclass(frozen=True)
class ExecutionPlan:
    """Immutable policy plan resolved before backend construction.

    ``backend`` and ``required`` remain the compatibility view for callers
    that only need one backend. ``stages`` is the extensible representation
    for pipelines such as execute -> independently verify.
    """
    backend: str
    required: frozenset[str]
    stages: tuple[ExecutionStage, ...] = ()

    def __post_init__(self):
        if not self.stages:
            object.__setattr__(
                self, "stages",
                (ExecutionStage("execute", self.backend, self.required),),
            )
        elif self.stages[0].backend != self.backend:
            raise ValueError("execution plan backend must match first stage")

        ids = [stage.id for stage in self.stages]
        if len(set(ids)) != len(ids):
            raise ValueError("execution stage ids must be unique")
        known = set(ids)
        for stage in self.stages:
            missing = set(stage.depends_on) - known
            if missing:
                raise ValueError(
                    f"execution stage {stage.id!r} has unknown dependencies: "
                    f"{sorted(missing)}"
                )

        visiting = set()
        visited = set()
        by_id = {stage.id: stage for stage in self.stages}

        def visit(stage_id):
            if stage_id in visiting:
                raise ValueError("execution stage dependency graph contains a cycle")
            if stage_id in visited:
                return
            visiting.add(stage_id)
            for dependency in by_id[stage_id].depends_on:
                visit(dependency)
            visiting.remove(stage_id)
            visited.add(stage_id)

        for stage_id in ids:
            visit(stage_id)

    def ready_stages(self, completed=()):
        """Return stages whose dependencies are satisfied in plan order."""
        completed = frozenset(completed)
        return tuple(
            stage for stage in self.stages
            if stage.id not in completed
            and set(stage.depends_on).issubset(completed)
        )

    def schedule(self, completed=()):
        """Return deterministic dependency waves for this plan.

        Each wave contains every currently-ready stage in plan order. The
        scheduler is deliberately separate from execution: concurrency policy
        can consume these waves without changing dependency semantics.
        """
        completed = set(completed)
        known = {stage.id for stage in self.stages}
        unknown = completed - known
        if unknown:
            raise ValueError(
                f"completed execution stages are unknown: {sorted(unknown)}")
        waves = []
        while len(completed) < len(self.stages):
            ready = self.ready_stages(completed)
            if not ready:
                raise ValueError("execution stage graph cannot make progress")
            wave = tuple(stage.id for stage in ready)
            waves.append(wave)
            completed.update(wave)
        return tuple(waves)

    @property
    def capabilities(self) -> BackendCapabilities:
        return capabilities(self.backend)


def schedule_execution(plan: ExecutionPlan, completed=()):
    """Return the immutable deterministic execution waves for a plan."""
    if not isinstance(plan, ExecutionPlan):
        raise TypeError("schedule_execution expects an ExecutionPlan")
    return plan.schedule(completed)



@dataclass(frozen=True)
class StageResult:
    """Immutable stage output envelope derived from canonical execution."""
    stage_id: str
    backend: str
    digest: str
    artifacts: tuple[dict, ...] = ()
    log: tuple = ()

    def __post_init__(self):
        if not self.stage_id or not self.backend:
            raise ValueError("stage result identity must not be empty")
        if (
            not isinstance(self.digest, str)
            or len(self.digest) != 71
            or not self.digest.startswith("sha256:")
            or any(ch not in "0123456789abcdef" for ch in self.digest[7:])
        ):
            raise ValueError("stage result digest must be a sha256 content address")
        seen = set()
        for artifact in self.artifacts:
            if canonical.check_value(artifact, "ref"):
                raise ValueError("stage result artifacts must be canonical refs")
            ref = artifact["$cas"]
            if ref in seen:
                raise ValueError("stage result artifacts must be unique")
            seen.add(ref)

    @classmethod
    def from_report(cls, stage: ExecutionStage, report: ExecutionReport,
                    artifacts=()):
        """Wrap a report without guessing stage ownership of its log artifacts.

        Reports may contain cumulative execution logs, so artifact ownership must
        be supplied explicitly when a report is used as a stage result.
        """
        if not isinstance(report, ExecutionReport):
            raise TypeError("stage result report must be an ExecutionReport")
        artifacts = tuple(artifacts)
        return cls(stage.id, report.backend, report.digest,
                   artifacts, tuple(report.log))

    def to_canonical(self) -> dict:
        return {"stage": self.stage_id, "backend": self.backend,
                "digest": self.digest, "artifacts": list(self.artifacts)}


@dataclass(frozen=True)
class StageFailure:
    """Deterministic failure envelope for one execution stage."""
    stage_id: str
    error_type: str
    message: str
    blocked_by: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.stage_id or not self.error_type:
            raise ValueError("stage failure identity must not be empty")
        if any(not item for item in self.blocked_by):
            raise ValueError("stage failure blockers must not be empty")

    @classmethod
    def from_exception(cls, stage, exc):
        return cls(stage.id, type(exc).__name__, str(exc))

    @classmethod
    def blocked(cls, stage, blockers):
        return cls(stage.id, "DependencyBlocked",
                   "dependency failure blocked stage", tuple(blockers))


@dataclass(frozen=True)
class StageContext:
    """Read-only inputs exposed to a stage from completed dependencies."""
    stage: ExecutionStage
    dependencies: tuple[StageResult, ...] = ()

    def __post_init__(self):
        expected = tuple(self.stage.depends_on)
        actual = tuple(item.stage_id for item in self.dependencies)
        if actual != expected:
            raise ValueError(
                f"stage context dependencies for {self.stage.id!r} must be "
                f"{expected}, got {actual}")

    @property
    def artifacts(self) -> tuple[dict, ...]:
        return tuple(a for dependency in self.dependencies for a in dependency.artifacts)

    @property
    def artifact_provenance(self) -> tuple[tuple[str, dict], ...]:
        """Return dependency artifact refs with their producing stage identity."""
        return tuple(
            (dependency.stage_id, artifact)
            for dependency in self.dependencies
            for artifact in dependency.artifacts
        )


@dataclass(frozen=True)
class StageExecution:
    """Deterministic result record for one scheduled execution stage."""
    stage: ExecutionStage
    result: StageResult | StageFailure

    def __post_init__(self):
        if self.result.stage_id != self.stage.id:
            raise ValueError(
                f"stage execution {self.stage.id!r} has result for "
                f"{self.result.stage_id!r}"
            )
        if (
            isinstance(self.result, StageResult)
            and self.result.backend != self.stage.backend
        ):
            raise ValueError(
                f"stage execution {self.stage.id!r} uses backend "
                f"{self.result.backend!r}, expected {self.stage.backend!r}"
            )


def execute_waves(plan: ExecutionPlan, runner, *, completed=(),
                  completed_results=None, failures=(),
                  failure_results=None, max_workers=None) -> tuple[StageExecution, ...]:
    """Execute dependency waves with deterministic failure propagation."""
    if not isinstance(plan, ExecutionPlan):
        raise TypeError("execute_waves expects an ExecutionPlan")
    if not callable(runner):
        raise TypeError("execute_waves runner must be callable")
    if max_workers is not None and (not isinstance(max_workers, int) or max_workers < 1):
        raise ValueError("max_workers must be a positive int or None")
    stage_by_id = {stage.id: stage for stage in plan.stages}
    completed_ids = set(completed)
    unknown = completed_ids - set(stage_by_id)
    if unknown:
        raise ValueError(f"completed execution stages are unknown: {sorted(unknown)}")
    if completed_results is None:
        completed_results = {}
    if not hasattr(completed_results, "items"):
        raise TypeError("completed_results must be a mapping of stage id to StageResult")
    results_by_id = dict(completed_results)
    unknown_results = set(results_by_id) - set(stage_by_id)
    if unknown_results:
        raise ValueError(f"completed execution results are unknown: {sorted(unknown_results)}")
    uncompleted_results = set(results_by_id) - completed_ids
    if uncompleted_results:
        raise ValueError(f"execution results supplied for non-completed stages: {sorted(uncompleted_results)}")
    missing_results = completed_ids - set(results_by_id)
    required_completed_results = {
        dependency for stage in plan.stages if stage.id not in completed_ids
        for dependency in stage.depends_on if dependency in completed_ids
    }
    missing_results &= required_completed_results
    if missing_results:
        raise ValueError(f"completed execution stages require results: {sorted(missing_results)}")
    for stage_id, result in results_by_id.items():
        if not isinstance(result, StageResult):
            raise TypeError(f"completed execution result {stage_id!r} must be a StageResult")
        if result.stage_id != stage_id:
            raise ValueError(f"completed execution result {stage_id!r} has stage id {result.stage_id!r}")
        if result.backend != stage_by_id[stage_id].backend:
            raise ValueError(
                f"completed execution result {stage_id!r} uses backend "
                f"{result.backend!r}, expected {stage_by_id[stage_id].backend!r}"
            )

    if failure_results is None:
        failure_results = {}
    if not hasattr(failure_results, "items"):
        raise TypeError("failure_results must be a mapping of stage id to StageFailure")
    persisted_failures = set(failures)
    if persisted_failures & completed_ids:
        raise ValueError("a stage cannot be both completed and failed")
    unknown_failures = persisted_failures - set(stage_by_id)
    if unknown_failures:
        raise ValueError(f"failed execution stages are unknown: {sorted(unknown_failures)}")
    unknown_failure_results = set(failure_results) - set(stage_by_id)
    if unknown_failure_results:
        raise ValueError(f"failed execution results are unknown: {sorted(unknown_failure_results)}")
    unpersisted_results = set(failure_results) - persisted_failures
    if unpersisted_results:
        raise ValueError(f"failure results supplied for non-failed stages: {sorted(unpersisted_results)}")
    for stage_id, failure in failure_results.items():
        if not isinstance(failure, StageFailure):
            raise TypeError(f"failed execution result {stage_id!r} must be a StageFailure")
        if failure.stage_id != stage_id:
            raise ValueError(f"failed execution result {stage_id!r} has stage id {failure.stage_id!r}")
    output_by_id = {}
    pending = set(stage_by_id) - completed_ids - persisted_failures
    failures = dict(failure_results)
    for stage_id in sorted(persisted_failures):
        output_by_id[stage_id] = StageExecution(stage_by_id[stage_id], failures[stage_id])
    while pending:
        blocked = []
        ready = []
        for stage in plan.stages:
            if stage.id not in pending:
                continue
            dependency_failures = tuple(d for d in stage.depends_on if d in failures)
            if dependency_failures:
                blocked.append((stage, dependency_failures))
            elif set(stage.depends_on).issubset(completed_ids):
                ready.append(stage)
        for stage, blockers in blocked:
            failure = StageFailure.blocked(stage, blockers)
            failures[stage.id] = failure
            pending.remove(stage.id)
            output_by_id[stage.id] = StageExecution(stage, failure)
        if blocked:
            continue
        if not ready:
            raise ValueError("execution stage graph cannot make progress")
        workers = max_workers or len(ready)
        with ThreadPoolExecutor(max_workers=min(workers, len(ready))) as pool:
            contexts = tuple(StageContext(stage, tuple(results_by_id[d] for d in stage.depends_on)) for stage in ready)
            futures = [pool.submit(runner, context) for context in contexts]
            wave_results = []
            for stage, future in zip(ready, futures):
                try:
                    value = future.result()
                    if isinstance(value, ExecutionReport):
                        value = StageResult.from_report(stage, value)
                    if not isinstance(value, StageResult):
                        raise TypeError(f"stage {stage.id!r} runner must return StageResult or ExecutionReport")
                    if value.stage_id != stage.id:
                        raise ValueError(f"stage {stage.id!r} returned result for {value.stage_id!r}")
                    if value.backend != stage.backend:
                        raise ValueError(
                            f"stage {stage.id!r} returned backend {value.backend!r}, "
                            f"expected {stage.backend!r}"
                        )
                except Exception as exc:
                    value = StageFailure.from_exception(stage, exc)
                    failures[stage.id] = value
                else:
                    results_by_id[stage.id] = value
                    completed_ids.add(stage.id)
                wave_results.append(StageExecution(stage, value))
        for execution in wave_results:
            pending.remove(execution.stage.id)
            output_by_id[execution.stage.id] = execution
    return tuple(output_by_id[stage.id] for stage in plan.stages if stage.id in output_by_id)

def _make_plan(backend_name: str, required: frozenset[str],
               *, verify: bool = False) -> ExecutionPlan:
    stages = [ExecutionStage("execute", backend_name, required)]
    if verify:
        verification_required = frozenset({"canonical-fold", "verification"})
        verifier = capabilities("rust-fold")
        if not verifier.supports(*sorted(verification_required)):
            raise canonical.CanonicalError("no verification backend satisfies canonical fold requirements")
        stages.append(
            ExecutionStage(
                "verify", "rust-fold", verification_required,
                id="verify", depends_on=("execute",),
            )
        )
    return ExecutionPlan(backend_name, required, tuple(stages))

def plan_execution(*, handlers=None, granted=(), workers=1,
                   predictions=None, branches=(), unravel=None,
                   preferred=None, verify=False) -> ExecutionPlan:
    """Derive requirements and build an immutable execution stage plan."""
    if preferred == "rust-fold":
        if handlers is not None or granted or predictions or branches or unravel:
            raise canonical.CanonicalError("rust-fold accepts only a canonical document and log")
        if verify:
            raise canonical.CanonicalError("rust-fold cannot be followed by its own verification stage")
        return _make_plan("rust-fold", frozenset())

    required = _request_requirements(
        handlers=handlers, granted=granted, workers=workers,
        predictions=predictions, branches=branches, unravel=unravel)
    if preferred is not None and preferred != "auto":
        spec = capabilities(preferred)
        missing = required - spec.features
        if missing:
            raise canonical.CanonicalError(
                f"backend {preferred!r} lacks required capabilities: {sorted(missing)}")
        return _make_plan(preferred, required, verify=verify)

    for registration in backends():
        if registration.capabilities.supports(*sorted(required)):
            return _make_plan(registration.capabilities.name, required,
                              verify=verify)
    raise canonical.CanonicalError(
        f"no execution backend satisfies required capabilities: {sorted(required)}")

def resolve_backend(*, handlers=None, granted=(), workers=1,
                    predictions=None, branches=(), unravel=None,
                    preferred=None, verify=False) -> str:
    """Backward-compatible backend-name view of the execution planner."""
    return plan_execution(
        handlers=handlers, granted=granted, workers=workers,
        predictions=predictions, branches=branches, unravel=unravel,
        preferred=preferred, verify=verify).backend


class ExecutionBackend(Protocol):
    name: str

    def run(self, document: canonical.IRDocument, *,
            log=(), handlers=None, granted=(), workers=1,
            store: runtime.ArtifactStore | None = None,
            predictions=None, branches=(), unravel=None) -> ExecutionReport:
        ...


def _artifact_handler(node, inputs, ctx):
    return {
        name: ctx.store.put({"node": node.id, "inputs": dict(inputs)})
        for name, _ in node.outputs
    }


class ReferenceBackend:
    name = "reference"

    def run(self, document, *, log=(), handlers=None, granted=(),
            workers=1, store=None, predictions=None, branches=(),
            unravel=None):
        if handlers is None:
            handlers = {"weave.artifact": _artifact_handler}
        result = runtime.execute(
            document, handlers, granted=granted, log=log, store=store,
            workers=workers, predictions=predictions, branches=branches,
            unravel=unravel,
        )
        return ExecutionReport(
            self.name, document, result.digest, tuple(result.log), result.state,
            {"waves": result.waves, "executed": result.executed,
             "failures": len(result.failures),
             "speculation": dict(result.speculation)},
        )


class DistributedBackend:
    """C32 shuttle executor using real OS worker processes."""

    name = "distributed"

    def run(self, document, *, log=(), handlers=None, granted=(),
            workers=1, store=None, predictions=None, branches=(),
            unravel=None):
        if handlers is not None and set(handlers) != {"weave.artifact"}:
            raise canonical.CanonicalError(
                "distributed backend accepts only the canonical "
                "weave.artifact handler")
        if predictions or branches or unravel:
            raise canonical.CanonicalError(
                "distributed backend does not yet expose speculative or "
                "unravel policy; use the reference backend")
        coordinator = shuttle.Coordinator(document, max(1, workers), granted)
        try:
            report = coordinator.run(list(log))
        finally:
            for worker in coordinator.workers:
                worker.close()
        records = runtime.log_from_canonical(report["log"])
        return ExecutionReport(
            self.name, document, report["digest"], tuple(records),
            report["state"],
            {"waves": report["waves"], "workers": report["workers"],
             "fused": report["fused"]},
        )


class RustFoldBackend:
    """Independent Rust fold verifier for an existing canonical log.

    It deliberately does not execute handlers. It proves that the canonical
    document + canonical log folds to the same state digest independently.
    """

    name = "rust-fold"

    def __init__(self, binary: str | Path = "second-runtime/canond"):
        self.binary = Path(binary)

    def run(self, document, *, log=(), handlers=None, granted=(),
            workers=1, store=None, predictions=None, branches=(),
            unravel=None):
        if handlers is not None or granted or predictions or branches or unravel:
            raise canonical.CanonicalError(
                "rust-fold accepts only a canonical document and log")
        if not self.binary.exists():
            raise canonical.CanonicalError(
                f"Rust fold binary not found: {self.binary}")
        records = list(log)
        vector = {
            "name": "backend-fold", "ir_version": canonical.IR_VERSION,
            "description": "executor backend canonical fold",
            "document": document.to_canonical(),
            "log": [record.to_canonical() for record in records],
            "expect": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vector.json"
            path.write_text(canonical.dumps(vector), encoding="utf-8")
            proc = subprocess.run(
                [str(self.binary), str(path)],
                capture_output=True, text=True, check=False)
        output = proc.stdout.strip()
        if proc.returncode != 0 or not output.startswith("DIGEST "):
            raise canonical.CanonicalError(
                f"rust-fold rejected canonical execution: "
                f"{proc.stderr.strip() or output}")
        return ExecutionReport(
            self.name, document, output[len("DIGEST "):], tuple(records), None,
            {"records": len(records)},
        )


# Built-ins are registered once, after their concrete classes exist.
register_backend(
    "reference", ReferenceBackend, _REFERENCE_CAPABILITIES, priority=200)
register_backend(
    "distributed", DistributedBackend, _DISTRIBUTED_CAPABILITIES, priority=100)
register_backend(
    "rust-fold", RustFoldBackend, _RUST_FOLD_CAPABILITIES, priority=1000)


def backend(name: str, **kwargs):
    """Construct a named execution backend from the extensible registry."""
    try:
        registration = _BACKENDS[name]
    except KeyError as exc:
        raise canonical.CanonicalError(
            f"unknown execution backend: {name!r}") from exc
    return registration.factory(**kwargs)


def execute(document: canonical.IRDocument, *, backend_name="reference",
            **kwargs) -> ExecutionReport:
    """Run one Canonical IR document through the selected policy backend.

    backend_name="auto" resolves against declarative capabilities. The
    default remains "reference" for backwards compatibility.
    """
    verify = bool(kwargs.get("verify", False))
    run_kwargs = {
        key: value for key, value in kwargs.items()
        if key not in {"binary", "verify"}
    }
    plan = plan_execution(
        handlers=run_kwargs.get("handlers"),
        granted=run_kwargs.get("granted", ()),
        workers=run_kwargs.get("workers", 1),
        predictions=run_kwargs.get("predictions"),
        branches=run_kwargs.get("branches", ()),
        unravel=run_kwargs.get("unravel"),
        preferred=backend_name,
        verify=verify,
    )
    report = None
    for stage in plan.stages:
        if stage.role == "execute":
            report = backend(stage.backend, **{
                key: value for key, value in kwargs.items()
                if key == "binary" and stage.backend == "rust-fold"
            }).run(document, **run_kwargs)
        elif stage.role == "verify":
            assert report is not None
            verification = backend(stage.backend, **{
                key: value for key, value in kwargs.items() if key == "binary"
            }).run(document, log=report.log)
            if verification.digest != report.digest:
                raise canonical.CanonicalError(
                    f"execution verification digest mismatch: "
                    f"executor={report.digest}, verifier={verification.digest}")
            report = replace(
                report,
                verified=True,
                verification_digest=verification.digest,
                verification_backend=verification.backend,
                metrics={**report.metrics, "verified": True,
                         "verification_backend": verification.backend},
            )
        else:
            raise canonical.CanonicalError(
                f"unknown execution stage role: {stage.role!r}")
    assert report is not None
    return report

def self_check() -> None:
    document = canonical.IRDocument.from_canonical({
        "ir_version": canonical.IR_VERSION, "schema_version": "1.0.0",
        "compatibility_version": canonical.IR_VERSION, "namespaces": {},
        "initial": {}, "intent": {"goal": "backend-check", "requires": ["START"]},
        "nodes": [{
            "id": "a", "operation": "weave.artifact", "inputs": [],
            "outputs": [["a_out", "ref"]], "requirements": ["START"],
            "effects": [{"kind": "pure"}],
        }],
        "events": [{"event_type": "START", "facts": [["date", "date"]]}],
    })
    world = [canonical.Event("s1", "START", 1, "backend-test",
                             {"date": "2026-11-08"})]
    reference = execute(document, backend_name="reference", log=world)
    distributed = execute(document, backend_name="distributed", log=world,
                          workers=2)
    assert reference.digest == distributed.digest
    assert reference.state == distributed.state
    rust_binary = Path("second-runtime/canond")
    if rust_binary.exists():
        rust = execute(document, backend_name="rust-fold",
                       log=reference.log, binary=rust_binary)
        assert rust.digest == reference.digest
    print("unified execution backends: PASS")
    print("reference + distributed: ONE canonical digest")
    if rust_binary.exists():
        print("rust fold verifier: SAME canonical digest")


if __name__ == "__main__":
    self_check()
