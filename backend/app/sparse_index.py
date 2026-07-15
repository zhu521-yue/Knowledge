from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from app.chunks import ChildChunk
from app.retrieval_scope import AuthorizedRetrievalScope

SPARSE_INDEX_SCHEMA_VERSION = "1"
_TERM_PATTERN = re.compile(
    r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\W\d_]+", re.UNICODE
)


@dataclass(frozen=True, slots=True)
class SparseIndexDocument:
    child_chunk_id: str
    parent_chunk_id: str
    user_id: str
    source_document_id: str
    source_revision_id: str
    ingestion_run_id: str
    sparse_index_version: str
    page_start: int
    page_end: int
    parent_char_start: int
    parent_char_end: int
    text: str
    term_frequencies: dict[str, int]
    document_length: int

    @classmethod
    def from_child(cls, child: ChildChunk) -> SparseIndexDocument:
        terms = tokenize(child.text)
        return cls(
            child_chunk_id=child.id,
            parent_chunk_id=child.parent_chunk_id,
            user_id=child.identity.user_id,
            source_document_id=child.identity.source_document_id,
            source_revision_id=child.identity.source_revision_id,
            ingestion_run_id=child.identity.ingestion_run_id,
            sparse_index_version=child.sparse_index_version,
            page_start=child.page_start,
            page_end=child.page_end,
            parent_char_start=child.parent_char_start,
            parent_char_end=child.parent_char_end,
            text=child.text,
            term_frequencies=dict(Counter(terms)),
            document_length=len(terms),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "child_chunk_id": self.child_chunk_id,
            "parent_chunk_id": self.parent_chunk_id,
            "user_id": self.user_id,
            "source_document_id": self.source_document_id,
            "source_revision_id": self.source_revision_id,
            "ingestion_run_id": self.ingestion_run_id,
            "sparse_index_version": self.sparse_index_version,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "parent_char_start": self.parent_char_start,
            "parent_char_end": self.parent_char_end,
            "text": self.text,
            "term_frequencies": self.term_frequencies,
            "document_length": self.document_length,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> SparseIndexDocument:
        frequencies = value["term_frequencies"]
        if not isinstance(frequencies, dict):
            raise ValueError("invalid term frequencies")
        return cls(
            child_chunk_id=str(value["child_chunk_id"]),
            parent_chunk_id=str(value["parent_chunk_id"]),
            user_id=str(value["user_id"]),
            source_document_id=str(value["source_document_id"]),
            source_revision_id=str(value["source_revision_id"]),
            ingestion_run_id=str(value["ingestion_run_id"]),
            sparse_index_version=str(value["sparse_index_version"]),
            page_start=int(value["page_start"]),
            page_end=int(value["page_end"]),
            parent_char_start=int(value["parent_char_start"]),
            parent_char_end=int(value["parent_char_end"]),
            text=str(value["text"]),
            term_frequencies={str(k): int(v) for k, v in frequencies.items()},
            document_length=int(value["document_length"]),
        )


@dataclass(frozen=True, slots=True)
class SparseIndexSnapshot:
    index_version: str
    documents: tuple[SparseIndexDocument, ...]
    schema_version: str = SPARSE_INDEX_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if any(
            document.sparse_index_version != self.index_version
            for document in self.documents
        ):
            raise ValueError("sparse index version mismatch")
        ids = [document.child_chunk_id for document in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate child chunk id")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "index_version": self.index_version,
            "documents": [document.to_dict() for document in self.documents],
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> SparseIndexSnapshot:
        if value.get("schema_version") != SPARSE_INDEX_SCHEMA_VERSION:
            raise ValueError("unsupported sparse index schema")
        documents = value["documents"]
        if not isinstance(documents, list) or any(
            not isinstance(document, dict) for document in documents
        ):
            raise ValueError("invalid sparse index documents")
        return cls(
            index_version=str(value["index_version"]),
            documents=tuple(
                SparseIndexDocument.from_dict(document) for document in documents
            ),
        )


@dataclass(frozen=True, slots=True)
class SparseSearchHit:
    child_chunk_id: str
    parent_chunk_id: str
    ingestion_run_id: str
    score: float
    rank: int
    text: str
    page_start: int
    page_end: int
    parent_char_start: int
    parent_char_end: int


def tokenize(text: str) -> tuple[str, ...]:
    return tuple(match.group(0).casefold() for match in _TERM_PATTERN.finditer(text))


def build_sparse_index(
    children: Iterable[ChildChunk], *, index_version: str
) -> SparseIndexSnapshot:
    documents_by_id: dict[str, SparseIndexDocument] = {}
    for child in children:
        document = SparseIndexDocument.from_child(child)
        existing = documents_by_id.get(document.child_chunk_id)
        if existing is not None and existing != document:
            raise ValueError("conflicting child chunk id")
        documents_by_id[document.child_chunk_id] = document
    return SparseIndexSnapshot(
        index_version=index_version,
        documents=tuple(
            documents_by_id[child_id] for child_id in sorted(documents_by_id)
        ),
    )


def delete_runs(
    snapshot: SparseIndexSnapshot, run_ids: frozenset[str]
) -> SparseIndexSnapshot:
    return SparseIndexSnapshot(
        index_version=snapshot.index_version,
        documents=tuple(
            document
            for document in snapshot.documents
            if document.ingestion_run_id not in run_ids
        ),
    )


def search_sparse_index(
    snapshot: SparseIndexSnapshot,
    query: str,
    *,
    scope: AuthorizedRetrievalScope,
    limit: int = 20,
    k1: float = 1.2,
    b: float = 0.75,
) -> tuple[SparseSearchHit, ...]:
    if limit < 1:
        return ()
    query_terms = tuple(dict.fromkeys(tokenize(query)))
    documents = tuple(
        document
        for document in snapshot.documents
        if scope.allows(
            user_id=document.user_id,
            ingestion_run_id=document.ingestion_run_id,
        )
        and document.sparse_index_version == snapshot.index_version
    )
    if not query_terms or not documents:
        return ()
    average_length = sum(document.document_length for document in documents) / len(
        documents
    )
    document_frequencies = {
        term: sum(term in document.term_frequencies for document in documents)
        for term in query_terms
    }
    scored = [
        (document, _bm25_score(document, query_terms, document_frequencies, len(documents), average_length, k1, b))
        for document in documents
    ]
    ranked = sorted(
        ((document, score) for document, score in scored if score > 0),
        key=lambda item: (-item[1], item[0].child_chunk_id),
    )[:limit]
    return tuple(
        SparseSearchHit(
            child_chunk_id=document.child_chunk_id,
            parent_chunk_id=document.parent_chunk_id,
            ingestion_run_id=document.ingestion_run_id,
            score=score,
            rank=rank,
            text=document.text,
            page_start=document.page_start,
            page_end=document.page_end,
            parent_char_start=document.parent_char_start,
            parent_char_end=document.parent_char_end,
        )
        for rank, (document, score) in enumerate(ranked, start=1)
    )


def _bm25_score(
    document: SparseIndexDocument,
    query_terms: tuple[str, ...],
    document_frequencies: dict[str, int],
    document_count: int,
    average_length: float,
    k1: float,
    b: float,
) -> float:
    score = 0.0
    for term in query_terms:
        frequency = document.term_frequencies.get(term, 0)
        if not frequency:
            continue
        inverse_document_frequency = math.log(
            1 + (document_count - document_frequencies[term] + 0.5) / (document_frequencies[term] + 0.5)
        )
        normalization = frequency + k1 * (
            1 - b + b * document.document_length / average_length
        )
        score += inverse_document_frequency * frequency * (k1 + 1) / normalization
    return score