from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.embedding import EmbeddingFailure
from app.infrastructure.embedding_http import _send_json, validate_public_https_target
from app.infrastructure.embedding_settings import EmbeddingSettingsService
from app.infrastructure.provider_credentials import ProviderCredentialService

JsonSender = Callable[[str, Mapping[str, str], dict[str, object], float], dict[str, Any]]
TargetValidator = Callable[[str], None]


class AuthorizedQueryEmbedding:
    def __init__(
        self,
        settings: EmbeddingSettingsService,
        credentials: ProviderCredentialService,
        *,
        send_json: JsonSender = _send_json,
        validate_target: TargetValidator = validate_public_https_target,
        timeout_seconds: float = 30,
    ) -> None:
        self._settings = settings
        self._credentials = credentials
        self._send_json = send_json
        self._validate_target = validate_target
        self._timeout_seconds = timeout_seconds

    def embed_query(
        self,
        query: str,
        *,
        user_id: str,
        topic_id: str,
        index_version: str,
        chunking_version: str,
    ) -> tuple[float, ...]:
        del topic_id
        current = self._settings.get(user_id=user_id)
        if current is None or not current.enabled:
            raise EmbeddingFailure("embedding_not_authorized", False)
        spec = current.spec
        if spec.index_version(chunking_version) != index_version:
            raise EmbeddingFailure("embedding_index_version_mismatch", False)
        self._validate_target(spec.base_url)
        secret = self._credentials.reveal(
            user_id=user_id,
            provider=spec.credential_name,
        )
        if not secret:
            raise EmbeddingFailure("embedding_credential_missing", False)
        endpoint = (
            spec.base_url.rstrip("/") + "/api/embed"
            if spec.response_format == "ollama"
            else spec.base_url.rstrip("/") + "/embeddings"
        )
        payload: dict[str, object] = {"model": spec.model, "input": [query]}
        response = self._send_json(
            endpoint,
            {"Authorization": f"Bearer {secret}"},
            payload,
            self._timeout_seconds,
        )
        vector = _first_vector(response, spec.response_format)
        if len(vector) != spec.dimension:
            raise EmbeddingFailure("embedding_dimension_mismatch", False)
        return vector


def _first_vector(response: dict[str, Any], response_format: str) -> tuple[float, ...]:
    if response_format == "ollama":
        vectors = response.get("embeddings")
        raw = vectors[0] if isinstance(vectors, list) and vectors else None
    else:
        data = response.get("data")
        first = data[0] if isinstance(data, list) and data else None
        raw = first.get("embedding") if isinstance(first, dict) else None
    if not isinstance(raw, list) or any(
        not isinstance(value, (int, float)) for value in raw
    ):
        raise EmbeddingFailure("embedding_response_invalid", False)
    return tuple(float(value) for value in raw)