"""create topic and source domain tables"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_topic_source_domain"
down_revision: str | None = "0005_provider_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "topics",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("query_profile", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name"),
    )
    op.create_index("ix_topics_user_archived", "topics", ["user_id", "archived_at"])

    op.create_table(
        "source_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=True),
        sa.Column("input_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("active_revision_id", sa.String(length=36), nullable=True),
        sa.Column("source_missing", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_source_documents_user_state",
        "source_documents",
        ["user_id", "state"],
    )

    op.create_table(
        "topic_source_documents",
        sa.Column("topic_id", sa.String(length=36), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"]),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"]),
        sa.PrimaryKeyConstraint("topic_id", "source_document_id"),
    )
    op.create_index(
        "ix_topic_source_documents_source",
        "topic_source_documents",
        ["source_document_id"],
    )

    op.create_table(
        "content_blobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "content_hash"),
    )

    op.create_table(
        "source_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("content_blob_id", sa.String(length=36), nullable=False),
        sa.Column("original_url", sa.String(length=2048), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("active_ingestion_run_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["content_blob_id"], ["content_blobs.id"]),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_document_id", "id"),
    )
    op.create_index(
        "ix_source_revisions_document_created",
        "source_revisions",
        ["source_document_id", "created_at"],
    )
    op.create_index(
        "ix_source_revisions_user_hash",
        "source_revisions",
        ["user_id", "content_hash"],
    )
    op.create_foreign_key(
        "fk_source_documents_active_revision",
        "source_documents",
        "source_revisions",
        ["id", "active_revision_id"],
        ["source_document_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_source_documents_active_revision",
        "source_documents",
        type_="foreignkey",
    )
    op.drop_index("ix_source_revisions_user_hash", table_name="source_revisions")
    op.drop_index("ix_source_revisions_document_created", table_name="source_revisions")
    op.drop_table("source_revisions")
    op.drop_table("content_blobs")
    op.drop_index(
        "ix_topic_source_documents_source",
        table_name="topic_source_documents",
    )
    op.drop_table("topic_source_documents")
    op.drop_index("ix_source_documents_user_state", table_name="source_documents")
    op.drop_table("source_documents")
    op.drop_index("ix_topics_user_archived", table_name="topics")
    op.drop_table("topics")