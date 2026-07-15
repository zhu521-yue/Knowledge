from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from app.chunks import ChildChunk
from app.embedding import EmbeddingRunResult
from app.retrieval_scope import AuthorizedRetrievalScope


@dataclass(frozen=True, slots=True)
class DenseIndexRecord:
    child_chunk_id: str
    parent_chunk_id: str
    user_id: str
    source_document_id: str
    source_revision_id: str
    ingestion_run_id: str
    index_version: str
    page_start: int
    page_end: int
    parent_char_start: int
    parent_char_end: int
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class DenseSearchHit:
    child_chunk_id: str
    parent_chunk_id: str
    ingestion_run_id: str
    score: float
    rank: int
    page_start: int
    page_end: int
    parent_char_start: int
    parent_char_end: int


class DenseIndexBackend(Protocol):
    def ensure_collection(self, index_version: str, dimension: int) -> None: ...

    def upsert(self, index_version: str, records: Sequence[DenseIndexRecord]) -> None: ...

    def delete_runs(self, index_version: str, run_ids: frozenset[str]) -> None: ...

    def search(
        self,
        index_version: str,
        query_vector: Sequence[float],
        *,
        scope: AuthorizedRetrievalScope,
        limit: int,
    ) -> Sequence[DenseSearchHit]: ...


class DenseIndexService:
    def __init__(self, backend: DenseIndexBackend) -> None:
        self._backend = backend

    def rebuild_run(
        self,
        chunks: Sequence[ChildChunk],
        embedding: EmbeddingRunResult,
    ) -> tuple[DenseIndexRecord, ...]:
        chunks_by_id = {chunk.id: chunk for chunk in chunks}
        if len(chunks_by_id) != len(chunks):
            raise ValueError("duplicate child chunk id")
        if {vector.child_chunk_id for vector in embedding.vectors} != set(chunks_by_id):
            raise ValueError("embedding child set mismatch")
        dimensions = {len(vector.values) for vector in embedding.vectors}
        if len(dimensions) != 1:
            raise ValueError("embedding dimension mismatch")
        dimension = next(iter(dimensions))
        records = tuple(
            _record(chunks_by_id[vector.child_chunk_id], embedding, vector.values)
            for vector in embedding.vectors
        )
        run_ids = frozenset(record.ingestion_run_id for record in records)
        if len(run_ids) != 1:
            raise ValueError("dense index run mixed identity")
        self._backend.ensure_collection(embedding.index_version, dimension)
        self._backend.delete_runs(embedding.index_version, run_ids)
        self._backend.upsert(embedding.index_version, records)
        return records

    def delete_runs(self, index_version: str, run_ids: frozenset[str]) -> None:
        if run_ids:
            self._backend.delete_runs(index_version, run_ids)

    def search(
        self,
        index_version: str,
        query_vector: Sequence[float],
        *,
        scope: AuthorizedRetrievalScope,
        limit: int = 20,
    ) -> tuple[DenseSearchHit, ...]:
        if limit < 1 or not scope.active_run_ids:
            return ()
        hits = self._backend.search(
            index_version,
            query_vector,
            scope=scope,
            limit=limit,
        )
        return tuple(hits)


def _record(
    chunk: ChildChunk,
    embedding: EmbeddingRunResult,
    vector: tuple[float, ...],
) -> DenseIndexRecord:
    return DenseIndexRecord(
        child_chunk_id=chunk.id,
        parent_chunk_id=chunk.parent_chunk_id,
        user_id=chunk.identity.user_id,
        source_document_id=chunk.identity.source_document_id,
        source_revision_id=chunk.identity.source_revision_id,
        ingestion_run_id=chunk.identity.ingestion_run_id,
        index_version=embedding.index_version,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        parent_char_start=chunk.parent_char_start,
        parent_char_end=chunk.parent_char_end,
        vector=vector,
    )