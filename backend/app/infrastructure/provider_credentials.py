from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Engine, select, update

from app.infrastructure.identity_tables import provider_credentials


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _normalize_provider(provider: str) -> str:
    return provider.strip().lower()


@dataclass(frozen=True)
class ProviderCredential:
    id: str
    user_id: str
    provider: str
    masked_secret: str
    version: int
    created_at: datetime
    updated_at: datetime


def _credential_from_row(row: Any) -> ProviderCredential:
    return ProviderCredential(
        id=row.id,
        user_id=row.user_id,
        provider=row.provider,
        masked_secret="*" * (row.secret_length - len(row.secret_hint)) + row.secret_hint,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class ProviderCredentialService:
    def __init__(self, engine: Engine, master_key: str) -> None:
        self.engine = engine
        try:
            self._cipher = Fernet(master_key.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid provider credentials master key") from exc

    def store(
        self,
        *,
        user_id: str,
        provider: str,
        secret: str,
        now: datetime | None = None,
    ) -> ProviderCredential:
        active_now = now or _utc_now()
        normalized_provider = _normalize_provider(provider)
        encrypted_secret = self._cipher.encrypt(secret.encode("utf-8")).decode("ascii")
        secret_hint = secret[-4:]

        with self.engine.begin() as connection:
            existing = (
                connection.execute(
                    select(provider_credentials).where(
                        provider_credentials.c.user_id == user_id,
                        provider_credentials.c.provider == normalized_provider,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is None:
                credential_id = str(uuid4())
                connection.execute(
                    provider_credentials.insert().values(
                        id=credential_id,
                        user_id=user_id,
                        provider=normalized_provider,
                        encrypted_secret=encrypted_secret,
                        secret_hint=secret_hint,
                        secret_length=len(secret),
                        version=1,
                        created_at=active_now,
                        updated_at=active_now,
                    )
                )
            else:
                credential_id = existing["id"]
                connection.execute(
                    update(provider_credentials)
                    .where(provider_credentials.c.id == credential_id)
                    .values(
                        encrypted_secret=encrypted_secret,
                        secret_hint=secret_hint,
                        secret_length=len(secret),
                        version=existing["version"] + 1,
                        updated_at=active_now,
                    )
                )
            row = connection.execute(
                select(provider_credentials).where(
                    provider_credentials.c.id == credential_id
                )
            ).one()

        return _credential_from_row(row)

    def list_masked(self, *, user_id: str) -> list[ProviderCredential]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(provider_credentials)
                .where(provider_credentials.c.user_id == user_id)
                .order_by(provider_credentials.c.provider)
            ).all()
        return [_credential_from_row(row) for row in rows]

    def reveal(self, *, user_id: str, provider: str) -> str | None:
        with self.engine.connect() as connection:
            encrypted_secret = connection.execute(
                select(provider_credentials.c.encrypted_secret).where(
                    provider_credentials.c.user_id == user_id,
                    provider_credentials.c.provider == _normalize_provider(provider),
                )
            ).scalar_one_or_none()
        if encrypted_secret is None:
            return None
        try:
            return self._cipher.decrypt(encrypted_secret.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("provider credential cannot be decrypted") from exc