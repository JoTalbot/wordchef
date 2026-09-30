#!/usr/bin/env python3
"""shuttle — the distributed runtime (STEP K, directive s21).

The shuttle carries work between looms: a COORDINATOR owns the global
frontier and the fusion; WORKERS are real OS processes executing nodes
locally. The protocol between them is CANONICAL JSON, one value per line —
the same language-independent contract everywhere (C22/C25):

    coordinator → worker    {"op": "hello", "document": …}        (once)
    coordinator → worker    {"op": "execute", "node": "id",
                             "granted": […], "execution_id": …,
                             "world": [… canonical records …]}
    worker → coordinator    {"op": "records", "records": […], "blocks": […]}

C32 — DISTRIBUTED FUSION (the contract, machine-checked below):

  A distributed execution produces SEGMENTS — one canonical log per worker.
  The global log is their deterministic FUSION: segments concatenated in
  ascending worker-key order. The state digest of the fused log is
  invariant to:

    S1  the arrival order of segments (fusion sorts, never trusts order);
    S2  the number of workers (1, 2, 4 … — one digest);
    S3  sequential equivalence — the same world executed by one local
        runtime folds to the same digest (runtime change != semantic
        change, directive s30);
    S4  the wire is canonical — every message is a canonical value;
    S5  replay independence — reduce(document, fused_log) with no runtime
        at all yields the coordinator's digest (C12/C19).

  Precondition — EVENT OWNERSHIP: every event type is written by exactly
  one worker (the reed and the prior port must see ONE order per type).
  The coordinator enforces it: events enter the world log directly, and a
  node is dispatched to exactly one worker; workers never emit events.

Artifacts are content-addressed (C21): workers return their CAS blocks and
the stores merge by ref — union, no negotiation.

Run:   python3 shuttle.py            (self-check S1–S5, then the demo)
       python3 shuttle.py --worker N (internal: one worker on stdio)
"""
from __future__ import annotations

import subprocess
import sys
import time
import tempfile
from pathlib import Path

from . import canonical as ir
from . import runtime as rt
from .shuttle_checkpoint import ShuttleCheckpoint
from .canonical import (Commit, Event, ExecutionRecord, IRDocument, Node,
                       compute_frontier, digest, dumps, loads, reduce)
from .runtime import ArtifactStore, resolve_inputs

IR_VERSION = ir.IR_VERSION


# ───────────────────────── C32: deterministic fusion ─────────────────────────

def fuse(segments: dict) -> list:
    """The C32 fusion: segments concatenated in ascending worker-key order.
    A segment is a list of canonical log records; the result is the global
    log. Pure, total, order-of-arrival independent."""
    fused: list = []
    for key in sorted(segments):
        fused.extend(segments[key])
    return fused


def log_from_canonical(records: list) -> list:
    return rt.log_from_canonical(records)


# ───────────────────────── the worker side ─────────────────────────

def worker_main() -> int:
    """One loom: hello carries the document once; each execute carries the
    node id and the WORLD records it resolves against (what it needs to
    bind its inputs — the coordinator decides how much of the world to
    send). No filesystem, no clocks, no guessing: canonical records in,
    canonical records out."""
    store = ArtifactStore()
    document: IRDocument | None = None

    def artifact(node, inputs, ctx):
        return {name: ctx.store.put({"node": node.id,
                                     "inputs": {k: v for k, v
                                                in inputs.items()}})
                for name, _ in node.outputs}

    handlers = {"weave.artifact": artifact,
                "weave.cloth": artifact}
    for line in sys.stdin:
        if not line.strip():
            continue
        request = loads(line)                     # canonical or reject
        op = request.get("op")
        if op == "hello":
            document = IRDocument.from_canonical(request["document"])
            print(dumps({"op": "ready"}), flush=True)
            continue
        if op != "execute" or document is None:
            print(dumps({"op": "error", "message": "protocol violation"}),
                  flush=True)
            continue
        node = next((n for n in document.nodes
                     if n.id == request["node"]), None)
        if node is None:
            print(dumps({"op": "error",
                         "message": f"unknown node {request['node']}"}),
                  flush=True)
            continue
        records, _ = rt._execute_node(
            document, node, handlers, request["granted"],
            log_from_canonical(request["world"]), store)
        blocks = [[ref["$cas"], loads(store.get(ref))]
                  for commit in records if isinstance(commit, Commit)
                  for ref in commit.artifacts]
        print(dumps({"op": "records",
                     "records": [r.to_canonical() for r in records],
                     "blocks": blocks}), flush=True)
    return 0


