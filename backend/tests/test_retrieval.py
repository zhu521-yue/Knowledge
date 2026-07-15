from app.chunks import ChildChunk, ChunkIdentity, ParentChunk
from app.dense_index import DenseSearchHit
from app.retrieval import RETRIEVAL_VERSION, reciprocal_rank_fusion
from app.retrieval_scope import AuthorizedRetrievalScope
from app.sparse_index import SparseSearchHit


def _identity(run_id: str = "run-1", user_id: str = "user-1") -> ChunkIdentity:
    return ChunkIdentity(user_id, f"source-{run_id}", f"revision-{run_id}", run_id)


def _parent(parent_id: str, *, run_id: str = "run-1", user_id: str = "user-1") -> ParentChunk:
    return ParentChunk(
        id=parent_id,
        ordinal=0,
        identity=_identity(run_id, user_id),
        heading_path=("Section",),
        page_start=1,
        page_end=2,
        document_char_start=0,
        document_char_end=100,
        token_count=20,
        text=f"parent text {parent_id}",
    )


def _child(child_id: str, parent_id: str, *, run_id: str = "run-1", user_id: str = "user-1") -> ChildChunk:
    return ChildChunk(
        id=child_id,
        parent_chunk_id=parent_id,
        ordinal=0,
        identity=_identity(run_id, user_id),
        parent_char_start=0,
        parent_char_end=20,
        heading_path=("Section",),
        page_start=1,
        page_end=1,
        token_count=4,
        dense_index_version="dense-v1",
        sparse_index_version="bm25-v1",
        text=f"child text {child_id}",
    )


def _dense(child_id: str, parent_id: str, rank: int, *, run_id: str = "run-1") -> DenseSearchHit:
    return DenseSearchHit(child_id, parent_id, run_id, 1 / rank, rank, 1, 1, 0, 20)


def _sparse(child_id: str, parent_id: str, rank: int, *, run_id: str = "run-1") -> SparseSearchHit:
    return SparseSearchHit(child_id, parent_id, run_id, 1 / rank, rank, "text", 1, 1, 0, 20)


def _retrieve(dense, sparse, parents, children):
    return reciprocal_rank_fusion(
        dense,
        sparse,
        parents={parent.id: parent for parent in parents},
        children={child.id: child for child in children},
        scope=AuthorizedRetrievalScope("user-1", "topic-1", frozenset({"run-1"})),
        dense_index_version="dense-v1",
        sparse_index_version="bm25-v1",
    )


def test_rrf_ranks_dense_only_sparse_only_and_shared_hits() -> None:
    parents = [_parent("parent-a"), _parent("parent-b"), _parent("parent-c")]
    children = [
        _child("dense-only", "parent-a"),
        _child("sparse-only", "parent-b"),
        _child("shared", "parent-c"),
    ]

    result = _retrieve(
        [_dense("dense-only", "parent-a", 1), _dense("shared", "parent-c", 2)],
        [_sparse("sparse-only", "parent-b", 1), _sparse("shared", "parent-c", 2)],
        parents,
        children,
    )

    assert result.parents[0].parent_chunk_id == "parent-c"
    assert {parent.parent_chunk_id for parent in result.parents} == {
        "parent-a",
        "parent-b",
        "parent-c",
    }
    assert result.parents[0].evidence[0].dense_rank == 2
    assert result.parents[0].evidence[0].sparse_rank == 2


def test_parent_score_uses_best_child_without_child_count_bias() -> None:
    parents = [_parent("parent-many"), _parent("parent-best")]
    children = [
        _child("many-1", "parent-many"),
        _child("many-2", "parent-many"),
        _child("best", "parent-best"),
    ]

    result = _retrieve(
        [
            _dense("best", "parent-best", 1),
            _dense("many-1", "parent-many", 2),
            _dense("many-2", "parent-many", 3),
        ],
        [],
        parents,
        children,
    )

    assert [parent.parent_chunk_id for parent in result.parents] == [
        "parent-best",
        "parent-many",
    ]
    assert len(result.parents[1].evidence) == 2
    assert result.parents[1].score == result.parents[1].evidence[0].rrf_score


def test_scope_and_index_versions_are_rechecked_before_parent_hydration() -> None:
    valid_parent = _parent("valid")
    old_parent = _parent("old", run_id="run-old")
    other_parent = _parent("other", user_id="user-2")
    valid_child = _child("valid-child", "valid")
    old_child = _child("old-child", "old", run_id="run-old")
    other_child = _child("other-child", "other", user_id="user-2")

    result = _retrieve(
        [
            _dense("old-child", "old", 1, run_id="run-old"),
            _dense("other-child", "other", 2),
            _dense("valid-child", "valid", 3),
        ],
        [],
        [valid_parent, old_parent, other_parent],
        [valid_child, old_child, other_child],
    )

    assert [parent.parent_chunk_id for parent in result.parents] == ["valid"]


def test_response_contains_stable_diagnostics_and_top_three_parents() -> None:
    parents = [_parent(f"parent-{index}") for index in range(4)]
    children = [_child(f"child-{index}", f"parent-{index}") for index in range(4)]

    result = _retrieve(
        [_dense(f"child-{index}", f"parent-{index}", index + 1) for index in range(4)],
        [],
        parents,
        children,
    )

    assert result.retrieval_version == RETRIEVAL_VERSION
    assert result.topic_id == "topic-1"
    assert result.active_run_ids == ("run-1",)
    assert result.dense_index_version == "dense-v1"
    assert result.sparse_index_version == "bm25-v1"
    assert len(result.parents) == 3