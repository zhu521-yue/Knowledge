from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from app.chunks import ChildChunk, ParentChunk
from app.dense_index import DenseIndexService
from app.infrastructure.retrieval_scope import RetrievalScopeResolver
from app.retrieval import RetrievalResult, reciprocal_rank_fusion
from app.sparse_index import SparseIndexSnapshot, search_sparse_index


class QueryEmbeddingPort(Protocol):
    def embed_query(
        self,
        query: str,
        *,
        user_id: str,
        topic_id: str,
        index_version: str,
        chunking_version: str,
    ) -> Sequence[float]: ...


class RetrievalArtifactPort(Protocol):
    def load_chunks(
        self, run_ids: frozenset[str]
    ) -> tuple[tuple[ParentChunk, ...], tuple[ChildChunk, ...]]: ...

    def load_sparse_index(self, index_version: str) -> SparseIndexSnapshot: ...


@dataclass(frozen=True, slots=True)
class RetrievalUseCaseError(Exception):
    code: str


class RetrieveTopicParents:
    def __init__(
        self,
        *,
        scope_resolver: RetrievalScopeResolver,
        query_embedding: QueryEmbeddingPort,
        dense_index: DenseIndexService,
        artifacts: RetrievalArtifactPort,
    ) -> None:
        self._scope_resolver = scope_resolver
        self._query_embedding = query_embedding
        self._dense_index = dense_index
        self._artifacts = artifacts

    def execute(self, *, user_id: str, topic_id: str, query: str) -> RetrievalResult:
        normalized_query = query.strip()
        if not normalized_query:
            raise RetrievalUseCaseError("retrieval_query_required")
        resolved = self._scope_resolver.resolve(user_id=user_id, topic_id=topic_id)
        if resolved is None:
            raise RetrievalUseCaseError("topic_not_found")
        if not resolved.scope.active_run_ids or resolved.versions is None:
            return RetrievalResult(
                retrieval_version="retrieval-v1",
                dense_index_version="",
                sparse_index_version="",
                topic_id=topic_id,
                active_run_ids=(),
                parents=(),
            )

        parents, children = self._artifacts.load_chunks(resolved.scope.active_run_ids)
        sparse_snapshot = self._artifacts.load_sparse_index(resolved.versions.sparse)
        if resolved.versions.dense:
            query_vector = self._query_embedding.embed_query(
                normalized_query,
                user_id=user_id,
                topic_id=topic_id,
                index_version=resolved.versions.dense,
                chunking_version=resolved.versions.chunking,
            )
            dense_hits = self._dense_index.search(
                resolved.versions.dense,
                query_vector,
                scope=resolved.scope,
                limit=20,
            )
        else:
            dense_hits = ()
        sparse_hits = search_sparse_index(
            sparse_snapshot,
            normalized_query,
            scope=resolved.scope,
            limit=20,
        )
        return reciprocal_rank_fusion(
            dense_hits,
            sparse_hits,
            parents={parent.id: parent for parent in parents},
            children={child.id: child for child in children},
            scope=resolved.scope,
            dense_index_version=resolved.versions.dense,
            sparse_index_version=resolved.versions.sparse,
        )