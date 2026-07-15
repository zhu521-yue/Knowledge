"""add idempotent source import requests"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_source_import_requests"
down_revision: str | None = "0008_embedding_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_import_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("source_revision_id", sa.String(length=36), nullable=False),
        sa.Column("ingestion_run_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["source_documents.id"]
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id", "source_revision_id"],
            ["source_revisions.source_document_id", "source_revisions.id"],
            name="fk_source_import_requests_revision",
        ),
        sa.ForeignKeyConstraint(
            ["source_revision_id", "ingestion_run_id"],
            ["ingestion_runs.source_revision_id", "ingestion_runs.id"],
            name="fk_source_import_requests_run",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "request_key"),
    )


def downgrade() -> None:
    op.drop_table("source_import_requests")