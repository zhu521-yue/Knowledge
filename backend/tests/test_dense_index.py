from collections.abc import Sequence

import pytest

from app.chunks import ChildChunk, ChunkIdentity
from app.dense_index import (
    DenseIndexRecord,
    DenseIndexService,
    DenseSearchHit,
)
from app.embedding import EmbeddingRunResult, EmbeddingVector
from app.infrastructure.dense_milvus import (
    _collection_name,
    _filter_expression,
    _record_dict,
)
from app.retrieval_scope import AuthorizedRetrievalScope


class _Backend:
    def __init__(self) -> None:
        self.collections: list[tuple[str, int]] = []
        self.upserts: list[tuple[str, tuple[DenseIndexRecord, ...]]] = []
        self.deletes: list[tuple[str, frozenset[str]]] = []
        self.searches: list[tuple[str, tuple[float, ...], AuthorizedRetrievalScope, int]] = []

    def ensure_collection(self, index_version: str, dimension: int) -> None:
        self.collections.append((index_version, dimension))

    def upsert(
        self, index_version: str, records: Sequence[DenseIndexRecord]
    ) -> None:
        self.upserts.append((index_version, tuple(records)))

    def delete_runs(self, index_version: str, run_ids: frozenset[str]) -> None:
        self.deletes.append((index_version, run_ids))

    def search(
        self,
        index_version: str,
        query_vector: Sequence[float],
        *,
        scope: AuthorizedRetrievalScope,
        limit: int,
    ) -> Sequence[DenseSearchHit]:
        self.searches.append((index_version, tuple(query_vector), scope, limit))
        return (
            DenseSearchHit(
                child_chunk_id="child-1",
                parent_chunk_id="parent-1",
                ingestion_run_id="run-1",
                score=0.9,
                rank=1,
                page_start=1,
                page_end=1,
                parent_char_start=0,
                parent_char_end=10,
            ),
        )


def _child(child_id: str, *, run_id: str = "run-1") -> ChildChunk:
    return ChildChunk(
        id=child_id,
        parent_chunk_id=f"parent-{child_id}",
        ordinal=0,
        identity=ChunkIdentity(
            user_id="user-1",
            source_document_id="source-1",
            source_revision_id="revision-1",
            ingestion_run_id=run_id,
        ),
        parent_char_start=0,
        parent_char_end=10,
        heading_path=("Section",),
        page_start=1,
        page_end=1,
        token_count=2,
        dense_index_version="staging",
        sparse_index_version="bm25-v1",
        text="dense text",
    )


def _embedding(*child_ids: str) -> EmbeddingRunResult:
    return EmbeddingRunResult(
        provider="ollama-gateway",
        model_identifier="model@digest",
        index_version="dense-v1",
        vectors=tuple(
            EmbeddingVector(child_id, (1.0, 0.0, 0.0))
            for child_id in child_ids
        ),
        used_fallback=False,
    )


def test_rebuild_deletes_run_then_idempotently_upserts_complete_child_set() -> None:
    backend = _Backend()
    service = DenseIndexService(backend)
    chunks = [_child("child-1"), _child("child-2")]

    records = service.rebuild_run(chunks, _embedding("child-1", "child-2"))

    assert backend.collections == [("dense-v1", 3)]
    assert backend.deletes == [("dense-v1", frozenset({"run-1"}))]
    assert backend.upserts == [("dense-v1", records)]
    assert [record.child_chunk_id for record in records] == ["child-1", "child-2"]
    assert all(record.user_id == "user-1" for record in records)


def test_rebuild_rejects_partial_or_mixed_run_vectors_before_backend_writes() -> None:
    backend = _Backend()
    service = DenseIndexService(backend)

    with pytest.raises(ValueError, match="child set mismatch"):
        service.rebuild_run(
            [_child("child-1"), _child("child-2")], _embedding("child-1")
        )
    with pytest.raises(ValueError, match="mixed identity"):
        service.rebuild_run(
            [_child("child-1"), _child("child-2", run_id="run-2")],
            _embedding("child-1", "child-2"),
        )
    assert backend.upserts == []


def test_search_passes_authorized_topic_scope_to_backend_before_recall() -> None:
    backend = _Backend()
    service = DenseIndexService(backend)
    scope = AuthorizedRetrievalScope(
        user_id="user-1",
        topic_id="topic-1",
        active_run_ids=frozenset({"run-1"}),
    )

    hits = service.search("dense-v1", [1, 0, 0], scope=scope)

    assert hits[0].child_chunk_id == "child-1"
    assert backend.searches == [("dense-v1", (1, 0, 0), scope, 20)]


def test_empty_authorized_run_scope_never_calls_vector_backend() -> None:
    backend = _Backend()
    service = DenseIndexService(backend)
    scope = AuthorizedRetrievalScope("user-1", "topic-1", frozenset())

    assert service.search("dense-v1", [1, 0, 0], scope=scope) == ()
    assert backend.searches == []


def test_milvus_names_filters_and_records_are_deterministic_and_scoped() -> None:
    record = DenseIndexRecord(
        child_chunk_id="child-1",
        parent_chunk_id="parent-1",
        user_id="user-1",
        source_document_id="source-1",
        source_revision_id="revision-1",
        ingestion_run_id="run-1",
        index_version="dense-v1",
        page_start=1,
        page_end=2,
        parent_char_start=3,
        parent_char_end=20,
        vector=(1.0, 0.0),
    )

    assert _collection_name("dense-v1") == _collection_name("dense-v1")
    assert _collection_name("dense-v1") != _collection_name("dense-v2")
    assert _filter_expression("user-1", frozenset({"run-2", "run-1"})) == (
        'user_id == "user-1" and ingestion_run_id in ["run-1","run-2"]'
    )
    assert _record_dict(record)["vector"] == [1.0, 0.0]
    assert "topic_id" not in _record_dict(record)