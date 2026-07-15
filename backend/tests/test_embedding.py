from dataclasses import replace
from typing import Sequence

import pytest

from app.chunks import ChildChunk, ChunkIdentity
from app.embedding import (
    EmbeddingConsentSnapshot,
    EmbeddingFailure,
    EmbeddingProviderSpec,
    EmbeddingRouter,
    EmbeddingVector,
)
from app.infrastructure.embedding_http import (
    HttpsEmbeddingAdapter,
    validate_public_https_target,
)


def _spec(
    provider: str = "ollama-gateway",
    *,
    response_format: str = "ollama",
    base_url: str = "https://embed.example.test/v1",
) -> EmbeddingProviderSpec:
    return EmbeddingProviderSpec(
        provider=provider,
        credential_name=provider,
        base_url=base_url,
        model="embedding-model",
        model_identifier="embedding-model@sha256:locked",
        dimension=3,
        normalization="l2",
        distance_metric="cosine",
        response_format=response_format,
    )


def _child(index: int, *, run_id: str = "run-1") -> ChildChunk:
    text = f"chunk text {index}"
    return ChildChunk(
        id=f"child-{index:02d}",
        parent_chunk_id=f"parent-{index // 2}",
        ordinal=index,
        identity=ChunkIdentity(
            user_id="user-1",
            source_document_id="source-1",
            source_revision_id="revision-1",
            ingestion_run_id=run_id,
        ),
        parent_char_start=0,
        parent_char_end=len(text),
        heading_path=("Section",),
        page_start=1,
        page_end=1,
        token_count=3,
        dense_index_version="staging",
        sparse_index_version="bm25-v1",
        text=text,
    )


def _consent(provider: str, *, allowed: bool = True) -> EmbeddingConsentSnapshot:
    return EmbeddingConsentSnapshot(
        user_id="user-1",
        ingestion_run_id="run-1",
        provider=provider,
        provider_config_version=1,
        authorization_source="user_setting",
        authorization_version=2,
        allowed_data_categories=frozenset({"source_chunk_text"}),
        external_processing_allowed=allowed,
    )


def test_https_adapter_sends_only_whitelisted_text_and_model() -> None:
    captured: dict[str, object] = {}

    def send_json(url, headers, payload, timeout):
        captured.update(
            url=url, headers=dict(headers), payload=payload, timeout=timeout
        )
        return {"embeddings": [[1, 0, 0], [0, 1, 0]]}

    adapter = HttpsEmbeddingAdapter(
        _spec(),
        secret_resolver=lambda user_id, name: "secret-key",
        send_json=send_json,
        validate_target=lambda url: None,
    )

    vectors = adapter.embed([_child(0), _child(1)], consent=_consent(adapter.spec.provider))

    assert [vector.child_chunk_id for vector in vectors] == ["child-00", "child-01"]
    assert captured["url"] == "https://embed.example.test/v1/api/embed"
    assert captured["payload"] == {
        "model": "embedding-model",
        "input": ["chunk text 0", "chunk text 1"],
    }
    assert captured["headers"] == {"Authorization": "Bearer secret-key"}
    assert "secret-key" not in str(captured["payload"])
    assert "source-1" not in str(captured["payload"])


def test_adapter_rechecks_consent_and_user_credential_at_request_boundary() -> None:
    calls: list[object] = []
    adapter = HttpsEmbeddingAdapter(
        _spec(),
        secret_resolver=lambda user_id, name: calls.append((user_id, name)),
        send_json=lambda *args: calls.append(args),
        validate_target=lambda url: calls.append(url),
    )

    with pytest.raises(EmbeddingFailure) as error:
        adapter.embed([_child(0)], consent=_consent(adapter.spec.provider, allowed=False))

    assert error.value.code == "embedding_not_authorized"
    assert calls == []


