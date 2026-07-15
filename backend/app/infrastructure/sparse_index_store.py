from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import uuid4

from app.sparse_index import SparseIndexSnapshot

_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class InvalidIndexVersion(ValueError):
    pass


class SparseIndexStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def artifact_path(self, index_version: str) -> Path:
        if not _VERSION_PATTERN.fullmatch(index_version):
            raise InvalidIndexVersion(index_version)
        return self._root / f"{index_version}.json"

    def write(self, snapshot: SparseIndexSnapshot) -> Path:
        destination = self.artifact_path(snapshot.index_version)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        payload = json.dumps(
            snapshot.to_dict(), ensure_ascii=False, separators=(",", ":")
        )
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return destination

    def read(self, index_version: str) -> SparseIndexSnapshot:
        value = json.loads(
            self.artifact_path(index_version).read_text(encoding="utf-8")
        )
        if not isinstance(value, dict):
            raise ValueError("invalid sparse index artifact")
        return SparseIndexSnapshot.from_dict(value)

    def delete(self, index_version: str) -> None:
        self.artifact_path(index_version).unlink(missing_ok=True)