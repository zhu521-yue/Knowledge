from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, select

from app.infrastructure.source_tables import (
    ingestion_runs,
    source_documents,
    source_revisions,
    topic_source_documents,
    topics,
)
from app.retrieval_scope import AuthorizedRetrievalScope


@dataclass(frozen=True, slots=True)
class RetrievalIndexVersions:
    dense: str
    sparse: str
    chunking: str


@dataclass(frozen=True, slots=True)
class ResolvedRetrievalScope:
    scope: AuthorizedRetrievalScope
    versions: RetrievalIndexVersions | None


class RetrievalScopeResolver:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def resolve(self, *, user_id: str, topic_id: str) -> ResolvedRetrievalScope | None:
        with self._engine.connect() as connection:
            owned_topic = connection.execute(
                select(topics.c.id).where(
                    topics.c.id == topic_id,
                    topics.c.user_id == user_id,
                    topics.c.archived_at.is_(None),
                )
            ).scalar_one_or_none()
            if owned_topic is None:
                return None
            rows = connection.execute(
                select(
                    source_revisions.c.active_ingestion_run_id,
                    ingestion_runs.c.embedding_index_version,
                    ingestion_runs.c.sparse_index_version,
                    ingestion_runs.c.chunking_version,
                )
                .select_from(
                    topic_source_documents.join(
                        source_documents,
                        topic_source_documents.c.source_document_id
                        == source_documents.c.id,
                    )
                    .join(
                        source_revisions,
                        source_documents.c.active_revision_id == source_revisions.c.id,
                    )
                    .join(
                        ingestion_runs,
                        source_revisions.c.active_ingestion_run_id == ingestion_runs.c.id,
                    )
                )
                .where(
                    topic_source_documents.c.topic_id == topic_id,
                    source_documents.c.user_id == user_id,
                    source_documents.c.state == "active",
                    source_documents.c.source_missing.is_(False),
                    ingestion_runs.c.user_id == user_id,
                    ingestion_runs.c.status == "published",
                )
            ).all()

        run_ids = frozenset(
            row.active_ingestion_run_id
            for row in rows
            if row.active_ingestion_run_id is not None
        )
        version_pairs = {
            (
                row.embedding_index_version,
                row.sparse_index_version,
                row.chunking_version,
            )
            for row in rows
        }
        if len(version_pairs) > 1:
            raise ValueError("topic active runs use incompatible index versions")
        versions = (
            RetrievalIndexVersions(*next(iter(version_pairs)))
            if version_pairs
            else None
        )
        return ResolvedRetrievalScope(
            scope=AuthorizedRetrievalScope(user_id, topic_id, run_ids),
            versions=versions,
        )