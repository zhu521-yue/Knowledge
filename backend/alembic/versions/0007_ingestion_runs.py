"""create ingestion run state machine"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_ingestion_runs"
down_revision: str | None = "0006_topic_source_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_source_revisions_owned_revision",
        "source_revisions",
        ["id", "user_id", "source_document_id"],
    )
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("source_revision_id", sa.String(length=36), nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("checkpoint", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("chunking_version", sa.String(length=64), nullable=False),
        sa.Column("embedding_index_version", sa.String(length=64), nullable=False),
        sa.Column("sparse_index_version", sa.String(length=64), nullable=False),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("last_error", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_revision_id", "user_id", "source_document_id"],
            [
                "source_revisions.id",
                "source_revisions.user_id",
                "source_revisions.source_document_id",
            ],
            name="fk_ingestion_runs_owned_revision",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_revision_id", "id"),
        sa.UniqueConstraint("user_id", "source_revision_id", "request_key"),
    )
    op.create_index(
        "ix_ingestion_runs_user_status",
        "ingestion_runs",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_ingestion_runs_revision_created",
        "ingestion_runs",
        ["source_revision_id", "created_at"],
    )
    op.create_foreign_key(
        "fk_source_revisions_active_ingestion_run",
        "source_revisions",
        "ingestion_runs",
        ["id", "active_ingestion_run_id"],
        ["source_revision_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_source_revisions_active_ingestion_run",
        "source_revisions",
        type_="foreignkey",
    )
    op.drop_index("ix_ingestion_runs_revision_created", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_user_status", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
    op.drop_constraint(
        "uq_source_revisions_owned_revision",
        "source_revisions",
        type_="unique",
    )