def test_adapter_validates_dimension_and_openai_order() -> None:
    adapter = HttpsEmbeddingAdapter(
        _spec(provider="external-api", response_format="openai"),
        secret_resolver=lambda user_id, name: "key",
        send_json=lambda *args: {
            "data": [
                {"index": 1, "embedding": [0, 1, 0]},
                {"index": 0, "embedding": [1, 0, 0]},
            ]
        },
        validate_target=lambda url: None,
    )

    vectors = adapter.embed(
        [_child(0), _child(1)], consent=_consent("external-api")
    )

    assert vectors[0].values == (1.0, 0.0, 0.0)
    assert vectors[1].values == (0.0, 1.0, 0.0)


def test_public_target_validation_rejects_non_https_before_dns() -> None:
    with pytest.raises(EmbeddingFailure) as error:
        validate_public_https_target("http://127.0.0.1:11434")

    assert error.value.code == "embedding_target_invalid"


class _RecordingProvider:
    def __init__(
        self,
        spec: EmbeddingProviderSpec,
        *,
        failure: EmbeddingFailure | None = None,
    ) -> None:
        self.spec = spec
        self.failure = failure
        self.batches: list[tuple[str, ...]] = []

    def embed(
        self,
        chunks: Sequence[ChildChunk],
        *,
        consent: EmbeddingConsentSnapshot,
    ) -> tuple[EmbeddingVector, ...]:
        assert consent.provider == self.spec.provider
        self.batches.append(tuple(chunk.id for chunk in chunks))
        if self.failure is not None:
            raise self.failure
        return tuple(
            EmbeddingVector(chunk.id, (1.0, 0.0, 0.0)) for chunk in chunks
        )


def test_retryable_primary_failure_cleans_and_recomputes_entire_run_on_fallback() -> None:
    primary = _RecordingProvider(
        _spec(), failure=EmbeddingFailure("embedding_provider_unavailable", True)
    )
    fallback = _RecordingProvider(_spec(provider="external-api", response_format="openai"))
    cleanup_calls: list[tuple[str, str]] = []
    chunks = [_child(index) for index in range(40)]
    router = EmbeddingRouter(
        primary,
        fallback,
        cleanup_staging=lambda run_id, version: cleanup_calls.append(
            (run_id, version)
        ),
    )

    result = router.embed_run(
        chunks,
        chunking_version="parent-child-v1",
        consents={
            primary.spec.provider: _consent(primary.spec.provider),
            fallback.spec.provider: _consent(fallback.spec.provider),
        },
    )

    assert result.used_fallback is True
    assert result.provider == "external-api"
    assert [len(batch) for batch in fallback.batches] == [32, 8]
    assert [child_id for batch in fallback.batches for child_id in batch] == [
        chunk.id for chunk in chunks
    ]
    assert cleanup_calls == [
        ("run-1", primary.spec.index_version("parent-child-v1"))
    ]
    assert result.index_version != primary.spec.index_version("parent-child-v1")


def test_non_retryable_primary_failure_does_not_fallback() -> None:
    primary = _RecordingProvider(
        _spec(), failure=EmbeddingFailure("embedding_provider_rejected", False)
    )
    fallback = _RecordingProvider(_spec(provider="external-api", response_format="openai"))
    router = EmbeddingRouter(primary, fallback, cleanup_staging=lambda *args: None)

    with pytest.raises(EmbeddingFailure) as error:
        router.embed_run(
            [_child(0)],
            chunking_version="parent-child-v1",
            consents={
                primary.spec.provider: _consent(primary.spec.provider),
                fallback.spec.provider: _consent(fallback.spec.provider),
            },
        )

    assert error.value.code == "embedding_provider_rejected"
    assert fallback.batches == []


def test_provider_or_chunking_change_derives_a_distinct_index_version() -> None:
    primary = _spec()
    fallback = _spec(provider="external-api", response_format="openai")

    assert primary.index_version("parent-child-v1") != fallback.index_version(
        "parent-child-v1"
    )
    assert primary.index_version("parent-child-v1") != primary.index_version(
        "parent-child-v2"
    )
    assert primary.index_version("parent-child-v1") != replace(
        primary, dimension=4
    ).index_version("parent-child-v1")