from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

identity_metadata = MetaData(naming_convention=naming_convention)

users = Table(
    "users",
    identity_metadata,
    Column("id", String(36), primary_key=True),
    Column("email", String(320), nullable=False),
    Column("display_name", String(128), nullable=False),
    Column("password_hash", String(256), nullable=False),
    Column("role", String(32), nullable=False),
    Column("is_active", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("disabled_at", DateTime(timezone=True), nullable=True),
    Column("last_login_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("email"),
    Index("ix_users_role", "role"),
)

user_sessions = Table(
    "user_sessions",
    identity_metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("token_hash", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("token_hash"),
    Index("ix_user_sessions_user_id", "user_id"),
    Index("ix_user_sessions_expires_at", "expires_at"),
)

provider_credentials = Table(
    "provider_credentials",
    identity_metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("provider", String(64), nullable=False),
    Column("encrypted_secret", String(2048), nullable=False),
    Column("secret_hint", String(4), nullable=False),
    Column("secret_length", Integer, nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "provider"),
    Index("ix_provider_credentials_user_id", "user_id"),
)


invitation_codes = Table(
    "invitation_codes",
    identity_metadata,
    Column("id", String(36), primary_key=True),
    Column("code", String(128), nullable=False),
    Column("created_by_user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("max_uses", Integer, nullable=False),
    Column("uses_count", Integer, nullable=False),
    Column("is_active", Boolean, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("disabled_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("code"),
    Index("ix_invitation_codes_active", "is_active"),
)