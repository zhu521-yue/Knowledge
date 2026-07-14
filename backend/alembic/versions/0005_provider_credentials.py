from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_provider_credentials"
down_revision: str | None = "0004_user_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("encrypted_secret", sa.String(length=2048), nullable=False),
        sa.Column("secret_hint", sa.String(length=4), nullable=False),
        sa.Column("secret_length", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider"),
    )
    op.create_index(
        "ix_provider_credentials_user_id",
        "provider_credentials",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_provider_credentials_user_id", table_name="provider_credentials")
    op.drop_table("provider_credentials")