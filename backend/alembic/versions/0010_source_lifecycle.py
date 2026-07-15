"""add source lifecycle state and audit"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_source_lifecycle"
down_revision: str | None = "0009_source_import_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in (
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lifecycle_actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("lifecycle_reason", sa.String(length=512), nullable=True),
    ):
        op.add_column("source_documents", column)
    op.create_table(
        "source_lifecycle_commands",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("command", sa.String(length=32), nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "command", "request_key"),
    )
    op.create_table(
        "source_lifecycle_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("from_state", sa.String(length=32), nullable=False),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_source_lifecycle_events_source_created",
        "source_lifecycle_events",
        ["source_document_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_lifecycle_events_source_created", table_name="source_lifecycle_events")
    op.drop_table("source_lifecycle_events")
    op.drop_table("source_lifecycle_commands")
    for name in (
        "lifecycle_reason",
        "lifecycle_actor_user_id",
        "purged_at",
        "purge_after",
        "trashed_at",
        "archived_at",
    ):
        op.drop_column("source_documents", name)