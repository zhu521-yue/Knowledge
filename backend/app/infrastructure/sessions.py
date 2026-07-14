from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, and_, select, update

from app.infrastructure.identity import IdentityUser, _active_utc_second, _stored_utc_second
from app.infrastructure.identity_tables import user_sessions, users

DEFAULT_SESSION_TTL = timedelta(hours=12)


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CreatedSession:
    id: str
    user_id: str
    token: str
    token_hash: str
    expires_at: datetime


@dataclass(frozen=True)
class StoredSession:
    id: str
    user_id: str
    token_hash: str
    expires_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True)
class ResolvedSession:
    session: StoredSession
    user: IdentityUser


def _session_from_row(row: Any) -> StoredSession:
    return StoredSession(
        id=row.id,
        user_id=row.user_id,
        token_hash=row.token_hash,
        expires_at=_stored_utc_second(row.expires_at),
        last_seen_at=_stored_utc_second(row.last_seen_at),
        revoked_at=_stored_utc_second(row.revoked_at) if row.revoked_at else None,
    )


def _session_from_mapping(row: Any) -> StoredSession:
    return StoredSession(
        id=row["id"],
        user_id=row["user_id"],
        token_hash=row["token_hash"],
        expires_at=_stored_utc_second(row["expires_at"]),
        last_seen_at=_stored_utc_second(row["last_seen_at"]),
        revoked_at=_stored_utc_second(row["revoked_at"]) if row["revoked_at"] else None,
    )


def _user_from_mapping(row: Any) -> IdentityUser:
    return IdentityUser(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SessionService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_session(
        self,
        *,
        user_id: str,
        ttl: timedelta = DEFAULT_SESSION_TTL,
        now: datetime | None = None,
    ) -> CreatedSession:
        active_now = _active_utc_second(now)
        token = secrets.token_urlsafe(32)
        token_hash = session_token_hash(token)
        session_id = str(uuid4())
        expires_at = active_now + ttl
        with self.engine.begin() as connection:
            connection.execute(
                user_sessions.insert().values(
                    id=session_id,
                    user_id=user_id,
                    token_hash=token_hash,
                    created_at=active_now,
                    expires_at=expires_at,
                    last_seen_at=active_now,
                    revoked_at=None,
                )
            )
        return CreatedSession(
            id=session_id,
            user_id=user_id,
            token=token,
            token_hash=token_hash,
            expires_at=expires_at,
        )

    def resolve_session(
        self,
        token: str,
        *,
        now: datetime | None = None,
    ) -> ResolvedSession | None:
        active_now = _active_utc_second(now)
        hashed = session_token_hash(token)
        with self.engine.begin() as connection:
            session_row = (
                connection.execute(
                    select(user_sessions).where(
                        and_(
                            user_sessions.c.token_hash == hashed,
                            user_sessions.c.revoked_at.is_(None),
                            user_sessions.c.expires_at > active_now,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if session_row is None:
                return None
            user_row = (
                connection.execute(
                    select(users).where(users.c.id == session_row["user_id"])
                )
                .mappings()
                .one_or_none()
            )
            if user_row is None or not user_row["is_active"]:
                return None
            connection.execute(
                update(user_sessions)
                .where(user_sessions.c.id == session_row["id"])
                .values(last_seen_at=active_now)
            )
        session = _session_from_mapping({**session_row, "last_seen_at": active_now})
        return ResolvedSession(session=session, user=_user_from_mapping(user_row))

    def refresh_session(
        self,
        token: str,
        *,
        ttl: timedelta = DEFAULT_SESSION_TTL,
        now: datetime | None = None,
    ) -> StoredSession | None:
        active_now = _active_utc_second(now)
        resolved = self.resolve_session(token, now=active_now)
        if resolved is None:
            return None
        expires_at = active_now + ttl
        with self.engine.begin() as connection:
            connection.execute(
                update(user_sessions)
                .where(user_sessions.c.id == resolved.session.id)
                .values(expires_at=expires_at, last_seen_at=active_now)
            )
            row = connection.execute(
                select(user_sessions).where(user_sessions.c.id == resolved.session.id)
            ).one()
        return _session_from_row(row)

    def revoke_session(
        self,
        token: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        active_now = _active_utc_second(now)
        hashed = session_token_hash(token)
        with self.engine.begin() as connection:
            result = connection.execute(
                update(user_sessions)
                .where(
                    and_(
                        user_sessions.c.token_hash == hashed,
                        user_sessions.c.revoked_at.is_(None),
                    )
                )
                .values(revoked_at=active_now, last_seen_at=active_now)
            )
        return result.rowcount > 0