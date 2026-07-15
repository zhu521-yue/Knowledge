from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.chunks import ChildChunk
from app.embedding import (
    EmbeddingConsentSnapshot,
    EmbeddingFailure,
    EmbeddingProviderSpec,
    EmbeddingVector,
)

JsonSender = Callable[[str, Mapping[str, str], dict[str, object], float], dict[str, Any]]
SecretResolver = Callable[[str, str], str | None]
TargetValidator = Callable[[str], None]


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        raise EmbeddingFailure("embedding_redirect_rejected", False)


def validate_public_https_target(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username:
        raise EmbeddingFailure("embedding_target_invalid", False)
    port = parsed.port or 443
    try:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(
                parsed.hostname, port, type=socket.SOCK_STREAM
            )
        }
    except OSError as exc:
        raise EmbeddingFailure("embedding_target_unresolved", True) from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise EmbeddingFailure("embedding_target_not_public", False)


def _send_json(
    url: str,
    headers: Mapping[str, str],
    payload: dict[str, object],
    timeout: float,
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with build_opener(_RejectRedirects()).open(request, timeout=timeout) as response:
            if response.status != 200:
                raise EmbeddingFailure(
                    "embedding_provider_unavailable", response.status >= 500
                )
            value = json.loads(response.read(16 * 1024 * 1024).decode())
    except EmbeddingFailure:
        raise
    except HTTPError as exc:
        retryable = exc.code == 429 or exc.code >= 500
        code = (
            "embedding_provider_retryable"
            if retryable
            else "embedding_provider_rejected"
        )
        raise EmbeddingFailure(code, retryable) from exc
    except (TimeoutError, URLError, OSError) as exc:
        raise EmbeddingFailure("embedding_provider_unavailable", True) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EmbeddingFailure("embedding_response_invalid", False) from exc
    if not isinstance(value, dict):
        raise EmbeddingFailure("embedding_response_invalid", False)
    return value


class HttpsEmbeddingAdapter:
    def __init__(
        self,
        spec: EmbeddingProviderSpec,
        *,
        secret_resolver: SecretResolver,
        send_json: JsonSender = _send_json,
        validate_target: TargetValidator = validate_public_https_target,
        timeout_seconds: float = 30,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("embedding timeout must be positive")
        self.spec = spec
        self._secret_resolver = secret_resolver
        self._send_json = send_json
        self._validate_target = validate_target
        self._timeout_seconds = timeout_seconds

    def embed(
        self,
        chunks: Sequence[ChildChunk],
        *,
        consent: EmbeddingConsentSnapshot,
    ) -> tuple[EmbeddingVector, ...]:
        if not chunks:
            return ()
        user_ids = {chunk.identity.user_id for chunk in chunks}
        run_ids = {chunk.identity.ingestion_run_id for chunk in chunks}
        if len(user_ids) != 1 or len(run_ids) != 1:
            raise EmbeddingFailure("embedding_batch_mixed_identity", False)
        user_id = next(iter(user_ids))
        run_id = next(iter(run_ids))
        if not consent.allows(
            user_id=user_id,
            run_id=run_id,
            provider=self.spec.provider,
        ):
            raise EmbeddingFailure("embedding_not_authorized", False)
        self._validate_target(self.spec.base_url)
        secret = self._secret_resolver(user_id, self.spec.credential_name)
        if not secret:
            raise EmbeddingFailure("embedding_credential_missing", False)
        endpoint, payload = self._request_payload(chunks)
        response = self._send_json(
            endpoint,
            {"Authorization": f"Bearer {secret}"},
            payload,
            self._timeout_seconds,
        )
        vectors = self._response_vectors(response)
        if len(vectors) != len(chunks):
            raise EmbeddingFailure("embedding_count_mismatch", False)
        return tuple(
            EmbeddingVector(child_chunk_id=chunk.id, values=vector)
            for chunk, vector in zip(chunks, vectors, strict=True)
        )

    def _request_payload(
        self, chunks: Sequence[ChildChunk]
    ) -> tuple[str, dict[str, object]]:
        texts = [chunk.text for chunk in chunks]
        if self.spec.response_format == "ollama":
            return self.spec.base_url.rstrip("/") + "/api/embed", {
                "model": self.spec.model,
                "input": texts,
            }
        return self.spec.base_url.rstrip("/") + "/embeddings", {
            "model": self.spec.model,
            "input": texts,
        }

    def _response_vectors(
        self, response: dict[str, Any]
    ) -> tuple[tuple[float, ...], ...]:
        if self.spec.response_format == "ollama":
            raw_vectors = response.get("embeddings")
        else:
            data = response.get("data")
            if not isinstance(data, list):
                raise EmbeddingFailure("embedding_response_invalid", False)
            ordered = sorted(
                data,
                key=lambda item: item.get("index", -1) if isinstance(item, dict) else -1,
            )
            raw_vectors = [
                item.get("embedding") if isinstance(item, dict) else None
                for item in ordered
            ]
        if not isinstance(raw_vectors, list):
            raise EmbeddingFailure("embedding_response_invalid", False)
        vectors: list[tuple[float, ...]] = []
        for raw_vector in raw_vectors:
            if not isinstance(raw_vector, list) or any(
                not isinstance(value, (int, float)) for value in raw_vector
            ):
                raise EmbeddingFailure("embedding_response_invalid", False)
            vector = tuple(float(value) for value in raw_vector)
            if len(vector) != self.spec.dimension:
                raise EmbeddingFailure("embedding_dimension_mismatch", False)
            vectors.append(vector)
        return tuple(vectors)