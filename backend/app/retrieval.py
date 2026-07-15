from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from app.chunks import ChildChunk, ParentChunk
from app.dense_index import DenseSearchHit
from app.retrieval_scope import AuthorizedRetrievalScope
from app.sparse_index import SparseSearchHit

RETRIEVAL_VERSION = "retrieval-v1"
RRF_K = 60


@dataclass(frozen=True, slots=True)
class RankedChildEvidence:
    child_chunk_id: str
    text: str
    page_start: int
    page_end: int
    parent_char_start: int
    parent_char_end: int
    dense_rank: int | None
    sparse_rank: int | None
    rrf_score: float


@dataclass(frozen=True, slots=True)
class RetrievedParent:
    parent_chunk_id: str
    source_document_id: str
    source_revision_id: str
    ingestion_run_id: str
    heading_path: tuple[str, ...]
    page_start: int
    page_end: int
    text: str
    score: float
    evidence: tuple[RankedChildEvidence, ...]


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    retrieval_version: str
    dense_index_version: str
    sparse_index_version: str
    topic_id: str
    active_run_ids: tuple[str, ...]
    parents: tuple[RetrievedParent, ...]


@dataclass(slots=True)
class _Ranks:
    dense: int | None = None
    sparse: int | None = None


def reciprocal_rank_fusion(
    dense_hits: Sequence[DenseSearchHit],
    sparse_hits: Sequence[SparseSearchHit],
    *,
    parents: Mapping[str, ParentChunk],
    children: Mapping[str, ChildChunk],
    scope: AuthorizedRetrievalScope,
    dense_index_version: str,
    sparse_index_version: str,
    parent_limit: int = 3,
    evidence_limit: int = 2,
    rrf_k: int = RRF_K,
) -> RetrievalResult:
    if parent_limit < 1 or evidence_limit < 1 or rrf_k < 1:
        raise ValueError("retrieval limits and rrf_k must be positive")

    ranks: dict[str, _Ranks] = {}
    _collect_ranks(ranks, dense_hits, channel="dense")
    _collect_ranks(ranks, sparse_hits, channel="sparse")

    evidence_by_parent: dict[str, list[RankedChildEvidence]] = {}
    for child_id, channel_ranks in ranks.items():
        child = children.get(child_id)
        if child is None or not scope.allows(
            user_id=child.identity.user_id,
            ingestion_run_id=child.identity.ingestion_run_id,
        ):
            continue
        if child.dense_index_version != dense_index_version:
            continue
        if child.sparse_index_version != sparse_index_version:
            continue
        score = _rrf_score(channel_ranks, rrf_k)
        evidence_by_parent.setdefault(child.parent_chunk_id, []).append(
            RankedChildEvidence(
                child_chunk_id=child.id,
                text=child.text,
                page_start=child.page_start,
                page_end=child.page_end,
                parent_char_start=child.parent_char_start,
                parent_char_end=child.parent_char_end,
                dense_rank=channel_ranks.dense,
                sparse_rank=channel_ranks.sparse,
                rrf_score=score,
            )
        )

    ranked_parents: list[RetrievedParent] = []
    for parent_id, evidence in evidence_by_parent.items():
        parent = parents.get(parent_id)
        if parent is None or not scope.allows(
            user_id=parent.identity.user_id,
            ingestion_run_id=parent.identity.ingestion_run_id,
        ):
            continue
        ordered_evidence = tuple(
            sorted(evidence, key=lambda item: (-item.rrf_score, item.child_chunk_id))[
                :evidence_limit
            ]
        )
        ranked_parents.append(
            RetrievedParent(
                parent_chunk_id=parent.id,
                source_document_id=parent.identity.source_document_id,
                source_revision_id=parent.identity.source_revision_id,
                ingestion_run_id=parent.identity.ingestion_run_id,
                heading_path=parent.heading_path,
                page_start=parent.page_start,
                page_end=parent.page_end,
                text=parent.text,
                score=ordered_evidence[0].rrf_score,
                evidence=ordered_evidence,
            )
        )

    result_parents = tuple(
        sorted(ranked_parents, key=lambda item: (-item.score, item.parent_chunk_id))[
            :parent_limit
        ]
    )
    return RetrievalResult(
        retrieval_version=RETRIEVAL_VERSION,
        dense_index_version=dense_index_version,
        sparse_index_version=sparse_index_version,
        topic_id=scope.topic_id,
        active_run_ids=tuple(sorted(scope.active_run_ids)),
        parents=result_parents,
    )


def _collect_ranks(
    ranks: dict[str, _Ranks],
    hits: Sequence[DenseSearchHit] | Sequence[SparseSearchHit],
    *,
    channel: str,
) -> None:
    seen: set[str] = set()
    for hit in sorted(hits, key=lambda item: (item.rank, item.child_chunk_id)):
        if hit.child_chunk_id in seen:
            continue
        seen.add(hit.child_chunk_id)
        value = ranks.setdefault(hit.child_chunk_id, _Ranks())
        if channel == "dense":
            value.dense = hit.rank
        else:
            value.sparse = hit.rank


def _rrf_score(ranks: _Ranks, rrf_k: int) -> float:
    return sum(
        1 / (rrf_k + rank)
        for rank in (ranks.dense, ranks.sparse)
        if rank is not None
    )