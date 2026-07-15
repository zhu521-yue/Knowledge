from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError

from app.infrastructure.execution_tables import outbox_events
from app.infrastructure.source_tables import (
    ingestion_runs,
    source_documents,
    source_revisions,
)

_STAGE_TRANSITIONS = {
    "parsing": ("extracting", "running", 20),
    "extracting": ("chunking", "running", 40),
    "chunking": ("embedding", "running", 60),
    "embedding": ("validating", "validating", 80),
    "validating": ("publishing", "publishing", 90),
}
_ACTIVE_STATUSES = {"queued", "running", "validating", "publishing"}
_CANCELLABLE_STATUSES = {"queued", "running", "validating"}


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


@dataclass(frozen=True)
class IngestionRun:
    id: str
    user_id: str
    source_document_id: str
    source_revision_id: str
    request_key: str
    status: str
    checkpoint: str
    progress: int
    parser_version: str
    chunking_version: str
    embedding_index_version: str
    sparse_index_version: str
    config_snapshot: dict[str, Any]
    last_error: dict[str, Any] | None
    version: int
    started_at: datetime | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class IngestionError:
    code: str


def _from_row(row: Any) -> IngestionRun:
    return IngestionRun(
        id=row.id,
        user_id=row.user_id,
        source_document_id=row.source_document_id,
        source_revision_id=row.source_revision_id,
        request_key=row.request_key,
        status=row.status,
        checkpoint=row.checkpoint,
        progress=row.progress,
        parser_version=row.parser_version,
        chunking_version=row.chunking_version,
        embedding_index_version=row.embedding_index_version,
        sparse_index_version=row.sparse_index_version,
        config_snapshot=dict(row.config_snapshot),
        last_error=dict(row.last_error) if row.last_error is not None else None,
        version=row.version,
        started_at=row.started_at,
        published_at=row.published_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _append_event(
    connection: Any,
    *,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
    now: datetime,
) -> None:
    connection.execute(
        outbox_events.insert().values(
            id=str(uuid4()),
            aggregate_type="ingestion_run",
            aggregate_id=run_id,
            event_type=event_type,
            payload=payload,
            status="pending",
            attempts=0,
            available_at=now,
            locked_by=None,
            locked_until=None,
            created_at=now,
            updated_at=now,
            published_at=None,
            last_error=None,
        )
    )


class IngestionRunService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create(
        self,
        *,
        user_id: str,
        source_revision_id: str,
        request_key: str,
        config_snapshot: dict[str, Any],
        parser_version: str = "parser-v1",
        chunking_version: str = "parent-child-v1",
        embedding_index_version: str = "dense-v1",
        sparse_index_version: str = "bm25-v1",
        now: datetime | None = None,
    ) -> IngestionRun | IngestionError:
        active_now = now or utc_now()
        normalized_key = request_key.strip()
        if not normalized_key:
            return IngestionError("request_key_required")
        run_id = str(uuid4())
        try:
            with self.engine.begin() as connection:
                revision = connection.execute(
                    select(
                        source_revisions.c.source_document_id,
                    ).where(
                        source_revisions.c.id == source_revision_id,
                        source_revisions.c.user_id == user_id,
                    )
                ).one_or_none()
                if revision is None:
                    return IngestionError("source_revision_not_found")
                connection.execute(
                    ingestion_runs.insert().values(
                        id=run_id,
                        user_id=user_id,
                        source_document_id=revision.source_document_id,
                        source_revision_id=source_revision_id,
                        request_key=normalized_key,
                        status="queued",
                        checkpoint="parsing",
                        progress=0,
                        parser_version=parser_version,
                        chunking_version=chunking_version,
                        embedding_index_version=embedding_index_version,
                        sparse_index_version=sparse_index_version,
                        config_snapshot=config_snapshot,
                        last_error=None,
                        version=1,
                        started_at=None,
                        published_at=None,
                        created_at=active_now,
                        updated_at=active_now,
                    )
                )
                _append_event(
                    connection,
                    run_id=run_id,
                    event_type="ingestion.run.queued",
                    payload={"run_id": run_id, "checkpoint": "parsing"},
                    now=active_now,
                )
                row = connection.execute(
                    select(ingestion_runs).where(ingestion_runs.c.id == run_id)
                ).one()
        except IntegrityError:
            with self.engine.connect() as connection:
                row = connection.execute(
                    select(ingestion_runs).where(
                        ingestion_runs.c.user_id == user_id,
                        ingestion_runs.c.source_revision_id == source_revision_id,
                        ingestion_runs.c.request_key == normalized_key,
                    )
                ).one_or_none()
            if row is None:
                raise
        return _from_row(row)

    def get(self, *, user_id: str, run_id: str) -> IngestionRun | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(ingestion_runs).where(
                    ingestion_runs.c.id == run_id,
                    ingestion_runs.c.user_id == user_id,
                )
            ).one_or_none()
        return _from_row(row) if row is not None else None

    def start(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int,
        now: datetime | None = None,
    ) -> IngestionRun | IngestionError:
        return self._transition(
            user_id=user_id,
            run_id=run_id,
            expected_version=expected_version,
            expected_status="queued",
            values={
                "status": "running",
                "checkpoint": "parsing",
                "progress": 5,
                "started_at": now or utc_now(),
                "last_error": None,
            },
            event_type="ingestion.stage.requested",
            now=now,
        )

    def advance(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int,
        completed_checkpoint: str,
        now: datetime | None = None,
    ) -> IngestionRun | IngestionError:
        transition = _STAGE_TRANSITIONS.get(completed_checkpoint)
        if transition is None:
            return IngestionError("ingestion_checkpoint_invalid")
        next_checkpoint, next_status, progress = transition
        expected_status = "validating" if completed_checkpoint == "validating" else "running"
        return self._transition(
            user_id=user_id,
            run_id=run_id,
            expected_version=expected_version,
            expected_status=expected_status,
            expected_checkpoint=completed_checkpoint,
            values={
                "status": next_status,
                "checkpoint": next_checkpoint,
                "progress": progress,
            },
            event_type=(
                "ingestion.publish.requested"
                if next_status == "publishing"
                else "ingestion.stage.requested"
            ),
            now=now,
        )

    def publish(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int,
        now: datetime | None = None,
    ) -> IngestionRun | IngestionError:
        active_now = now or utc_now()
        with self.engine.begin() as connection:
            run = connection.execute(
                select(ingestion_runs).where(
                    ingestion_runs.c.id == run_id,
                    ingestion_runs.c.user_id == user_id,
                )
            ).one_or_none()
            if run is None:
                return IngestionError("ingestion_run_not_found")
            if run.version != expected_version:
                return IngestionError("ingestion_version_conflict")
            if run.status != "publishing" or run.checkpoint != "publishing":
                return IngestionError("ingestion_transition_invalid")
            connection.execute(
                update(source_revisions)
                .where(
                    source_revisions.c.id == run.source_revision_id,
                    source_revisions.c.user_id == user_id,
                    source_revisions.c.source_document_id == run.source_document_id,
                )
                .values(active_ingestion_run_id=run_id)
            )
            connection.execute(
                update(source_documents)
                .where(
                    source_documents.c.id == run.source_document_id,
                    source_documents.c.user_id == user_id,
                )
                .values(
                    active_revision_id=run.source_revision_id,
                    updated_at=active_now,
                    version=source_documents.c.version + 1,
                )
            )
            connection.execute(
                update(ingestion_runs)
                .where(
                    ingestion_runs.c.id == run_id,
                    ingestion_runs.c.version == expected_version,
                )
                .values(
                    status="published",
                    progress=100,
                    version=expected_version + 1,
                    published_at=active_now,
                    updated_at=active_now,
                )
            )
            _append_event(
                connection,
                run_id=run_id,
                event_type="ingestion.run.published",
                payload={
                    "run_id": run_id,
                    "source_revision_id": run.source_revision_id,
                },
                now=active_now,
            )
            row = connection.execute(
                select(ingestion_runs).where(ingestion_runs.c.id == run_id)
            ).one()
        return _from_row(row)

    def request_cancel(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int,
        now: datetime | None = None,
    ) -> IngestionRun | IngestionError:
        current = self.get(user_id=user_id, run_id=run_id)
        if current is None:
            return IngestionError("ingestion_run_not_found")
        if current.status not in _CANCELLABLE_STATUSES:
            return IngestionError("ingestion_not_cancellable")
        return self._transition(
            user_id=user_id,
            run_id=run_id,
            expected_version=expected_version,
            expected_status=current.status,
            values={"status": "cancel_requested"},
            event_type="ingestion.compensation.requested",
            now=now,
        )

    def fail(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int,
        error: dict[str, Any],
        now: datetime | None = None,
    ) -> IngestionRun | IngestionError:
        current = self.get(user_id=user_id, run_id=run_id)
        if current is None:
            return IngestionError("ingestion_run_not_found")
        if current.status not in _ACTIVE_STATUSES:
            return IngestionError("ingestion_transition_invalid")
        return self._transition(
            user_id=user_id,
            run_id=run_id,
            expected_version=expected_version,
            expected_status=current.status,
            values={"status": "compensating", "last_error": error},
            event_type="ingestion.compensation.requested",
            now=now,
        )

    def begin_compensation(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int,
        now: datetime | None = None,
    ) -> IngestionRun | IngestionError:
        return self._transition(
            user_id=user_id,
            run_id=run_id,
            expected_version=expected_version,
            expected_status="cancel_requested",
            values={"status": "compensating"},
            event_type="ingestion.compensation.started",
            now=now,
        )

    def complete_compensation(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int,
        succeeded: bool,
        now: datetime | None = None,
    ) -> IngestionRun | IngestionError:
        current = self.get(user_id=user_id, run_id=run_id)
        if current is None:
            return IngestionError("ingestion_run_not_found")
        if current.status != "compensating":
            return IngestionError("ingestion_transition_invalid")
        final_status = (
            "failed"
            if succeeded and current.last_error is not None
            else "cancelled"
            if succeeded
            else "compensation_failed"
        )
        return self._transition(
            user_id=user_id,
            run_id=run_id,
            expected_version=expected_version,
            expected_status="compensating",
            values={"status": final_status},
            event_type=f"ingestion.run.{final_status}",
            now=now,
        )

    def retry(
        self,
        *,
        user_id: str,
        run_id: str,
        request_key: str,
        now: datetime | None = None,
    ) -> IngestionRun | IngestionError:
        current = self.get(user_id=user_id, run_id=run_id)
        if current is None:
            return IngestionError("ingestion_run_not_found")
        if current.status not in {"failed", "compensation_failed", "cancelled"}:
            return IngestionError("ingestion_retry_not_allowed")
        return self.create(
            user_id=user_id,
            source_revision_id=current.source_revision_id,
            request_key=request_key,
            config_snapshot=current.config_snapshot,
            parser_version=current.parser_version,
            chunking_version=current.chunking_version,
            embedding_index_version=current.embedding_index_version,
            sparse_index_version=current.sparse_index_version,
            now=now,
        )

    def _transition(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int,
        expected_status: str,
        values: dict[str, Any],
        event_type: str,
        expected_checkpoint: str | None = None,
        now: datetime | None = None,
    ) -> IngestionRun | IngestionError:
        active_now = now or utc_now()
        predicates = [
            ingestion_runs.c.id == run_id,
            ingestion_runs.c.user_id == user_id,
            ingestion_runs.c.version == expected_version,
            ingestion_runs.c.status == expected_status,
        ]
        if expected_checkpoint is not None:
            predicates.append(ingestion_runs.c.checkpoint == expected_checkpoint)
        with self.engine.begin() as connection:
            result = connection.execute(
                update(ingestion_runs)
                .where(*predicates)
                .values(
                    **values,
                    version=expected_version + 1,
                    updated_at=active_now,
                )
            )
            if result.rowcount == 0:
                current = connection.execute(
                    select(ingestion_runs.c.version).where(
                        ingestion_runs.c.id == run_id,
                        ingestion_runs.c.user_id == user_id,
                    )
                ).scalar_one_or_none()
                if current is None:
                    return IngestionError("ingestion_run_not_found")
                if current != expected_version:
                    return IngestionError("ingestion_version_conflict")
                return IngestionError("ingestion_transition_invalid")
            row = connection.execute(
                select(ingestion_runs).where(ingestion_runs.c.id == run_id)
            ).one()
            _append_event(
                connection,
                run_id=run_id,
                event_type=event_type,
                payload={
                    "run_id": run_id,
                    "status": row.status,
                    "checkpoint": row.checkpoint,
                    "version": row.version,
                },
                now=active_now,
            )
        return _from_row(row)