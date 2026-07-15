from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import uuid4

from app.parsed_documents import ParsedDocument

_ARTIFACT_NAME = "parsed-document.v1.json"
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class InvalidRunId(ValueError):
    pass


class ParsedDocumentStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def artifact_path(self, run_id: str) -> Path:
        if not _RUN_ID_PATTERN.fullmatch(run_id):
            raise InvalidRunId(run_id)
        return self._root / run_id / _ARTIFACT_NAME

    def write(self, run_id: str, document: ParsedDocument) -> Path:
        destination = self.artifact_path(run_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        payload = json.dumps(
            document.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
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