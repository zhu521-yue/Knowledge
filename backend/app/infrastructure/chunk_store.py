from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from app.chunks import ChunkSet
from app.infrastructure.parsed_document_store import run_artifact_directory

_ARTIFACT_NAME = "chunks.v1.json"


class ChunkStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def artifact_path(self, run_id: str) -> Path:
        return run_artifact_directory(self._root, run_id) / _ARTIFACT_NAME

    def write(self, chunks: ChunkSet) -> Path:
        destination = self.artifact_path(chunks.identity.ingestion_run_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        payload = json.dumps(
            chunks.to_dict(), ensure_ascii=False, separators=(",", ":")
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