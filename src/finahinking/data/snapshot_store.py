"""Private, immutable, content-addressed market snapshots for offline replay."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from finahinking.p8_2.contracts import DatasetSnapshot

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
MAX_SNAPSHOT_BYTES = 10 * 1024 * 1024


class SnapshotStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, fingerprint: str) -> Path:
        if not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint):
            raise ValueError("snapshot fingerprint must be a SHA-256 reference")
        return self.root / f"{fingerprint}.json"

    def load(self, fingerprint: str) -> DatasetSnapshot:
        path = self._path(fingerprint)
        if path.is_symlink():
            raise ValueError("snapshot integrity check failed")
        with path.open("rb") as handle:
            raw = handle.read(MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise ValueError("snapshot integrity size limit exceeded")
        try:
            snapshot = DatasetSnapshot.from_json(raw.decode("utf-8"))
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise ValueError("snapshot fingerprint integrity check failed") from exc
        if snapshot.fingerprint != fingerprint:
            raise ValueError("snapshot fingerprint integrity check failed")
        return snapshot

    def save(self, snapshot: DatasetSnapshot) -> str:
        if not isinstance(snapshot, DatasetSnapshot):
            raise TypeError("snapshot must be a DatasetSnapshot")
        encoded = snapshot.to_json().encode("utf-8")
        if len(encoded) > MAX_SNAPSHOT_BYTES:
            raise ValueError("snapshot size limit exceeded")
        fingerprint = snapshot.fingerprint
        destination = self._path(fingerprint)
        if destination.exists() or destination.is_symlink():
            self.load(fingerprint)
            return fingerprint
        descriptor, name = tempfile.mkstemp(prefix=".snapshot-", dir=self.root)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # Atomic create-if-absent: never rewrite an admitted snapshot.
                os.link(temporary, destination)
            except FileExistsError:
                self.load(fingerprint)
        finally:
            temporary.unlink(missing_ok=True)
        return fingerprint

    def list_snapshots(self) -> tuple[DatasetSnapshot, ...]:
        return tuple(self.load(path.stem) for path in sorted(self.root.glob("*.json")) if _FINGERPRINT.fullmatch(path.stem))

