from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, select

from app.embedding import EmbeddingProviderSpec
from app.infrastructure.identity_tables import embedding_settings


@dataclass(frozen=True, slots=True)
class UserEmbeddingSettings:
    user_id: str
    enabled: bool
    spec: EmbeddingProviderSpec
    version: int
    created_at: datetime
    updated_at: datetime


def _from_row(row: Any) -> UserEmbeddingSettings:
    return UserEmbeddingSettings(
        user_id=row.user_id,
        enabled=row.enabled,
        spec=EmbeddingProviderSpec(
            provider=row.provider,
            credential_name=row.credential_name,
            base_url=row.base_url,
            model=row.model,
            model_identifier=row.model_identifier,
            dimension=row.dimension,
            normalization=row.normalization,
            distance_metric=row.distance_metric,
            response_format=row.response_format,
        ),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class EmbeddingSettingsService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, *, user_id: str) -> UserEmbeddingSettings | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(embedding_settings).where(embedding_settings.c.user_id == user_id)
            ).one_or_none()
        return _from_row(row) if row is not None else None

    def put(
        self,
        *,
        user_id: str,
        enabled: bool,
        spec: EmbeddingProviderSpec,
        now: datetime | None = None,
    ) -> UserEmbeddingSettings:
        active_now = now or datetime.now(UTC).replace(microsecond=0)
        current = self.get(user_id=user_id)
        values = {
            "enabled": enabled,
            "provider": spec.provider,
            "credential_name": spec.credential_name,
            "base_url": spec.base_url,
            "model": spec.model,
            "model_identifier": spec.model_identifier,
            "dimension": spec.dimension,
            "response_format": spec.response_format,
            "normalization": spec.normalization,
            "distance_metric": spec.distance_metric,
            "version": 1 if current is None else current.version + 1,
            "updated_at": active_now,
        }
        with self._engine.begin() as connection:
            if current is None:
                connection.execute(
                    embedding_settings.insert().values(
                        user_id=user_id,
                        created_at=active_now,
                        **values,
                    )
                )
            else:
                connection.execute(
                    embedding_settings.update()
                    .where(embedding_settings.c.user_id == user_id)
                    .values(**values)
                )
            row = connection.execute(
                select(embedding_settings).where(embedding_settings.c.user_id == user_id)
            ).one()
        return _from_row(row)