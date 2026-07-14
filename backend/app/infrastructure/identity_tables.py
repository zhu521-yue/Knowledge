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