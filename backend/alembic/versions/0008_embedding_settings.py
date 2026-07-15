from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_embedding_settings"
down_revision: str | None = "0007_ingestion_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "embedding_settings",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("credential_name", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("model_identifier", sa.String(length=512), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("response_format", sa.String(length=32), nullable=False),
        sa.Column("normalization", sa.String(length=32), nullable=False),
        sa.Column("distance_metric", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("embedding_settings")