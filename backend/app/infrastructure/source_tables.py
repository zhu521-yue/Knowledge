from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from app.infrastructure.identity_tables import identity_metadata


topics = Table(
    "topics",
    identity_metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("name", String(160), nullable=False),
    Column("description", Text, nullable=False),
    Column("language", String(32), nullable=False),
    Column("query_profile", JSON, nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("archived_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("user_id", "name"),
    Index("ix_topics_user_archived", "user_id", "archived_at"),
)

source_documents = Table(
    "source_documents",
    identity_metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("candidate_id", String(36), nullable=True),
    Column("input_type", String(32), nullable=False),
    Column("title", String(512), nullable=False),
    Column("state", String(32), nullable=False),
    Column("active_revision_id", String(36), nullable=True),
    Column("source_missing", Boolean, nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["id", "active_revision_id"],
        ["source_revisions.source_document_id", "source_revisions.id"],
        name="fk_source_documents_active_revision",
    ),
    Index("ix_source_documents_user_state", "user_id", "state"),
)

topic_source_documents = Table(
    "topic_source_documents",
    identity_metadata,
    Column("topic_id", String(36), ForeignKey("topics.id"), primary_key=True),
    Column(
        "source_document_id",
        String(36),
        ForeignKey("source_documents.id"),
        primary_key=True,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_topic_source_documents_source", "source_document_id"),
)

source_import_requests = Table(
    "source_import_requests",
    identity_metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("request_key", String(128), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("source_document_id", String(36), ForeignKey("source_documents.id"), nullable=False),
    Column("source_revision_id", String(36), nullable=False),
    Column("ingestion_run_id", String(36), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "request_key"),
    ForeignKeyConstraint(
        ["source_document_id", "source_revision_id"],
        ["source_revisions.source_document_id", "source_revisions.id"],
        name="fk_source_import_requests_revision",
    ),
    ForeignKeyConstraint(
        ["source_revision_id", "ingestion_run_id"],
        ["ingestion_runs.source_revision_id", "ingestion_runs.id"],
        name="fk_source_import_requests_run",
    ),
)

content_blobs = Table(
    "content_blobs",
    identity_metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("storage_path", String(1024), nullable=False),
    Column("byte_size", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "content_hash"),
)

source_revisions = Table(
    "source_revisions",
    identity_metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column(
        "source_document_id",
        String(36),
        ForeignKey("source_documents.id"),
        nullable=False,
    ),
    Column("content_blob_id", String(36), ForeignKey("content_blobs.id"), nullable=False),
    Column("original_url", String(2048), nullable=True),
    Column("mime_type", String(255), nullable=True),
    Column("page_count", Integer, nullable=True),
    Column("content_hash", String(64), nullable=False),
    Column("sha256", String(64), nullable=True),
    Column("active_ingestion_run_id", String(36), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["id", "active_ingestion_run_id"],
        ["ingestion_runs.source_revision_id", "ingestion_runs.id"],
        name="fk_source_revisions_active_ingestion_run",
    ),
    UniqueConstraint("source_document_id", "id"),
    UniqueConstraint("id", "user_id", "source_document_id"),
    Index("ix_source_revisions_document_created", "source_document_id", "created_at"),
    Index("ix_source_revisions_user_hash", "user_id", "content_hash"),
)

ingestion_runs = Table(
    "ingestion_runs",
    identity_metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("source_document_id", String(36), nullable=False),
    Column("source_revision_id", String(36), nullable=False),
    Column("request_key", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("checkpoint", String(32), nullable=False),
    Column("progress", Integer, nullable=False),
    Column("parser_version", String(64), nullable=False),
    Column("chunking_version", String(64), nullable=False),
    Column("embedding_index_version", String(64), nullable=False),
    Column("sparse_index_version", String(64), nullable=False),
    Column("config_snapshot", JSON, nullable=False),
    Column("last_error", JSON, nullable=True),
    Column("version", Integer, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["source_revision_id", "user_id", "source_document_id"],
        [
            "source_revisions.id",
            "source_revisions.user_id",
            "source_revisions.source_document_id",
        ],
        name="fk_ingestion_runs_owned_revision",
    ),
    UniqueConstraint("source_revision_id", "id"),
    UniqueConstraint("user_id", "source_revision_id", "request_key"),
    Index("ix_ingestion_runs_user_status", "user_id", "status"),
    Index("ix_ingestion_runs_revision_created", "source_revision_id", "created_at"),
)
