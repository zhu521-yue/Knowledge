from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import IntegrityError

from app.infrastructure.identity_tables import invitation_codes, users

PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 200_000


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _active_utc_second(value: datetime | None) -> datetime:
    active = value or utc_now()
    if active.tzinfo is None:
        raise ValueError("identity timestamps must include a timezone")
    return active.astimezone(UTC).replace(microsecond=0)


def _stored_utc_second(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).replace(microsecond=0)
    return value.astimezone(UTC).replace(microsecond=0)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _normalize_code(code: str) -> str:
    return code.strip()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    ).hex()
    return f"{PASSWORD_HASH_ALGORITHM}${PASSWORD_HASH_ITERATIONS}${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt, expected = password_hash.split("$", 3)
        iterations = int(iterations_text)
    except ValueError:
        return False
    if algorithm != PASSWORD_HASH_ALGORITHM:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return hmac.compare_digest(actual, expected)


@dataclass(frozen=True)
class IdentityError:
    error: str


@dataclass(frozen=True)
class IdentityUser:
    id: str
    email: str
    display_name: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class InvitationCode:
    id: str
    code: str
    created_by_user_id: str
    max_uses: int
    uses_count: int
    is_active: bool
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _user_from_row(row: Any) -> IdentityUser:
    return IdentityUser(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        role=row.role,
        is_active=bool(row.is_active),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _invitation_from_row(row: Any) -> InvitationCode:
    return InvitationCode(
        id=row.id,
        code=row.code,
        created_by_user_id=row.created_by_user_id,
        max_uses=row.max_uses,
        uses_count=row.uses_count,
        is_active=bool(row.is_active),
        expires_at=row.expires_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class IdentityService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def bootstrap_admin(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
        now: datetime | None = None,
    ) -> IdentityUser | IdentityError:
        active_now = _active_utc_second(now)
        normalized_email = _normalize_email(email)
        with self.engine.begin() as connection:
            user_count = connection.execute(select(func.count()).select_from(users)).scalar_one()
            if user_count > 0:
                return IdentityError("admin_already_initialized")

            user_id = str(uuid4())
            try:
                connection.execute(
                    users.insert().values(
                        id=user_id,
                        email=normalized_email,
                        display_name=display_name.strip(),
                        password_hash=hash_password(password),
                        role="admin",
                        is_active=True,
                        created_at=active_now,
                        updated_at=active_now,
                        disabled_at=None,
                        last_login_at=None,
                    )
                )
            except IntegrityError:
                return IdentityError("email_already_registered")
            row = connection.execute(select(users).where(users.c.id == user_id)).one()
        return _user_from_row(row)

    def create_invitation(
        self,
        *,
        actor_user_id: str,
        code: str | None = None,
        max_uses: int = 1,
        expires_at: datetime | None = None,
        now: datetime | None = None,
    ) -> InvitationCode | IdentityError:
        active_now = _active_utc_second(now)
        invitation_code = _normalize_code(code or secrets.token_urlsafe(12))
        if max_uses < 1:
            return IdentityError("invalid_invitation_limit")

        with self.engine.begin() as connection:
            actor = (
                connection.execute(select(users).where(users.c.id == actor_user_id))
                .mappings()
                .one_or_none()
            )
            if actor is None or actor["role"] != "admin" or not actor["is_active"]:
                return IdentityError("admin_required")

            invitation_id = str(uuid4())
            try:
                connection.execute(
                    invitation_codes.insert().values(
                        id=invitation_id,
                        code=invitation_code,
                        created_by_user_id=actor_user_id,
                        max_uses=max_uses,
                        uses_count=0,
                        is_active=True,
                        expires_at=(
                            _active_utc_second(expires_at)
                            if expires_at is not None
                            else None
                        ),
                        created_at=active_now,
                        updated_at=active_now,
                        disabled_at=None,
                    )
                )
            except IntegrityError:
                return IdentityError("invitation_code_taken")
            row = connection.execute(
                select(invitation_codes).where(invitation_codes.c.id == invitation_id)
            ).one()
        return _invitation_from_row(row)

    def register_with_invitation(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
        invitation_code: str,
        now: datetime | None = None,
    ) -> IdentityUser | IdentityError:
        active_now = _active_utc_second(now)
        normalized_email = _normalize_email(email)
        normalized_code = _normalize_code(invitation_code)
        with self.engine.begin() as connection:
            invitation = (
                connection.execute(
                    select(invitation_codes).where(invitation_codes.c.code == normalized_code)
                )
                .mappings()
                .one_or_none()
            )
            if invitation is None:
                return IdentityError("invitation_not_found")
            if not invitation["is_active"]:
                return IdentityError("invitation_disabled")
            expires_at = invitation["expires_at"]
            if expires_at is not None and _stored_utc_second(expires_at) <= active_now:
                return IdentityError("invitation_expired")
            if invitation["uses_count"] >= invitation["max_uses"]:
                return IdentityError("invitation_exhausted")

            user_id = str(uuid4())
            try:
                connection.execute(
                    users.insert().values(
                        id=user_id,
                        email=normalized_email,
                        display_name=display_name.strip(),
                        password_hash=hash_password(password),
                        role="member",
                        is_active=True,
                        created_at=active_now,
                        updated_at=active_now,
                        disabled_at=None,
                        last_login_at=None,
                    )
                )
            except IntegrityError:
                return IdentityError("email_already_registered")
            connection.execute(
                update(invitation_codes)
                .where(invitation_codes.c.id == invitation["id"])
                .values(
                    uses_count=invitation["uses_count"] + 1,
                    updated_at=active_now,
                )
            )
            row = connection.execute(select(users).where(users.c.id == user_id)).one()
        return _user_from_row(row)

    def authenticate(
        self,
        *,
        email: str,
        password: str,
        now: datetime | None = None,
    ) -> IdentityUser | IdentityError:
        active_now = _active_utc_second(now)
        normalized_email = _normalize_email(email)
        with self.engine.begin() as connection:
            row = (
                connection.execute(select(users).where(users.c.email == normalized_email))
                .mappings()
                .one_or_none()
            )
            if row is None or not verify_password(password, row["password_hash"]):
                return IdentityError("invalid_credentials")
            if not row["is_active"]:
                return IdentityError("user_disabled")
            connection.execute(
                update(users)
                .where(users.c.id == row["id"])
                .values(last_login_at=active_now, updated_at=active_now)
            )
            updated = connection.execute(select(users).where(users.c.id == row["id"])).one()
        return _user_from_row(updated)

    def set_user_active(
        self,
        *,
        actor_user_id: str,
        target_user_id: str,
        is_active: bool,
        now: datetime | None = None,
    ) -> IdentityUser | IdentityError:
        active_now = _active_utc_second(now)
        with self.engine.begin() as connection:
            actor = (
                connection.execute(select(users).where(users.c.id == actor_user_id))
                .mappings()
                .one_or_none()
            )
            if actor is None or actor["role"] != "admin" or not actor["is_active"]:
                return IdentityError("admin_required")
            target = (
                connection.execute(select(users).where(users.c.id == target_user_id))
                .mappings()
                .one_or_none()
            )
            if target is None:
                return IdentityError("user_not_found")
            connection.execute(
                update(users)
                .where(users.c.id == target_user_id)
                .values(
                    is_active=is_active,
                    disabled_at=None if is_active else active_now,
                    updated_at=active_now,
                )
            )
            updated = connection.execute(
                select(users).where(users.c.id == target_user_id)
            ).one()
        return _user_from_row(updated)