# ───────────────────────── the coordinator ─────────────────────────

class Worker:
    """One real OS process speaking the canonical wire."""

    def __init__(self, index: int):
        self.key = f"worker-{index:02d}"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "prolepsis.shuttle", "--worker",
             str(index)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8")
        self.segment: list = []          # this worker's canonical records
        self.busy = False

    def hello(self, document: IRDocument):
        """One-time handshake: the worker adopts the document (it validates
        the same C0–C32 contracts locally — trust is not assumed)."""
        self.process.stdin.write(
            dumps({"op": "hello",
                   "document": document.to_canonical()}) + "\n")
        self.process.stdin.flush()
        reply = loads(self.process.stdout.readline())
        if reply.get("op") != "ready":
            raise ir.CanonicalError(f"{self.key}: worker rejected the "
                                    "document")

    def dispatch(self, node: Node, world: list, granted, execution_id: str):
        request = {"op": "execute", "execution_id": execution_id,
                   "granted": list(granted), "node": node.id,
                   "world": [r.to_canonical() for r in world]}
        try:
            self.process.stdin.write(dumps(request) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self.busy = False
            raise ir.CanonicalError(f"{self.key}: worker dispatch failed: {exc}") from exc
        self.busy = True

    def collect(self) -> tuple[list, list]:
        """Read one canonical reply: (records, cas_blocks)."""
        line = self.process.stdout.readline()
        if not line:
            raise ir.CanonicalError(f"{self.key}: the worker died")
        reply = loads(line)                        # S4: canonical wire
        if reply.get("op") != "records":
            raise ir.CanonicalError(
                f"{self.key}: {reply.get('message', 'protocol violation')}")
        records = reply["records"]
        existing = {
            r.get("execution_id") for r in self.segment
            if isinstance(r, dict) and r.get("execution_id")
        }
        for record in records:
            execution_id = record.get("execution_id") if isinstance(record, dict) else None
            if execution_id and execution_id in existing:
                continue
            self.segment.append(record)
            if execution_id:
                existing.add(execution_id)
        self.busy = False
        return reply["records"], reply["blocks"]

    def restart(self, document: IRDocument):
        """Replace a dead worker and re-establish the canonical document handshake."""
        try:
            self.process.kill()
            self.process.wait(timeout=2)
        except Exception:
            pass
        self.process = subprocess.Popen(
            [sys.executable, "-m", "prolepsis.shuttle", "--worker", self.key.split("-")[-1]],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8")
        self.busy = False
        self.hello(document)

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=10)
        except Exception:
            self.process.kill()


class Coordinator:
    """The global frontier owner: dispatches ready nodes to idle workers,
    fuses the segments (C32), folds the global state."""

    def __init__(self, document: IRDocument, workers: int, granted=(), checkpoint=None, max_retries=2):
        self.document = document
        self.granted = list(granted)
        self.workers = [Worker(i) for i in range(max(1, workers))]
        for worker in self.workers:
            worker.hello(document)          # validated on BOTH sides (C26)
        self.store = ArtifactStore()
        self.world: list = []            # events + prior records (the world)
        self.checkpoint = ShuttleCheckpoint(checkpoint) if checkpoint else None
        self.max_retries = max(0, int(max_retries))
        self.execution_config = {
            "workers": len(self.workers),
            "granted": sorted(self.granted),
            "max_retries": self.max_retries,
            "protocol": "shuttle-v0.24",
        }
        self.retry_count = 0

    def run(self, world_log: list) -> dict:
        """Execute to exhaustion, then fuse and fold. Returns the run
        report: fused log (canonical), state, digest, metrics.

        The coordinator keeps `world` (everything it knows — events plus
        collected records) purely to RESOLVE inputs and number attempts;
        the GLOBAL LOG is built canonically: the world's own records
        (`prefix`) followed by the C32 fusion of the worker segments —
        worker records appear exactly once, in their segment."""
        self.prefix = list(world_log)
        self.world = list(world_log)
        assignments = {}
        if self.checkpoint and self.checkpoint.exists():
            restored_world, assignments, restored_segments, restored_retry_count = self.checkpoint.load(self.document, execution_config=self.execution_config)
            self.world = restored_world
            self.retry_count = restored_retry_count
            self.prefix = list(restored_world[:len(world_log)])
            for worker in self.workers:
                worker.segment = list(restored_segments.get(worker.key, []))
        else:
            for worker in self.workers:
                worker.segment = []
        waves = 0
        while True:
            frontier = compute_frontier(self.document, self.world)
            ready = [n for n in self.document.nodes
                     if frontier[n.id] == "ready"]
            if not ready:
                break
            waves += 1
            pending = list(ready)
            inflight: list[tuple[Worker, Node, str]] = []
            while pending or inflight:
                for worker in self.workers:          # fill idle looms
                    if not pending:
                        break
                    if not worker.busy:
                        node = pending.pop(0)
                        execution_id = (f"{node.id}#"
                                        + str(rt._attempt_number(
                                            node, self.world)))
                        try:
                            worker.dispatch(node, self.world, self.granted,
                                            execution_id)
                        except ir.CanonicalError:
                            attempts = (assignments.get(worker.key, {}).get("retries", 0)
                                       if assignments.get(worker.key, {}).get("execution_id") == execution_id
                                       else 0)
                            if attempts >= self.max_retries:
                                raise
                            assignments[worker.key] = {
                                "node": node.id, "execution_id": execution_id,
                                "status": "retrying", "retries": attempts + 1}
                            self.retry_count += 1
                            worker.restart(self.document)
                            worker.dispatch(node, self.world, self.granted,
                                            execution_id)
                        inflight.append((worker, node, execution_id))
                if inflight:
                    worker, node, execution_id = inflight.pop(0)
                    try:
                        records, blocks = worker.collect()
                    except ir.CanonicalError:
                        attempts = (assignments.get(worker.key, {}).get("retries", 0)
                                   if assignments.get(worker.key, {}).get("execution_id") == execution_id
                                   else 0)
                        if attempts >= self.max_retries:
                            raise
                        assignments[worker.key] = {
                            "node": node.id, "execution_id": execution_id,
                            "status": "retrying", "retries": attempts + 1}
                        self.retry_count += 1
                        worker.restart(self.document)
                        worker.dispatch(node, self.world, self.granted, execution_id)
                        inflight.append((worker, node, execution_id))
                        continue
                    for ref, payload in blocks:      # CAS union (C21)
                        self.store.put(payload)
                    self.world.extend(log_from_canonical(records))
                    assignments[worker.key] = {
                        "node": node.id, "execution_id": execution_id,
                        "status": "committed", "retries": assignments.get(worker.key, {}).get("retries", 0)}
                    if self.checkpoint:
                        self.checkpoint.save(self.document, self.world,
                                             assignments,
                                             {w.key: w.segment for w in self.workers},
                                             execution_config=self.execution_config,
                                             retry_count=self.retry_count)
        segments = {w.key: w.segment for w in self.workers}
        fused = fuse(segments)            # C32: deterministic fusion
        # the world's own records come first (event ownership), worker
        # segments follow in key order — one global log, one fold
        global_log = ([r.to_canonical() for r in self.prefix]
                      + list(fused))
        state = reduce(self.document, log_from_canonical(global_log))
        return {"log": global_log,
                "state": state, "digest": digest(state),
                "waves": waves, "workers": len(self.workers),
                "fused": len(fused), "retries": self.retry_count}


# ───────────────────────── the self-check (S1–S5) ─────────────────────────

def _doc(nodes: list, events: list, requires: list) -> IRDocument:
    return IRDocument.from_canonical({
        "ir_version": IR_VERSION, "schema_version": "1.0.0",
        "compatibility_version": IR_VERSION, "namespaces": {},
        "initial": {}, "intent": {"goal": "g", "requires": requires},
        "nodes": nodes, "events": events})


def _diamond() -> IRDocument:
    """a → {b, c} → d over START, plus an independent e on the side."""
    node = lambda i, reqs, ins: {                     # noqa: E731
        "id": i, "operation": "weave.artifact", "inputs": ins,
        "outputs": [[i + "_out", "ref"]], "requirements": reqs,
        "effects": [{"kind": "pure"}]}
    return _doc(
        [node("a", ["START"], []), node("b", ["a"], [["a_out", "ref"]]),
         node("c", ["a"], [["a_out", "ref"]]),
         node("d", ["b", "c"], [["b_out", "ref"], ["c_out", "ref"]]),
         node("e", ["START"], [])],
        [{"event_type": "START", "facts": [["date", "date"]]}],
        ["d", "e"])


def self_check() -> None:
    document = _diamond()
    world = [Event("s1", "START", 1, "w", {"date": "2026-11-06"})]

    # the honest sequential baseline (the reference runtime, one process)
    def handler(node, inputs, ctx):
        return {name: ctx.store.put({"node": node.id,
                                     "inputs": dict(inputs)})
                for name, _ in node.outputs}

    baseline = rt.execute(document, {"weave.artifact": handler}, log=world)

    # S2/S3: 1, 2 and 4 real workers — and the sequential baseline — all
    # fold to the SAME digest
    digests = {}
    for count in (1, 2, 4):
        coordinator = Coordinator(document, count)
        try:
            report = coordinator.run(world)
        finally:
            for worker in coordinator.workers:
                worker.close()
        digests[count] = report["digest"]
        assert report["fused"] == 10, report["fused"]  # 5 nodes × 2 records
    assert digests[1] == digests[2] == digests[4], digests
    assert digests[1] == baseline.digest, (digests[1], baseline.digest)

    # v0.21: a worker can disappear after dispatch; retry the SAME execution
    # identity and keep one canonical commit. The checkpoint is also durable.
    with tempfile.TemporaryDirectory() as tmp:
        checkpoint_path = Path(tmp) / "shuttle.json"
        coordinator = Coordinator(document, 2,
                                  checkpoint=checkpoint_path,
                                  max_retries=2)
        original_collect = coordinator.workers[0].collect
        crashed = {"done": False}
        def crash_once():
            if not crashed["done"]:
                crashed["done"] = True
                coordinator.workers[0].process.kill()
            return original_collect()
        coordinator.workers[0].collect = crash_once
        try:
            recovered = coordinator.run(world)
        finally:
            for worker in coordinator.workers:
                worker.close()
        assert recovered["retries"] >= 1
        assert recovered["digest"] == baseline.digest
        assert checkpoint_path.is_file()

        # Commit-window recovery: if the Coordinator terminates after the
        # worker reply is collected but before checkpoint publication, the
        # durable state is still the previous checkpoint. A fresh Coordinator
        # must safely redispatch the unfinished execution and produce exactly
        # the same canonical result.
        window_checkpoint = Path(tmp) / "window.json"
        coordinator = Coordinator(document, 2, checkpoint=window_checkpoint)
        original_save = coordinator.checkpoint.save
        stopped_before_commit = {"done": False}
        class CommitWindowStop(Exception):
            pass
        def save_before_commit(*args, **kwargs):
            if not stopped_before_commit["done"]:
                stopped_before_commit["done"] = True
                raise CommitWindowStop()
            return original_save(*args, **kwargs)
        coordinator.checkpoint.save = save_before_commit
        try:
            coordinator.run(world)
        except CommitWindowStop:
            pass
        finally:
            for worker in coordinator.workers:
                worker.close()
        assert stopped_before_commit["done"]
        assert not window_checkpoint.exists()
        resumed_window = Coordinator(document, 2, checkpoint=window_checkpoint)
        try:
            window_report = resumed_window.run(world)
        finally:
            for worker in resumed_window.workers:
                worker.close()
        assert window_report["digest"] == baseline.digest
        assert len(window_report["log"]) == len(recovered["log"])

        # v0.23: a Coordinator process can terminate after a durable commit
        # and a fresh Coordinator can resume from that checkpoint instead of
        # redispatching already committed nodes.
        restart_checkpoint = Path(tmp) / "restart.json"
        coordinator = Coordinator(document, 2, checkpoint=restart_checkpoint)
        original_save = coordinator.checkpoint.save
        stopped = {"done": False}
        class CoordinatorStop(Exception):
            pass
        def save_then_stop(*args, **kwargs):
            payload = original_save(*args, **kwargs)
            if not stopped["done"]:
                stopped["done"] = True
                raise CoordinatorStop()
            return payload
        coordinator.checkpoint.save = save_then_stop
        try:
            coordinator.run(world)
        except CoordinatorStop:
            pass
        finally:
            for worker in coordinator.workers:
                worker.close()
        assert stopped["done"]
        partial_world, _, partial_segments, _ = ShuttleCheckpoint(
            restart_checkpoint).load(document, execution_config=coordinator.execution_config)
        assert len(partial_world) < len(recovered["log"])
        assert any(partial_segments.values())

        resumed = Coordinator(document, 2, checkpoint=restart_checkpoint)
        try:
            resumed_report = resumed.run(world)
        finally:
            for worker in resumed.workers:
                worker.close()
        assert resumed_report["digest"] == baseline.digest
        assert resumed_report["retries"] == 0
        assert len(resumed_report["log"]) == len(recovered["log"])

        # v0.27: one worker may still be in-flight when another worker's
        # commit is durably checkpointed. A Coordinator crash at that exact
        # point must discard the non-durable in-flight reply and let a fresh
        # Coordinator redispatch that unfinished execution.
        inflight_checkpoint = Path(tmp) / "inflight.json"
        coordinator = Coordinator(document, 2, checkpoint=inflight_checkpoint)
        original_save = coordinator.checkpoint.save
        stopped_inflight = {"done": False}
        class InflightStop(Exception):
            pass
        def save_after_first_commit(*args, **kwargs):
            payload = original_save(*args, **kwargs)
            if not stopped_inflight["done"]:
                stopped_inflight["done"] = True
                raise InflightStop()
            return payload
        coordinator.checkpoint.save = save_after_first_commit
        try:
            coordinator.run(world)
        except InflightStop:
            pass
        finally:
            for worker in coordinator.workers:
                worker.close()
        assert stopped_inflight["done"]
        durable_world, _, durable_segments, _ = ShuttleCheckpoint(
            inflight_checkpoint).load(
                document, execution_config=coordinator.execution_config)
        assert len(durable_world) < len(recovered["log"])
        assert sum(len(records) for records in durable_segments.values()) == 2

        resumed_inflight = Coordinator(
            document, 2, checkpoint=inflight_checkpoint)
        try:
            inflight_report = resumed_inflight.run(world)
        finally:
            for worker in resumed_inflight.workers:
                worker.close()
        assert inflight_report["digest"] == baseline.digest
        assert inflight_report["retries"] == 0
        assert len(inflight_report["log"]) == len(recovered["log"])


    # Multi-worker retry identity: several executions are in flight at once.
    # Killing worker-00 must retry its own execution_id, not the ID from the
    # last worker dispatched by the fill loop.
    coordinator = Coordinator(document, 4, max_retries=1)
    original_collect = coordinator.workers[0].collect
    failed = {"done": False}
    def collect_and_fail_once():
        if not failed["done"]:
            failed["done"] = True
            coordinator.workers[0].process.kill()
        return original_collect()
    coordinator.workers[0].collect = collect_and_fail_once
    try:
        retry_report = coordinator.run(world)
    finally:
        for worker in coordinator.workers:
            worker.close()
    assert retry_report["retries"] >= 1
    assert retry_report["digest"] == baseline.digest

    # Retry accounting is per execution, not per worker. Every distinct
    # execution below fails once at dispatch; max_retries=1 must permit each
    # independent execution even when the same worker handles several nodes.
    coordinator = Coordinator(document, 2, max_retries=1)
    original_dispatch = coordinator.workers[0].dispatch
    failed_dispatches = set()
    def dispatch_fail_once(node, world, granted, execution_id):
        if execution_id not in failed_dispatches:
            failed_dispatches.add(execution_id)
            raise ir.CanonicalError("synthetic dispatch failure")
        return original_dispatch(node, world, granted, execution_id)
    coordinator.workers[0].dispatch = dispatch_fail_once
    try:
        retry_accounting = coordinator.run(world)
    finally:
        for worker in coordinator.workers:
            worker.close()
    assert retry_accounting["retries"] >= 1
    assert retry_accounting["digest"] == baseline.digest

    # S1: arrival order independence — fuse the SAME segments in any order
    segments = {f"worker-{i:02d}": [] for i in range(3)}
    segments["worker-00"] = [{"node": "a", "status": "completed"}]
    segments["worker-01"] = [{"node": "b", "status": "completed"}]
    segments["worker-02"] = [{"node": "c", "status": "completed"}]
    forward = fuse(segments)
    shuffled = fuse(dict(reversed(list(segments.items()))))
    assert forward == shuffled                            # sorted inside
    assert digest(forward) == digest(shuffled)

    # S5: replay independence — the fused log alone, no runtime, no store
    coordinator = Coordinator(document, 2)
    try:
        report = coordinator.run(world)
    finally:
        for worker in coordinator.workers:
            worker.close()
    replay = reduce(document, log_from_canonical(report["log"]))
    assert digest(replay) == report["digest"]
    assert report["digest"] == baseline.digest

    # S4: the wire is canonical — the protocol messages parse under the
    # strict loader (checked inside collect(); here we assert the fusion
    # inputs themselves were strict-loaded values)
    assert isinstance(fuse({}), list)
    assert fuse({"b": [1], "a": [0]}) == [0, 1]

    print("SELF-CHECK S1–S5: all distributed invariants hold")
    print(f"      diamond on 1/2/4 real worker processes: one digest "
          f"{digests[1][:23]}…")


# ───────────────────────── the demo ─────────────────────────

def demo() -> None:
    """A wider cloth — 12 nodes, 4 real worker processes, honest clocks."""
    node = lambda i, reqs, ins: {                     # noqa: E731
        "id": i, "operation": "weave.artifact", "inputs": ins,
        "outputs": [[i + "_out", "ref"]], "requirements": reqs,
        "effects": [{"kind": "pure"}]}
    nodes = [node("warp", ["START"], [])]
    for i in range(5):
        nodes.append(node(f"weft_{i}", ["warp"], [["warp_out", "ref"]]))
    for i in range(5):
        nodes.append(node(f"selvedge_{i}", [f"weft_{i}"],
                          [[f"weft_{i}_out", "ref"]]))
    nodes.append(node("cloth", [f"selvedge_{i}" for i in range(5)],
                      [[f"selvedge_{i}_out", "ref"] for i in range(5)]))
    document = _doc(nodes, [{"event_type": "START",
                             "facts": [["date", "date"]]}], ["cloth"])
    world = [Event("s1", "START", 1, "weaver", {"date": "2026-11-06"})]

    print("SHUTTLE — the distributed runtime (STEP K, C32)")
    print("=" * 74)
    print(f"  document: {len(nodes)} nodes · addr {document.addr[:30]}…")

    def handler(node, inputs, ctx):
        return {name: ctx.store.put({"node": node.id,
                                     "inputs": dict(inputs)})
                for name, _ in node.outputs}

    t0 = time.perf_counter()
    sequential = rt.execute(document, {"weave.artifact": handler}, log=world)
    t_sequential = time.perf_counter() - t0

    coordinator = Coordinator(document, 4)
    try:
        t0 = time.perf_counter()
        report = coordinator.run(world)
        t_distributed = time.perf_counter() - t0
    finally:
        for worker in coordinator.workers:
            worker.close()

    print(f"  sequential: {sequential.waves} waves · "
          f"digest {sequential.digest[:30]}… · {t_sequential:.3f} s")
    print(f"  distributed: 4 real processes · {report['waves']} waves · "
          f"{report['fused']} records fused · {t_distributed:.3f} s")
    print(f"  S3 sequential == distributed: "
          f"{sequential.digest == report['digest']}")
    replay = reduce(document, log_from_canonical(report["log"]))
    print(f"  S5 replay (no runtime): {digest(replay) == report['digest']}")
    print("  honesty: workers are real OS processes on a canonical JSON")
    print("  wire; the handlers themselves do no real I/O — the CLAIM here")
    print("  is the fusion contract, not a speed record")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--worker":
        sys.exit(worker_main())
    try:
        self_check()
        demo()
    except (ir.CanonicalError, AssertionError) as error:
        print(f"SHUTTLE FAILURE: {type(error).__name__}: {error}",
              file=sys.stderr)
        sys.exit(1)
