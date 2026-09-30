"""Durable Shuttle coordinator state.

Runtime policy only. The checkpoint binds a distributed run to one exact
document and preserves only canonical world records plus worker ownership.
"""
from __future__ import annotations
import hashlib
import os, tempfile
from pathlib import Path
from . import canonical

FORMAT = "prolepsis-shuttle-checkpoint-v2"

class ShuttleCheckpoint:
    def __init__(self, path):
        self.path = Path(path)

    def save(self, document, world, assignments, segments, *, execution_config=None, retry_count=0):
        config = execution_config or {}
        if not isinstance(config, dict):
            raise canonical.CanonicalError("invalid shuttle checkpoint execution configuration")
        if not isinstance(retry_count, int) or retry_count < 0:
            raise canonical.CanonicalError("invalid shuttle checkpoint retry count")
        config_digest = hashlib.sha256(canonical.dumps(config).encode("utf-8")).hexdigest()
        payload = {
            "format": FORMAT,
            "execution_config": config,
            "execution_config_digest": "sha256:" + config_digest,
            "document": document.addr,
            "ir_version": document.ir_version,
            "retry_count": int(retry_count),
            "world": [r.to_canonical() for r in world],
            "assignments": {
                key: {"node": value["node"], "execution_id": value["execution_id"],
                      "status": value["status"], "retries": int(value.get("retries", 0))}
                for key, value in assignments.items()
            },
            "segments": {key: list(value) for key, value in segments.items()},
        }
        payload["checkpoint_digest"] = canonical.digest({key: value for key, value in payload.items() if key != "checkpoint_digest"})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".shuttle-", dir=self.path.parent)
        tmp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(canonical.dumps(payload))
                handle.flush(); os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            directory = os.open(self.path.parent, os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            tmp.unlink(missing_ok=True)
        return payload

    def load(self, document, *, execution_config=None):
        try:
            payload = canonical.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise canonical.CanonicalError("shuttle checkpoint not found") from exc
        except Exception as exc:
            raise canonical.CanonicalError("invalid shuttle checkpoint") from exc
        if payload.get("format") != FORMAT:
            raise canonical.CanonicalError("unsupported shuttle checkpoint format")
        checkpoint_digest = payload.get("checkpoint_digest")
        if not isinstance(checkpoint_digest, str):
            raise canonical.CanonicalError("shuttle checkpoint digest missing")
        expected_checkpoint_digest = canonical.digest({key: value for key, value in payload.items() if key != "checkpoint_digest"})
        if checkpoint_digest != expected_checkpoint_digest:
            raise canonical.CanonicalError("shuttle checkpoint integrity failure")
        if payload.get("document") != document.addr:
            raise canonical.CanonicalError("shuttle checkpoint document mismatch")
        if payload.get("ir_version") != document.ir_version:
            raise canonical.CanonicalError("shuttle checkpoint IR mismatch")
        config = execution_config or {}
        expected_config_digest = "sha256:" + hashlib.sha256(canonical.dumps(config).encode("utf-8")).hexdigest()
        stored_config = payload.get("execution_config")
        stored_config_digest = payload.get("execution_config_digest")
        if not isinstance(stored_config, dict) or not isinstance(stored_config_digest, str):
            raise canonical.CanonicalError("invalid shuttle checkpoint execution configuration")
        actual_config_digest = "sha256:" + hashlib.sha256(canonical.dumps(stored_config).encode("utf-8")).hexdigest()
        if stored_config_digest != actual_config_digest:
            raise canonical.CanonicalError("shuttle checkpoint execution configuration integrity failure")
        if stored_config_digest != expected_config_digest:
            raise canonical.CanonicalError("shuttle checkpoint execution configuration mismatch")
        world_records = payload.get("world", [])
        world = rt_log(world_records)
        assignments = payload.get("assignments", {})
        segments = payload.get("segments", {})
        retry_count = payload.get("retry_count", 0)
        if not isinstance(retry_count, int) or retry_count < 0:
            raise canonical.CanonicalError("invalid shuttle checkpoint retry count")
        if not isinstance(assignments, dict) or not isinstance(segments, dict):
            raise canonical.CanonicalError("invalid shuttle checkpoint maps")
        for worker_key, assignment in assignments.items():
            if not isinstance(worker_key, str) or not worker_key:
                raise canonical.CanonicalError("invalid shuttle checkpoint worker key")
            if not isinstance(assignment, dict):
                raise canonical.CanonicalError("invalid shuttle checkpoint assignment")
            if not isinstance(assignment.get("node"), str) or not assignment["node"]:
                raise canonical.CanonicalError("invalid shuttle checkpoint assignment node")
            if not isinstance(assignment.get("execution_id"), str) or not assignment["execution_id"]:
                raise canonical.CanonicalError("invalid shuttle checkpoint assignment execution id")
            if not isinstance(assignment.get("status"), str) or not assignment["status"]:
                raise canonical.CanonicalError("invalid shuttle checkpoint assignment status")
            retries = assignment.get("retries", 0)
            if not isinstance(retries, int) or retries < 0:
                raise canonical.CanonicalError("invalid shuttle checkpoint assignment retries")
        for worker_key, records in segments.items():
            if not isinstance(worker_key, str) or not worker_key:
                raise canonical.CanonicalError("invalid shuttle checkpoint segment worker key")
            if not isinstance(records, list):
                raise canonical.CanonicalError("invalid shuttle checkpoint segment")
            try:
                rt_log(records)
            except Exception as exc:
                raise canonical.CanonicalError("invalid shuttle checkpoint segments") from exc
        return world, assignments, segments, retry_count

    def exists(self):
        return self.path.is_file()

def rt_log(records):
    from . import runtime
    return runtime.log_from_canonical(records)
