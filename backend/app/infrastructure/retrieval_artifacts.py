from __future__ import annotations

from app.chunks import ChildChunk, ParentChunk
from app.infrastructure.chunk_store import ChunkStore
from app.infrastructure.sparse_index_store import SparseIndexStore
from app.sparse_index import SparseIndexSnapshot


class FileRetrievalArtifacts:
    def __init__(
        self,
        *,
        chunks: ChunkStore,
        sparse_indexes: SparseIndexStore,
    ) -> None:
        self._chunks = chunks
        self._sparse_indexes = sparse_indexes

    def load_chunks(
        self, run_ids: frozenset[str]
    ) -> tuple[tuple[ParentChunk, ...], tuple[ChildChunk, ...]]:
        chunk_sets = tuple(self._chunks.read(run_id) for run_id in sorted(run_ids))
        return (
            tuple(parent for chunks in chunk_sets for parent in chunks.parents),
            tuple(child for chunks in chunk_sets for child in chunks.children),
        )

    def load_sparse_index(self, index_version: str) -> SparseIndexSnapshot:
        return self._sparse_indexes.read(index_version)