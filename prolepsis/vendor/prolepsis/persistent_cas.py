#!/usr/bin/env python3
"""Persistent content-addressed storage for the Canonical WEAVE runtime.

This module is runtime infrastructure only. Canonical IR remains responsible
for the meaning of refs; this store is responsible for durable bytes,
integrity, manifests, crash-safe publication, and reclamation.

On-disk layout:
  <root>/blocks/<sha256 hex>     canonical JSON payloads
  <root>/manifests/<name>.json   atomic live-root manifests
  <root>/tmp/                    temporary files removed by recover()

A block is published with write-to-temp + fsync + atomic replace. Existing
equal content is never rewritten. Reads verify both the address and the
canonical payload, so corruption cannot silently become state.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from . import canonical
from .runtime import ArtifactStore


class PersistentArtifactStore(ArtifactStore):
    """Durable drop-in ArtifactStore with atomic CAS publication."""

    FORMAT_VERSION = "1"

    def __init__(self, root, blocks=None):
        self.root = Path(root)
        self.blocks_dir = self.root / "blocks"
        self.manifests_dir = self.root / "manifests"
        self.tmp_dir = self.root / "tmp"
        self.blocks_dir.mkdir(parents=True, exist_ok=True)
        self.manifests_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        super().__init__()
        self.recover()
        if blocks:
            for ref, payload in blocks.items():
                self._publish(ref, payload)

    @staticmethod
    def _key(ref: str) -> str:
        if not isinstance(ref, str) or not ref.startswith("sha256:") or len(ref) != 71:
            raise canonical.CanonicalError(f"invalid CAS address: {ref!r}")
        if any(ch not in "0123456789abcdef" for ch in ref[7:]):
            raise canonical.CanonicalError(f"invalid CAS address: {ref!r}")
        return ref[7:]

    def _path(self, ref: str) -> Path:
        return self.blocks_dir / self._key(ref)

    def _publish(self, ref: str, payload: str) -> None:
        path = self._path(ref)
        if path.exists():
            return
        fd, name = tempfile.mkstemp(prefix=".block-", dir=self.tmp_dir)
        tmp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            dir_fd = os.open(self.blocks_dir, os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            tmp.unlink(missing_ok=True)

    def put(self, value) -> dict:
        payload = canonical.dumps(value)
        ref = {"$cas": canonical.digest(value)}
        self._publish(ref["$cas"], payload)
        return ref

    def get(self, ref: dict) -> str:
        if canonical.check_value(ref, "ref"):
            raise canonical.CanonicalError(f"not a ref: {ref!r}")
        address = ref["$cas"]
        path = self._path(address)
        try:
            payload = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise canonical.CanonicalError(
                f"CAS block not found: {address}") from exc
        try:
            value = canonical.loads(payload)
        except Exception as exc:
            raise canonical.CanonicalError(
                f"CAS block is invalid JSON: {address}") from exc
        if canonical.digest(value) != address:
            raise canonical.CanonicalError(
                f"CAS integrity failure: {address}")
        return payload

    def to_canonical(self) -> dict:
        result = {}
        for path in sorted(self.blocks_dir.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                ref = "sha256:" + path.name
                result[ref] = path.read_text(encoding="utf-8")
        return result

    @classmethod
    def from_canonical(cls, value: dict, root) -> "PersistentArtifactStore":
        return cls(root, value)

    def __len__(self):
        return sum(1 for path in self.blocks_dir.iterdir()
                   if path.is_file() and not path.name.startswith("."))

    def bytes(self) -> int:
        return sum(path.stat().st_size for path in self.blocks_dir.iterdir()
                   if path.is_file() and not path.name.startswith("."))

    def write_manifest(self, name: str, roots) -> dict:
        """Publish a durable live-root manifest atomically."""
        if not name or "/" in name or name in {".", ".."}:
            raise ValueError("manifest name must be a simple non-empty name")
        roots = [dict(root) for root in roots]
        for root in roots:
            if canonical.check_value(root, "ref"):
                raise canonical.CanonicalError(f"invalid manifest root: {root!r}")
            self._path(root["$cas"])  # validate address
        value = {"format": self.FORMAT_VERSION, "name": name, "roots": roots}
        payload = canonical.dumps(value)
        target = self.manifests_dir / f"{name}.json"
        fd, temp_name = tempfile.mkstemp(prefix=".manifest-", dir=self.tmp_dir)
        tmp = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
            dir_fd = os.open(self.manifests_dir, os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            tmp.unlink(missing_ok=True)
        return value

    def read_manifest(self, name: str) -> dict:
        path = self.manifests_dir / f"{name}.json"
        try:
            value = canonical.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise canonical.CanonicalError(
                f"CAS manifest not found: {name}") from exc
        if value.get("format") != self.FORMAT_VERSION:
            raise canonical.CanonicalError("unsupported CAS manifest format")
        roots = value.get("roots")
        if not isinstance(roots, list):
            raise canonical.CanonicalError("CAS manifest roots must be a list")
        return value

    def live_roots(self):
        roots = []
        for path in sorted(self.manifests_dir.glob("*.json")):
            value = canonical.loads(path.read_text(encoding="utf-8"))
            roots.extend(value.get("roots", []))
        return roots

    def reclaim(self, roots) -> dict:
        """Mark-and-sweep the durable block directory from explicit roots."""
        live: set[str] = set()
        pending: list[str] = []

        def mark(value):
            if isinstance(value, dict):
                if set(value) == {"$cas"} and isinstance(value["$cas"], str):
                    ref = value["$cas"]
                    if ref not in live:
                        pending.append(ref)
                    return
                for child in value.values():
                    mark(child)
            elif isinstance(value, list):
                for child in value:
                    mark(child)

        for root in roots:
            mark(root)

        while pending:
            ref = pending.pop()
            if ref in live:
                continue
            path = self._path(ref)
            if not path.exists():
                raise canonical.CanonicalError(
                    f"GC root references missing CAS block: {ref}")
            payload = path.read_text(encoding="utf-8")
            try:
                value = canonical.loads(payload)
            except Exception as exc:
                raise canonical.CanonicalError(
                    f"CAS block is invalid JSON: {ref}") from exc
            if canonical.digest(value) != ref:
                raise canonical.CanonicalError(
                    f"CAS integrity failure: {ref}")
            live.add(ref)
            mark(value)

        paths = {
            "sha256:" + path.name: path
            for path in self.blocks_dir.iterdir()
            if path.is_file() and not path.name.startswith(".")
        }
        reclaimed = sorted(set(paths) - live)
        reclaimed_bytes = sum(paths[ref].stat().st_size for ref in reclaimed)
        for ref in reclaimed:
            paths[ref].unlink()
        return {
            "live_blocks": len(live),
            "reclaimed_objects": len(reclaimed),
            "reclaimed_bytes": reclaimed_bytes,
            "reclaimed_refs": reclaimed,
            "before": len(paths),
            "after": len(paths) - len(reclaimed),
        }

    def reclaim_live(self) -> dict:
        """Reclaim everything not reachable from all durable manifests."""
        return self.reclaim(self.live_roots())

    def recover(self) -> dict:
        """Remove abandoned temp files left by interrupted atomic writes."""
        removed = 0
        for path in self.tmp_dir.iterdir():
            if path.is_file():
                path.unlink()
                removed += 1
        return {"removed_temporary_files": removed}

    def verify(self) -> dict:
        """Verify every on-disk block against its content address."""
        checked = 0
        for path in sorted(self.blocks_dir.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            address = "sha256:" + path.name
            payload = path.read_text(encoding="utf-8")
            try:
                value = canonical.loads(payload)
            except Exception as exc:
                raise canonical.CanonicalError(
                    f"CAS block is invalid JSON: {address}") from exc
            if canonical.digest(value) != address:
                raise canonical.CanonicalError(
                    f"CAS integrity failure: {address}")
            checked += 1
        return {"checked_blocks": checked, "bytes": self.bytes()}
