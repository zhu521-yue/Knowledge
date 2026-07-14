from sqlalchemy import (
    JSON,
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

execution_metadata = MetaData(naming_convention=naming_convention)

operations = Table(
    "operations",
    execution_metadata,
    Column("id", String(36), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("resource_type", String(128), nullable=True),
    Column("resource_id", String(128), nullable=True),
    Column("resource_url", String(512), nullable=True),
    Column("error_snapshot", JSON, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

idempotency_records = Table(
    "idempotency_records",
    execution_metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("command_name", String(128), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("operation_id", String(36), ForeignKey("operations.id"), nullable=True),
    Column("response_status", Integer, nullable=True),
    Column("response_body", JSON, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "command_name", "idempotency_key"),
    Index("ix_idempotency_records_expires_at", "expires_at"),
)

worker_jobs = Table(
    "worker_jobs",
    execution_metadata,
    Column("id", String(36), primary_key=True),
    Column("job_type", String(128), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("status", String(32), nullable=False),
    Column("priority", Integer, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("lease_owner", String(128), nullable=True),
    Column("leased_until", DateTime(timezone=True), nullable=True),
    Column(
        "idempotency_record_id",
        String(36),
        ForeignKey("idempotency_records.id"),
        nullable=True,
    ),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("last_error", JSON, nullable=True),
    Index("ix_worker_jobs_status_available_at", "status", "available_at"),
    Index("ix_worker_jobs_lease", "lease_owner", "leased_until"),
)

outbox_events = Table(
    "outbox_events",
    execution_metadata,
    Column("id", String(36), primary_key=True),
    Column("aggregate_type", String(128), nullable=False),
    Column("aggregate_id", String(128), nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("status", String(32), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("locked_by", String(128), nullable=True),
    Column("locked_until", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("last_error", JSON, nullable=True),
    Index("ix_outbox_events_status_available_at", "status", "available_at"),
    Index("ix_outbox_events_lock", "locked_by", "locked_until"),
)
