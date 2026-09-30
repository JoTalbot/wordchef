"""Durable Executor ABI checkpoints. Runtime policy only."""
from __future__ import annotations
import os
import tempfile
from pathlib import Path
from . import canonical
from .executor import ExecutionPlan, StageFailure, StageResult

FORMAT = "prolepsis-executor-checkpoint-v1"

def plan_digest(plan: ExecutionPlan) -> str:
    value = {"backend": plan.backend, "required": sorted(plan.required),
             "stages": [{"id": s.id, "role": s.role, "backend": s.backend,
                         "required": sorted(s.required), "depends_on": list(s.depends_on)}
                        for s in plan.stages]}
    return canonical.digest(value)

class ExecutionCheckpoint:
    def __init__(self, path):
        self.path = Path(path)

    def save(self, document, plan, executions):
        completed, failures = {}, {}
        for item in executions:
            value = item.result
            if isinstance(value, StageResult):
                completed[item.stage.id] = value.to_canonical()
            elif isinstance(value, StageFailure):
                failures[item.stage.id] = {
                    "stage": value.stage_id, "error_type": value.error_type,
                    "message": value.message, "blocked_by": list(value.blocked_by)}
            else:
                raise TypeError("invalid stage result")
        payload = {"format": FORMAT, "document": document.addr,
                   "ir_version": document.ir_version, "plan": plan_digest(plan),
                   "completed": completed, "failures": failures}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".checkpoint-", dir=self.path.parent)
        tmp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(canonical.dumps(payload))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            directory = os.open(self.path.parent, os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            tmp.unlink(missing_ok=True)
        return payload

    def load(self, document, plan):
        try:
            payload = canonical.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise canonical.CanonicalError("execution checkpoint not found") from exc
        except Exception as exc:
            raise canonical.CanonicalError("invalid execution checkpoint") from exc
        if payload.get("format") != FORMAT:
            raise canonical.CanonicalError("unsupported execution checkpoint format")
        if payload.get("document") != document.addr:
            raise canonical.CanonicalError("execution checkpoint document mismatch")
        if payload.get("ir_version") != document.ir_version:
            raise canonical.CanonicalError("execution checkpoint IR version mismatch")
        if payload.get("plan") != plan_digest(plan):
            raise canonical.CanonicalError("execution checkpoint plan mismatch")
        by_id = {s.id: s for s in plan.stages}
        completed, failures = {}, {}
        for sid, value in payload.get("completed", {}).items():
            if sid not in by_id or value.get("stage") != sid:
                raise canonical.CanonicalError("checkpoint completed stage identity mismatch")
            result = StageResult(sid, value["backend"], value["digest"],
                                 tuple(value.get("artifacts", ())))
            if result.backend != by_id[sid].backend:
                raise canonical.CanonicalError("checkpoint backend mismatch")
            completed[sid] = result
        for sid, value in payload.get("failures", {}).items():
            if sid not in by_id or value.get("stage") != sid:
                raise canonical.CanonicalError("checkpoint failed stage identity mismatch")
            failures[sid] = StageFailure(sid, value["error_type"], value["message"],
                                         tuple(value.get("blocked_by", ())))
        if set(completed) & set(failures):
            raise canonical.CanonicalError("checkpoint stage both completed and failed")
        return completed, failures

    def exists(self):
        return self.path.is_file()
