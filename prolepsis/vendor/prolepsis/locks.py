# -*- coding: utf-8 -*-
"""
LOCKS v0.1 — real locks for multi-process looms
================================================
A single event loop needs no locks on synchronous code: only one coroutine
runs at a time. Real looms — several OS processes hammering one beam and one
pick log — need real locks. These are file locks:

  · flock(2) on POSIX (advisory, per-file, released on close)
  · an O_CREAT|O_EXCL spin with timeout elsewhere (portable fallback)

The lock protects exactly two things:
  · Beam.put / Beam.submit_cloth — the check-then-write pair on the CAS store
    is not atomic across processes (the write itself is idempotent — same
    content, same address — but two writers must not both count "new", and
    a reader must never see a half-written block);
  · PickLog.commit — batched appends to picks.jsonl, one writer at a time.

What is NOT locked: reading (imprints, passports, replay) — content-addressed
blocks are immutable once written. And the simulated I/O latency lives
OUTSIDE the lock: the lock covers the fast bookkeeping, not the slow op.

One coarse lock per store. Contention is microseconds; if it ever matters,
shard by address prefix — the beam's objects/xx layout already invites it.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

try:
    import fcntl
except ImportError:              # non-POSIX
    fcntl = None


class FileLock:
    """A cross-process lock on one file. Blocking on POSIX (flock),
    spin-with-timeout elsewhere."""

    def __init__(self, path: Path | str, timeout: float = 30.0):
        self.path = Path(path)
        self.timeout = timeout
        self._fh = None
        self._held = False
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def acquire(self):
        if fcntl is not None:
            self._fh = open(self.path, "a+")
            fcntl.flock(self._fh, fcntl.LOCK_EX)     # blocking; no nesting → no deadlock
        else:
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.close(fd)
                    break
                except FileExistsError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"file lock timeout: {self.path}")
                    time.sleep(0.005)
        self._held = True

    def release(self):
        if not self._held:
            return
        if fcntl is not None and self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
        else:
            self.path.unlink(missing_ok=True)
        self._held = False

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False
