from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.chunks import ChildChunk


@dataclass(frozen=True, slots=True)
class EmbeddingProviderSpec:
    provider: str
    credential_name: str
    base_url: str
    model: str
    model_identifier: str
    dimension: int
    normalization: str
    distance_metric: str
    response_format: str

    def __post_init__(self) -> None:
        if self.dimension < 1:
            raise ValueError("embedding dimension must be positive")
        if self.response_format not in {"ollama", "openai"}:
            raise ValueError("unsupported embedding response format")

    def index_version(self, chunking_version: str) -> str:
        material = "\x1f".join(
            (
                self.provider,
                self.base_url,
                self.model_identifier,
                str(self.dimension),
                self.normalization,
                self.distance_metric,
                chunking_version,
            )
        )
        digest = hashlib.sha256(material.encode()).hexdigest()[:24]
        return f"dense-{self.provider}-{digest}"


@dataclass(frozen=True, slots=True)
class EmbeddingConsentSnapshot:
    user_id: str
    ingestion_run_id: str
    provider: str
    provider_config_version: int
    authorization_source: str
    authorization_version: int
    allowed_data_categories: frozenset[str]
    external_processing_allowed: bool

    def allows(self, *, user_id: str, run_id: str, provider: str) -> bool:
        return (
            self.external_processing_allowed
            and self.user_id == user_id
            and self.ingestion_run_id == run_id
            and self.provider == provider
            and "source_chunk_text" in self.allowed_data_categories
        )


@dataclass(frozen=True, slots=True)
class EmbeddingVector:
    child_chunk_id: str
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingRunResult:
    provider: str
    model_identifier: str
    index_version: str
    vectors: tuple[EmbeddingVector, ...]
    used_fallback: bool


@dataclass(frozen=True, slots=True)
class EmbeddingFailure(Exception):
    code: str
    retryable: bool


class EmbeddingProvider(Protocol):
    spec: EmbeddingProviderSpec

    def embed(
        self,
        chunks: Sequence[ChildChunk],
        *,
        consent: EmbeddingConsentSnapshot,
    ) -> tuple[EmbeddingVector, ...]: ...


class EmbeddingRouter:
    def __init__(
        self,
        primary: EmbeddingProvider,
        fallback: EmbeddingProvider | None,
        *,
        cleanup_staging: Callable[[str, str], None],
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._cleanup_staging = cleanup_staging

    def embed_run(
        self,
        chunks: Sequence[ChildChunk],
        *,
        chunking_version: str,
        consents: Mapping[str, EmbeddingConsentSnapshot],
    ) -> EmbeddingRunResult:
        if not chunks:
            raise EmbeddingFailure("embedding_chunks_required", False)
        run_ids = {chunk.identity.ingestion_run_id for chunk in chunks}
        user_ids = {chunk.identity.user_id for chunk in chunks}
        if len(run_ids) != 1 or len(user_ids) != 1:
            raise EmbeddingFailure("embedding_run_mixed_identity", False)
        run_id = next(iter(run_ids))
        primary_version = self._primary.spec.index_version(chunking_version)
        try:
            vectors = self._embed_all(
                self._primary,
                chunks,
                consent=consents.get(self._primary.spec.provider),
            )
            return EmbeddingRunResult(
                provider=self._primary.spec.provider,
                model_identifier=self._primary.spec.model_identifier,
                index_version=primary_version,
                vectors=vectors,
                used_fallback=False,
            )
        except EmbeddingFailure as primary_error:
            self._cleanup_staging(run_id, primary_version)
            if self._fallback is None or not primary_error.retryable:
                raise primary_error

        fallback_version = self._fallback.spec.index_version(chunking_version)
        try:
            vectors = self._embed_all(
                self._fallback,
                chunks,
                consent=consents.get(self._fallback.spec.provider),
            )
        except EmbeddingFailure:
            self._cleanup_staging(run_id, fallback_version)
            raise
        return EmbeddingRunResult(
            provider=self._fallback.spec.provider,
            model_identifier=self._fallback.spec.model_identifier,
            index_version=fallback_version,
            vectors=vectors,
            used_fallback=True,
        )

    @staticmethod
    def _embed_all(
        provider: EmbeddingProvider,
        chunks: Sequence[ChildChunk],
        *,
        consent: EmbeddingConsentSnapshot | None,
    ) -> tuple[EmbeddingVector, ...]:
        if consent is None:
            raise EmbeddingFailure("embedding_not_authorized", False)
        vectors: list[EmbeddingVector] = []
        for start in range(0, len(chunks), 32):
            vectors.extend(provider.embed(chunks[start : start + 32], consent=consent))
        if len(vectors) != len(chunks):
            raise EmbeddingFailure("embedding_count_mismatch", False)
        expected_ids = [chunk.id for chunk in chunks]
        if [vector.child_chunk_id for vector in vectors] != expected_ids:
            raise EmbeddingFailure("embedding_reference_mismatch", False)
        if any(len(vector.values) != provider.spec.dimension for vector in vectors):
            raise EmbeddingFailure("embedding_dimension_mismatch", False)
        return tuple(vectors)