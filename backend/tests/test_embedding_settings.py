from sqlalchemy import create_engine

from app.embedding import EmbeddingFailure, EmbeddingProviderSpec
from app.infrastructure.embedding_query import AuthorizedQueryEmbedding
from app.infrastructure.embedding_settings import EmbeddingSettingsService
from app.infrastructure.identity import IdentityService
from app.infrastructure.identity_tables import identity_metadata
from app.infrastructure.provider_credentials import ProviderCredentialService

MASTER_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def _services():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    identity_metadata.create_all(engine)
    user = IdentityService(engine).bootstrap_admin(
        email="admin@example.test",
        password="correct horse battery staple",
        display_name="Admin",
    )
    settings = EmbeddingSettingsService(engine)
    credentials = ProviderCredentialService(engine, MASTER_KEY)
    return user, settings, credentials


def _spec() -> EmbeddingProviderSpec:
    return EmbeddingProviderSpec(
        provider="ollama-gateway",
        credential_name="ollama-gateway",
        base_url="https://embed.example.test",
        model="qwen3-embedding:4b",
        model_identifier="qwen3-embedding:4b@locked",
        dimension=3,
        normalization="l2",
        distance_metric="cosine",
        response_format="ollama",
    )


def test_query_embedding_rechecks_current_consent_credential_and_index_version() -> None:
    user, settings, credentials = _services()
    spec = _spec()
    configured = settings.put(user_id=user.id, enabled=True, spec=spec)
    credentials.store(user_id=user.id, provider="ollama-gateway", secret="secret")
    requests: list[dict[str, object]] = []

    def send(url, headers, payload, timeout):
        del url, headers, timeout
        requests.append(payload)
        return {"embeddings": [[1, 0, 0]]}

    adapter = AuthorizedQueryEmbedding(
        settings,
        credentials,
        send_json=send,
        validate_target=lambda url: None,
    )
    index_version = spec.index_version("parent-child-v1")

    assert adapter.embed_query(
        "RRF 是什么",
        user_id=user.id,
        topic_id="topic-1",
        index_version=index_version,
        chunking_version="parent-child-v1",
    ) == (1.0, 0.0, 0.0)
    assert requests == [{"model": spec.model, "input": ["RRF 是什么"]}]
    assert configured.version == 1

    settings.put(user_id=user.id, enabled=False, spec=spec)
    try:
        adapter.embed_query(
            "不得发送",
            user_id=user.id,
            topic_id="topic-1",
            index_version=index_version,
            chunking_version="parent-child-v1",
        )
    except EmbeddingFailure as error:
        assert error.code == "embedding_not_authorized"
    else:
        raise AssertionError("disabled consent must reject query embedding")
    assert len(requests) == 1


def test_query_embedding_rejects_index_version_drift_before_network() -> None:
    user, settings, credentials = _services()
    settings.put(user_id=user.id, enabled=True, spec=_spec())
    credentials.store(user_id=user.id, provider="ollama-gateway", secret="secret")
    adapter = AuthorizedQueryEmbedding(
        settings,
        credentials,
        send_json=lambda *args: {"embeddings": [[1, 0, 0]]},
        validate_target=lambda url: None,
    )

    try:
        adapter.embed_query(
            "query",
            user_id=user.id,
            topic_id="topic-1",
            index_version="dense-stale",
            chunking_version="parent-child-v1",
        )
    except EmbeddingFailure as error:
        assert error.code == "embedding_index_version_mismatch"
    else:
        raise AssertionError("index version drift must be rejected")