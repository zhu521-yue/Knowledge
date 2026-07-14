"""create execution framework tables"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_execution_framework"
down_revision: str | None = "0001_migration_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=128), nullable=True),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("resource_url", sa.String(length=512), nullable=True),
        sa.Column("error_snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("command_name", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("operation_id", sa.String(length=36), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "command_name", "idempotency_key"),
    )
    op.create_index(
        "ix_idempotency_records_expires_at",
        "idempotency_records",
        ["expires_at"],
    )

    op.create_table(
        "worker_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_type", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_record_id", sa.String(length=36), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["idempotency_record_id"], ["idempotency_records.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_worker_jobs_status_available_at",
        "worker_jobs",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_worker_jobs_lease",
        "worker_jobs",
        ["lease_owner", "leased_until"],
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("aggregate_type", sa.String(length=128), nullable=False),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outbox_events_status_available_at",
        "outbox_events",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_outbox_events_lock",
        "outbox_events",
        ["locked_by", "locked_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_lock", table_name="outbox_events")
    op.drop_index("ix_outbox_events_status_available_at", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_worker_jobs_lease", table_name="worker_jobs")
    op.drop_index("ix_worker_jobs_status_available_at", table_name="worker_jobs")
    op.drop_table("worker_jobs")
    op.drop_index("ix_idempotency_records_expires_at", table_name="idempotency_records")
    op.drop_table("idempotency_records")
    op.drop_table("operations")
