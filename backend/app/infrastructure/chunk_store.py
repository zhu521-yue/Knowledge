from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from app.chunks import ChildChunk, ChunkIdentity, ChunkSet, ParentChunk
from app.infrastructure.parsed_document_store import run_artifact_directory

_ARTIFACT_NAME = "chunks.v1.json"


class ChunkStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def artifact_path(self, run_id: str) -> Path:
        return run_artifact_directory(self._root, run_id) / _ARTIFACT_NAME

    def read(self, run_id: str) -> ChunkSet:
        value = json.loads(self.artifact_path(run_id).read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != "1":
            raise ValueError("invalid chunk artifact")
        identity = _identity(value)
        parents_value = value.get("parents")
        children_value = value.get("children")
        if not isinstance(parents_value, list) or not isinstance(children_value, list):
            raise ValueError("invalid chunk artifact")
        parents = tuple(_parent(item, identity) for item in parents_value)
        children = tuple(_child(item, identity) for item in children_value)
        if any(child.parent_chunk_id not in {parent.id for parent in parents} for child in children):
            raise ValueError("chunk parent reference invalid")
        return ChunkSet(
            identity=identity,
            chunking_version=str(value["chunking_version"]),
            parents=parents,
            children=children,
        )

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


def _identity(value: dict[str, object]) -> ChunkIdentity:
    return ChunkIdentity(
        user_id=str(value["user_id"]),
        source_document_id=str(value["source_document_id"]),
        source_revision_id=str(value["source_revision_id"]),
        ingestion_run_id=str(value["ingestion_run_id"]),
    )


def _parent(value: object, identity: ChunkIdentity) -> ParentChunk:
    if not isinstance(value, dict):
        raise ValueError("invalid parent chunk")
    return ParentChunk(
        id=str(value["id"]),
        ordinal=int(value["ordinal"]),
        identity=identity,
        heading_path=tuple(str(item) for item in value["heading_path"]),
        page_start=int(value["page_start"]),
        page_end=int(value["page_end"]),
        document_char_start=int(value["document_char_start"]),
        document_char_end=int(value["document_char_end"]),
        token_count=int(value["token_count"]),
        text=str(value["text"]),
    )


def _child(value: object, identity: ChunkIdentity) -> ChildChunk:
    if not isinstance(value, dict):
        raise ValueError("invalid child chunk")
    return ChildChunk(
        id=str(value["id"]),
        parent_chunk_id=str(value["parent_chunk_id"]),
        ordinal=int(value["ordinal"]),
        identity=identity,
        parent_char_start=int(value["parent_char_start"]),
        parent_char_end=int(value["parent_char_end"]),
        heading_path=tuple(str(item) for item in value["heading_path"]),
        page_start=int(value["page_start"]),
        page_end=int(value["page_end"]),
        token_count=int(value["token_count"]),
        dense_index_version=str(value["dense_index_version"]),
        sparse_index_version=str(value["sparse_index_version"]),
        text=str(value["text"]),
    )
