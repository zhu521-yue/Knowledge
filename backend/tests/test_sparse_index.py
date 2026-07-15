from dataclasses import replace
from pathlib import Path

import pytest

from app.chunks import ChildChunk, ChunkIdentity
from app.infrastructure.sparse_index_store import (
    InvalidIndexVersion,
    SparseIndexStore,
)
from app.retrieval_scope import AuthorizedRetrievalScope
from app.sparse_index import (
    SparseIndexSnapshot,
    build_sparse_index,
    delete_runs,
    search_sparse_index,
    tokenize,
)


def _child(
    child_id: str,
    text: str,
    *,
    user_id: str = "user-1",
    run_id: str = "run-active",
    version: str = "bm25-v1",
) -> ChildChunk:
    return ChildChunk(
        id=child_id,
        parent_chunk_id=f"parent-{child_id}",
        ordinal=0,
        identity=ChunkIdentity(
            user_id=user_id,
            source_document_id=f"source-{child_id}",
            source_revision_id=f"revision-{child_id}",
            ingestion_run_id=run_id,
        ),
        parent_char_start=0,
        parent_char_end=len(text),
        heading_path=("Section",),
        page_start=1,
        page_end=1,
        token_count=len(tokenize(text)),
        dense_index_version="dense-v1",
        sparse_index_version=version,
        text=text,
    )


def _scope(*run_ids: str) -> AuthorizedRetrievalScope:
    return AuthorizedRetrievalScope(
        user_id="user-1",
        topic_id="topic-1",
        active_run_ids=frozenset(run_ids),
    )


def test_exact_technical_term_is_recalled_with_stable_rank() -> None:
    snapshot = build_sparse_index(
        [
            _child("child-b", "介绍一般数据库索引"),
            _child("child-a", "Milvus HNSW 参数和向量索引"),
        ],
        index_version="bm25-v1",
    )

    hits = search_sparse_index(snapshot, "HNSW", scope=_scope("run-active"))

    assert [(hit.child_chunk_id, hit.rank) for hit in hits] == [("child-a", 1)]
    assert hits[0].parent_chunk_id == "parent-child-a"
    assert hits[0].page_start == 1


def test_chinese_tokenization_supports_exact_term_recall() -> None:
    assert tokenize("稀疏索引 BM25") == ("稀", "疏", "索", "引", "bm25")
    snapshot = build_sparse_index(
        [_child("child-a", "精确术语：混合检索")], index_version="bm25-v1"
    )

    assert search_sparse_index(snapshot, "混合检索", scope=_scope("run-active"))


def test_scope_filters_user_inactive_run_and_index_version_before_scoring() -> None:
    valid = _child("valid", "shared exact-term")
    other_user = _child("other-user", "exact-term", user_id="user-2")
    old_run = _child("old-run", "exact-term", run_id="run-old")
    snapshot = build_sparse_index(
        [valid, other_user, old_run], index_version="bm25-v1"
    )

    hits = search_sparse_index(snapshot, "exact-term", scope=_scope("run-active"))

    assert [hit.child_chunk_id for hit in hits] == ["valid"]


def test_build_is_idempotent_and_rejects_conflicting_child_identity() -> None:
    child = _child("child-a", "same content")

    snapshot = build_sparse_index([child, child], index_version="bm25-v1")

    assert len(snapshot.documents) == 1
    with pytest.raises(ValueError, match="conflicting child chunk id"):
        build_sparse_index(
            [child, replace(child, text="different content")],
            index_version="bm25-v1",
        )


def test_version_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="version mismatch"):
        build_sparse_index(
            [_child("child-a", "content", version="bm25-v2")],
            index_version="bm25-v1",
        )


def test_delete_run_removes_only_target_run() -> None:
    snapshot = build_sparse_index(
        [
            _child("active", "content", run_id="run-active"),
            _child("old", "content", run_id="run-old"),
        ],
        index_version="bm25-v1",
    )

    remaining = delete_runs(snapshot, frozenset({"run-old"}))

    assert [document.child_chunk_id for document in remaining.documents] == ["active"]


def test_store_round_trip_and_delete_are_version_scoped(tmp_path: Path) -> None:
    store = SparseIndexStore(tmp_path)
    snapshot = build_sparse_index(
        [_child("child-a", "HNSW exact term")], index_version="bm25-v1"
    )

    path = store.write(snapshot)

    assert path == tmp_path / "bm25-v1.json"
    assert store.read("bm25-v1") == snapshot
    assert list(tmp_path.glob(".*.tmp")) == []
    store.delete("bm25-v1")
    assert not path.exists()


@pytest.mark.parametrize("version", ["../escape", "nested/index", "", ".hidden"])
def test_store_rejects_unsafe_versions(tmp_path: Path, version: str) -> None:
    store = SparseIndexStore(tmp_path)

    with pytest.raises(InvalidIndexVersion):
        store.artifact_path(version)


def test_snapshot_rejects_silently_dropped_invalid_documents() -> None:
    with pytest.raises(ValueError, match="invalid sparse index documents"):
        SparseIndexSnapshot.from_dict(
            {
                "schema_version": "1",
                "index_version": "bm25-v1",
                "documents": "invalid",
            }
        )